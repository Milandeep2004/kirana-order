"""Scheme engine: pure functions, no database access.

All money is integer paise. Assumptions (also listed in the TDD):
  * "Buy N get M free" gives M free cases per FULL set of N cases bought (25 cases at 10+1 -> 2 free).
  * Free cases come out of stock, so cases + free_cases must be <= stock.
  * A slab discount ("3% off 5+ cases") applies to that SKU's line only, on the bought cases.
  * A SKU may carry both a free-goods scheme and a slab; both then apply.
  * Duplicate lines for the same SKU are merged before schemes are evaluated.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SkuInfo:
    code: str
    name: str
    case_price_paise: int  # already adjusted for the retailer's price tier
    stock_cases: int


@dataclass(frozen=True)
class SchemeInfo:
    type: str  # "free_goods" | "slab"
    buy_qty: int = 0
    free_qty: int = 0
    min_cases: int = 0
    discount_bp: int = 0  # basis points: 300 = 3%


@dataclass(frozen=True)
class CartLine:
    sku_code: str
    cases: int


@dataclass
class Issue:
    code: str  # out_of_stock | insufficient_stock | unknown_sku | invalid_qty
    message: str
    max_orderable: int | None = None


@dataclass
class PricedLine:
    sku_code: str
    name: str
    cases: int
    free_cases: int
    case_price_paise: int
    gross_paise: int
    discount_paise: int
    net_paise: int
    notes: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return bool(self.issues)


@dataclass
class PricedCart:
    lines: list[PricedLine]

    @property
    def total_paise(self) -> int:
        return sum(l.net_paise for l in self.lines)

    @property
    def placeable(self) -> bool:
        return bool(self.lines) and not any(l.blocking for l in self.lines)


def free_cases_for(cases: int, scheme: SchemeInfo) -> int:
    if scheme.type != "free_goods" or scheme.buy_qty <= 0:
        return 0
    return (cases // scheme.buy_qty) * scheme.free_qty


def max_orderable(stock: int, schemes: list[SchemeInfo]) -> int:
    """Largest number of bought cases such that bought + free still fits in stock."""
    for c in range(max(stock, 0), -1, -1):
        free = sum(free_cases_for(c, s) for s in schemes)
        if c + free <= stock:
            return c
    return 0


def _percent(amount_paise: int, bp: int) -> int:
    """amount * bp/10000, rounded half-up to a whole paisa."""
    return (amount_paise * bp + 5000) // 10000


def price_cart(
    lines: list[CartLine],
    skus: dict[str, SkuInfo],
    schemes: dict[str, list[SchemeInfo]],
) -> PricedCart:
    merged: dict[str, int] = {}
    order: list[str] = []
    invalid: list[PricedLine] = []
    for l in lines:
        if l.cases <= 0:
            invalid.append(
                PricedLine(l.sku_code, l.sku_code, 0, 0, 0, 0, 0, 0,
                           issues=[Issue("invalid_qty", "Quantity must be at least 1 case.")])
            )
            continue
        if l.sku_code not in merged:
            order.append(l.sku_code)
        merged[l.sku_code] = merged.get(l.sku_code, 0) + l.cases

    out: list[PricedLine] = []
    for code in order:
        cases = merged[code]
        sku = skus.get(code)
        if sku is None:
            out.append(PricedLine(code, code, cases, 0, 0, 0, 0, 0,
                                  issues=[Issue("unknown_sku", f"{code} is not in the catalogue.")]))
            continue
        sk_schemes = schemes.get(code, [])
        free = sum(free_cases_for(cases, s) for s in sk_schemes)
        gross = cases * sku.case_price_paise
        discount = 0
        notes: list[str] = []
        for s in sk_schemes:
            if s.type == "free_goods" and free_cases_for(cases, s):
                n = free_cases_for(cases, s)
                notes.append(f"Buy {s.buy_qty} get {s.free_qty} free: {n} free")
            elif s.type == "slab" and cases >= s.min_cases:
                d = _percent(gross, s.discount_bp)
                discount += d
                notes.append(f"{s.discount_bp / 100:g}% off for {s.min_cases}+ cases")
        issues: list[Issue] = []
        if sku.stock_cases <= 0:
            issues.append(Issue("out_of_stock", f"{sku.name} is out of stock.", 0))
        elif cases + free > sku.stock_cases:
            m = max_orderable(sku.stock_cases, sk_schemes)
            issues.append(Issue(
                "insufficient_stock",
                f"Only {sku.stock_cases} cases in stock (including free cases). You can order up to {m}.",
                m,
            ))
        out.append(PricedLine(code, sku.name, cases, free, sku.case_price_paise, gross, discount,
                              gross - discount, notes, issues))
    return PricedCart(out + invalid)


def check_credit(limit_paise: int, outstanding_paise: int, order_paise: int) -> str | None:
    """Return a hold reason if this order would take the retailer over their credit limit."""
    exposure = outstanding_paise + order_paise
    if exposure > limit_paise:
        over = exposure - limit_paise
        return f"Credit limit exceeded by ₹{over / 100:,.2f}"
    return None


def adjust_price(case_price_paise: int, price_discount_bp: int) -> int:
    """Apply a retailer's price tier (basis points off dealer price)."""
    return case_price_paise - _percent(case_price_paise, price_discount_bp)


def rupees(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"
