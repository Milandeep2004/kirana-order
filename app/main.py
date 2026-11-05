from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import auth
from .db import Base, SessionLocal, engine, get_db
from .models import Retailer, Scheme, Sku, User
from .pricing import CartLine, adjust_price, price_cart
from .seed import load_pricing, seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    yield


app = FastAPI(title="Kirana Order", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- auth ----------
class OtpRequest(BaseModel):
    phone: str


class LoginRequest(BaseModel):
    phone: str
    otp: str = Field(min_length=4, max_length=8)


@app.post("/api/auth/request-otp")
def request_otp(body: OtpRequest, db: Session = Depends(get_db)):
    if not auth.valid_phone(body.phone):
        raise HTTPException(422, "Enter a valid 10-digit mobile number.")
    # Same answer whether or not the number is registered, so numbers cannot be probed.
    return {"sent": True}


@app.post("/api/auth/login")
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    if not auth.valid_phone(body.phone):
        raise HTTPException(422, "Enter a valid 10-digit mobile number.")
    if auth.too_many_failures(body.phone):
        raise HTTPException(429, "Too many wrong codes. Try again in a few minutes.")
    user = db.scalar(select(User).where(User.phone == body.phone))
    if not user or not auth.check_otp(body.phone, body.otp):
        auth.record_failure(body.phone)
        raise HTTPException(401, "Wrong phone number or code.")
    response.set_cookie(auth.COOKIE, auth.make_session(user.id), max_age=auth.SESSION_SECONDS,
                        httponly=True, samesite="lax")
    return {"role": user.role, "name": user.name}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    out = {"role": user.role, "name": user.name}
    if user.role == "retailer":
        r = db.scalar(select(Retailer).where(Retailer.user_id == user.id))
        out["shop"] = r.shop_name if r else None
    return out


# ---------- catalogue (retailer-specific prices; visible only to that retailer) ----------
def _scheme_text(s: Scheme) -> str:
    if s.type == "free_goods":
        return f"Buy {s.buy_qty} cases, get {s.free_qty} free"
    return f"{s.discount_bp / 100:g}% off on {s.min_cases}+ cases"


@app.get("/api/catalogue")
def catalogue(retailer: Retailer = Depends(auth.current_retailer), db: Session = Depends(get_db)):
    schemes: dict[str, list[str]] = {}
    for sc in db.scalars(select(Scheme)):
        schemes.setdefault(sc.sku_code, []).append(_scheme_text(sc))
    items = []
    for s in db.scalars(select(Sku).order_by(Sku.category, Sku.name)):
        case_price = adjust_price(s.case_price_paise, retailer.price_discount_bp)
        stock = "out" if s.stock_cases <= 0 else "low" if s.stock_cases < 50 else "in"
        items.append({
            "code": s.code, "name": s.name, "category": s.category, "color": s.color,
            "pieces_per_case": s.pieces_per_case,
            "piece_price_paise": adjust_price(s.piece_price_paise, retailer.price_discount_bp),
            "case_price_paise": case_price, "stock_status": stock,
            "schemes": schemes.get(s.code, []),
        })
    return {"gst_note": "Prices exclude GST", "items": items}


# ---------- cart pricing ----------
class CartItem(BaseModel):
    sku_code: str = Field(max_length=24)
    cases: int = Field(ge=1, le=10000)


class CartRequest(BaseModel):
    lines: list[CartItem] = Field(max_length=100)


def _price(db: Session, retailer: Retailer, items: list[CartItem]):
    skus, schemes = load_pricing(db, retailer)
    return price_cart([CartLine(i.sku_code, i.cases) for i in items], skus, schemes)


def _cart_json(priced):
    return {
        "lines": [{
            "sku_code": l.sku_code, "name": l.name, "cases": l.cases, "free_cases": l.free_cases,
            "case_price_paise": l.case_price_paise, "gross_paise": l.gross_paise,
            "discount_paise": l.discount_paise, "net_paise": l.net_paise, "notes": l.notes,
            "issues": [{"code": i.code, "message": i.message, "max_orderable": i.max_orderable} for i in l.issues],
        } for l in priced.lines],
        "total_paise": priced.total_paise, "placeable": priced.placeable, "gst_note": "Prices exclude GST",
    }


@app.post("/api/cart/price")
def cart_price(body: CartRequest, retailer: Retailer = Depends(auth.current_retailer), db: Session = Depends(get_db)):
    return _cart_json(_price(db, retailer, body.lines))


# ---------- orders ----------
from .models import Order, OrderLine  # noqa: E402
from .pricing import check_credit  # noqa: E402


def _order_json(o: Order):
    return {
        "id": o.id, "status": o.status, "source": o.source, "total_paise": o.total_paise,
        "credit_hold_reason": o.credit_hold_reason, "created_at": o.created_at.isoformat(),
        "lines": [{"sku_code": l.sku_code, "name": l.sku.name if l.sku else "", "cases": l.cases,
                   "free_cases": l.free_cases, "discount_paise": l.discount_paise,
                   "net_paise": l.net_paise} for l in o.lines],
    }


def create_order(db: Session, retailer: Retailer, items: list[CartItem], source: str = "app",
                 raw_text: str = "", status: str | None = None) -> Order:
    """Price on the server (never trust client prices), reserve nothing, apply the credit rule."""
    priced = _price(db, retailer, items)
    if not priced.placeable:
        raise HTTPException(422, {"message": "Some items cannot be ordered.", "cart": _cart_json(priced)})
    hold = check_credit(retailer.credit_limit_paise, retailer.outstanding_paise, priced.total_paise)
    o = Order(retailer_id=retailer.id, source=source, raw_text=raw_text, total_paise=priced.total_paise,
              status=status or ("on_hold" if hold else "placed"),
              credit_hold_reason=hold or "" if (status or "") != "draft_ai" else "")
    for l in priced.lines:
        o.lines.append(OrderLine(sku_code=l.sku_code, cases=l.cases, free_cases=l.free_cases,
                                 gross_paise=l.gross_paise, discount_paise=l.discount_paise,
                                 net_paise=l.net_paise))
    db.add(o)
    db.commit()
    return o


@app.post("/api/orders", status_code=201)
def place_order(body: CartRequest, retailer: Retailer = Depends(auth.current_retailer),
                db: Session = Depends(get_db)):
    if not body.lines:
        raise HTTPException(422, "Your cart is empty.")
    return _order_json(create_order(db, retailer, body.lines))


@app.get("/api/orders")
def my_orders(retailer: Retailer = Depends(auth.current_retailer), db: Session = Depends(get_db)):
    rows = db.scalars(select(Order).where(Order.retailer_id == retailer.id, Order.status != "draft_ai")
                      .order_by(Order.created_at.desc()).limit(50))
    return [_order_json(o) for o in rows]


@app.get("/api/orders/{order_id}/repeat")
def repeat_order(order_id: int, retailer: Retailer = Depends(auth.current_retailer),
                 db: Session = Depends(get_db)):
    """Re-price an old order at today's prices/stock. Returns the cart; the retailer still confirms."""
    o = db.get(Order, order_id)
    if not o or o.retailer_id != retailer.id:  # same answer for missing and someone else's
        raise HTTPException(404, "Order not found.")
    return _cart_json(_price(db, retailer, [CartItem(sku_code=l.sku_code, cases=l.cases)
                                            for l in o.lines if l.sku_code and l.cases > 0]))
