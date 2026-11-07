"""Sample data. The three SKUs and the three retailers' numbers come from / extend the client brief;
everything marked ASSUMED is invented to make the demo realistic and is listed in the TDD."""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Alias, Order, OrderLine, Retailer, Scheme, Sku, User, now
from .pricing import CartLine, SchemeInfo, SkuInfo, adjust_price, price_cart

NAME_HI = {
    'SKU-BS-075': 'ग्लूकोज़ बिस्कुट 75 ग्राम, 96 की पेटी',
    'SKU-DT-1KG': 'डिटर्जेंट पाउडर 1 किग्रा, 20 की पेटी',
    'SKU-TE-250': 'चाय पत्ती 250 ग्राम, 40 की पेटी',
    'SKU-BS-200': 'ग्लूकोज़ बिस्कुट 200 ग्राम, 36 की पेटी',
    'SKU-DT-500': 'डिटर्जेंट पाउडर 500 ग्राम, 40 की पेटी',
    'SKU-TE-100': 'चाय पत्ती 100 ग्राम, 100 की पेटी',
    'SKU-SP-100': 'नहाने का साबुन 100 ग्राम, 72 की पेटी',
    'SKU-DW-500': 'बर्तन धोने का लिक्विड 500 मिली, 24 की पेटी',
}

# code, name, category, pieces/case, piece price (paise), stock, keywords, colour
SKUS = [
    # --- from the brief ---
    ("SKU-BS-075", "Glucose biscuit 75 g, case of 96", "Biscuits", 96, 810, 340,
     "biscuit,biskut,glucose,chhota,small,75", "#f4e3c1"),
    ("SKU-DT-1KG", "Detergent powder 1 kg, case of 20", "Home care", 20, 10150, 85,
     "detergent,washing powder,kapde ka powder,powder,1 kg,1 kilo", "#d6e6f5"),
    ("SKU-TE-250", "Tea leaf 250 g, case of 40", "Tea", 40, 11800, 0,
     "tea,chai,chai patti,patti,250", "#e3d3c2"),
    # --- ASSUMED, to make the catalogue realistic ---
    ("SKU-BS-200", "Glucose biscuit 200 g, case of 36", "Biscuits", 36, 2400, 120,
     "biscuit,biskut,glucose,bada,big,200", "#f1d9a6"),
    ("SKU-DT-500", "Detergent powder 500 g, case of 40", "Home care", 40, 5400, 60,
     "detergent,washing powder,kapde ka powder,powder,500,aadha kilo", "#c9dcef"),
    ("SKU-TE-100", "Tea leaf 100 g, case of 100", "Tea", 100, 4800, 45,
     "tea,chai,chai patti,patti,100", "#d9c5b0"),
    ("SKU-SP-100", "Bathing soap 100 g, case of 72", "Home care", 72, 3800, 200,
     "soap,sabun,nahane ka sabun,bathing,100", "#dceee0"),
    ("SKU-DW-500", "Dishwash liquid 500 ml, case of 24", "Home care", 24, 9500, 30,
     "dishwash,bartan,liquid,500 ml", "#e1e4f6"),
]

# sku, type, buy, free, min_cases, bp
SCHEMES = [
    ("SKU-BS-075", "free_goods", 10, 1, 0, 0),   # brief: buy 10 cases get 1 free
    ("SKU-DT-1KG", "slab", 0, 0, 5, 300),        # brief: 3% off 5 or more cases
    ("SKU-BS-200", "free_goods", 5, 1, 0, 0),    # ASSUMED
    ("SKU-DT-500", "slab", 0, 0, 10, 200),       # ASSUMED
    ("SKU-SP-100", "free_goods", 8, 1, 0, 0),    # ASSUMED
]

# phone, role, name
USERS = [
    ("9000000001", "retailer", "Mahesh Sharma"),
    ("9000000002", "retailer", "Pooja Gupta"),
    ("9000000003", "retailer", "Rakesh Verma"),
    ("9000000004", "retailer", "Mohan Agrawal"),
    ("9000000005", "retailer", "Sunita Tiwari"),
    ("9000000100", "desk", "Anita (Order desk)"),
]

RUPEE = 100
# phone, shop, salesman, credit limit, outstanding, price discount bp  (all ASSUMED)
RETAILERS = [
    ("9000000001", "Sharma General Store", "Ramesh", 200000 * RUPEE, 35000 * RUPEE, 0),
    ("9000000002", "Gupta Kirana", "Suresh", 50000 * RUPEE, 42000 * RUPEE, 100),  # tight credit: demos holds
    ("9000000003", "Verma Super Mart", "Ramesh", 300000 * RUPEE, 0, 150),
    ("9000000004", "Agrawal Provision", "Suresh", 80000 * RUPEE, 10000 * RUPEE, 100),  # last order 22 days ago: inactive list
    ("9000000005", "Tiwari Stores", "Ramesh", 60000 * RUPEE, 0, 100),  # never ordered: inactive list
]


def load_pricing(db: Session, retailer: Retailer | None = None):
    skus = {}
    for s in db.scalars(select(Sku)):
        cp = s.case_price_paise
        if retailer:
            cp = adjust_price(cp, retailer.price_discount_bp)
        skus[s.code] = SkuInfo(s.code, s.name, cp, s.stock_cases)
    schemes: dict[str, list[SchemeInfo]] = {}
    for sc in db.scalars(select(Scheme)):
        schemes.setdefault(sc.sku_code, []).append(
            SchemeInfo(sc.type, sc.buy_qty, sc.free_qty, sc.min_cases, sc.discount_bp)
        )
    return skus, schemes


def seed(db: Session) -> bool:
    """Idempotent: does nothing if data already exists. Returns True if it seeded."""
    if db.scalar(select(User.id).limit(1)):
        return False
    for code, name, cat, ppc, pp, stock, kw, color in SKUS:
        db.add(Sku(code=code, name=name, name_hi=NAME_HI.get(code, ""), category=cat, pieces_per_case=ppc, piece_price_paise=pp,
                   stock_cases=stock, keywords=kw, color=color))
    for sku, typ, buy, free, mn, bp in SCHEMES:
        db.add(Scheme(sku_code=sku, type=typ, buy_qty=buy, free_qty=free, min_cases=mn, discount_bp=bp))
    users = {}
    for phone, role, name in USERS:
        u = User(phone=phone, role=role, name=name)
        db.add(u)
        users[phone] = u
    db.flush()
    retailers = {}
    for phone, shop, sm, limit, out, bp in RETAILERS:
        r = Retailer(user_id=users[phone].id, shop_name=shop, salesman_name=sm,
                     credit_limit_paise=limit, outstanding_paise=out, price_discount_bp=bp)
        db.add(r)
        retailers[phone] = r
    db.flush()

    # Sharma knows how he writes "biscuit chhota"; Gupta has no aliases yet, so the AI is less sure for him.
    sharma = retailers["9000000001"]
    db.add(Alias(retailer_id=sharma.id, phrase="biscuit chhota", sku_code="SKU-BS-075"))
    db.add(Alias(retailer_id=sharma.id, phrase="chai patti 250 wala", sku_code="SKU-TE-250"))

    # A little history so "repeat order" and the desk queue are not empty.
    skus, schemes = load_pricing(db)
    history = [
        (sharma, "exported", 12, [("SKU-BS-075", 10), ("SKU-DT-1KG", 5), ("SKU-TE-250", 2)]),
        (sharma, "exported", 5, [("SKU-BS-075", 6), ("SKU-SP-100", 8)]),
        (retailers["9000000002"], "confirmed", 1, [("SKU-DT-500", 10), ("SKU-DW-500", 2)]),
        (retailers["9000000003"], "placed", 0, [("SKU-BS-200", 5), ("SKU-TE-100", 3)]),
        (retailers["9000000004"], "exported", 22, [("SKU-BS-075", 4), ("SKU-DT-500", 3)]),
    ]
    for ret, status, days_ago, items in history:
        priced = price_cart([CartLine(c, q) for c, q in items], *load_pricing(db, ret))
        o = Order(retailer_id=ret.id, status=status, source="app", total_paise=priced.total_paise,
                  created_at=now() - timedelta(days=days_ago, hours=1))
        for l in priced.lines:
            o.lines.append(OrderLine(sku_code=l.sku_code, cases=l.cases, free_cases=l.free_cases,
                                     gross_paise=l.gross_paise, discount_paise=l.discount_paise,
                                     net_paise=l.net_paise))
        db.add(o)
    db.commit()
    return True
