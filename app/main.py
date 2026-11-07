from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import auth
from .config import COOKIE_SECURE, DEMO_MODE, DEMO_OTP
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
app.add_middleware(GZipMiddleware, minimum_size=500)  # small payloads matter on 3G

STATIC = Path(__file__).parent / "static"
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.api_route("/health", methods=["GET", "HEAD"])
def health():  # HEAD too: uptime monitors often probe with HEAD
    return {"status": "ok"}


@app.get("/api/config")
def public_config():
    """Only used by the login screen. The demo code is exposed ONLY when DEMO_MODE=1."""
    from .config import DEMO_MODE, DEMO_OTP
    out = {"demo_mode": DEMO_MODE}
    if DEMO_MODE:
        out["demo_otp"] = DEMO_OTP
        out["demo_users"] = [
            {"phone": "9000000001", "label": "Sharma General Store", "role": "retailer", "note": "Retailer. Normal prices, learned wording"},
            {"phone": "9000000002", "label": "Gupta Kirana", "role": "retailer", "note": "Retailer. Tight credit, big orders go on hold"},
            {"phone": "9000000100", "label": "Order desk", "role": "desk", "note": "Office. Check, edit, confirm, export to Tally"}]
    return out


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
    if not user and DEMO_MODE and body.otp == DEMO_OTP:
        # Demo only (fake data): be helpful instead of cryptic. Outside demo mode the message stays generic.
        raise HTTPException(401, "This number is not registered. In the demo, use one of the quick-login buttons below.")
    if not user or not auth.check_otp(body.phone, body.otp):
        auth.record_failure(body.phone)
        raise HTTPException(401, "Wrong phone number or code.")
    response.set_cookie(auth.COOKIE, auth.make_session(user.id), max_age=auth.SESSION_SECONDS,
                        httponly=True, samesite="lax", secure=COOKIE_SECURE)
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
        if r:
            out["credit_limit_paise"] = r.credit_limit_paise
            out["outstanding_paise"] = r.outstanding_paise
    return out


# ---------- catalogue (retailer-specific prices; visible only to that retailer) ----------
def _scheme_text(s: Scheme) -> str:
    if s.type == "free_goods":
        return f"Buy {s.buy_qty} cases, get {s.free_qty} free"
    return f"{s.discount_bp / 100:g}% off on {s.min_cases}+ cases"


@app.get("/api/catalogue")
def catalogue(retailer: Retailer = Depends(auth.current_retailer), db: Session = Depends(get_db)):
    schemes: dict[str, list[str]] = {}
    rules: dict[str, list[dict]] = {}
    for sc in db.scalars(select(Scheme)):
        schemes.setdefault(sc.sku_code, []).append(_scheme_text(sc))
        # Structured form so the browser can word the offer in the shopkeeper's language.
        rules.setdefault(sc.sku_code, []).append({"type": sc.type, "buy_qty": sc.buy_qty, "free_qty": sc.free_qty,
                                                  "min_cases": sc.min_cases, "discount_pct": sc.discount_bp / 100})
    items = []
    for s in db.scalars(select(Sku).order_by(Sku.category, Sku.name)):
        case_price = adjust_price(s.case_price_paise, retailer.price_discount_bp)
        stock = "out" if s.stock_cases <= 0 else "low" if s.stock_cases < 50 else "in"
        items.append({
            "code": s.code, "name": s.name, "name_hi": s.name_hi, "category": s.category, "color": s.color,
            "pieces_per_case": s.pieces_per_case,
            "piece_price_paise": adjust_price(s.piece_price_paise, retailer.price_discount_bp),
            "case_price_paise": case_price, "stock_status": stock,
            "schemes": schemes.get(s.code, []), "scheme_rules": rules.get(s.code, []),
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


# ---------- AI capture (text) ----------
from . import capture as cap  # noqa: E402
from .config import ANTHROPIC_API_KEY, LLM_MODEL  # noqa: E402
from .models import Alias  # noqa: E402

_llm = cap.make_anthropic_picker(ANTHROPIC_API_KEY, LLM_MODEL) if ANTHROPIC_API_KEY else None


class CaptureRequest(BaseModel):
    text: str = Field(min_length=3, max_length=2000)


class ConfirmLine(BaseModel):
    line_id: int
    sku_code: str = Field(max_length=24)
    qty: int = Field(ge=1, le=10000)
    unit: str = Field(pattern="^(case|piece)$")  # the retailer must state the unit; we never guess


class ConfirmRequest(BaseModel):
    lines: list[ConfirmLine] = Field(min_length=1, max_length=100)


def _draft_json(o: Order, db: Session):
    names = {s.code: s.name for s in db.scalars(select(Sku))}
    return {"id": o.id, "status": o.status, "raw_text": o.raw_text, "lines": [{
        "line_id": l.id, "original_text": l.original_text, "sku_code": l.sku_code,
        "sku_name": names.get(l.sku_code, ""), "qty": l.qty, "unit": l.unit,
        "unit_question": l.unit_question, "confidence": l.confidence, "score": l.score,
        "alternatives": l.alternatives, "note": l.note,
        "out_of_stock": bool(l.sku_code and l.sku and l.sku.stock_cases <= 0),
    } for l in o.lines]}


def _order_history(db: Session, retailer_id: int) -> dict[str, int]:
    """SKU code -> number of this shop's real orders (not drafts or cancelled) that contain it."""
    rows = db.execute(
        select(OrderLine.sku_code, func.count(func.distinct(OrderLine.order_id)))
        .join(Order, Order.id == OrderLine.order_id)
        .where(Order.retailer_id == retailer_id, Order.status.in_(("placed", "on_hold", "confirmed", "exported")),
               OrderLine.sku_code.is_not(None))
        .group_by(OrderLine.sku_code)).all()
    return {code: n for code, n in rows}


@app.post("/api/capture", status_code=201)
def capture_order(body: CaptureRequest, retailer: Retailer = Depends(auth.current_retailer),
                  db: Session = Depends(get_db)):
    catalog = [cap.CatalogItem(s.code, s.name, s.category, s.keywords, s.stock_cases)
               for s in db.scalars(select(Sku))]
    aliases = {a.phrase: a.sku_code for a in db.scalars(select(Alias).where(Alias.retailer_id == retailer.id))}
    lines = cap.capture(body.text, catalog, aliases, _llm, _order_history(db, retailer.id))
    # Nothing understood at all (no item matched and no quantity found) is not an order: say so.
    if not any(c.sku_code or c.qty for c in lines):
        raise HTTPException(422, "We could not find any items in that message. Try e.g. '5 case biscuit'.")
    o = Order(retailer_id=retailer.id, status="draft_ai", source="ai_text", raw_text=body.text)
    for c in lines:
        o.lines.append(OrderLine(
            sku_code=c.sku_code, original_text=c.original_text[:300], confidence=c.confidence,
            score=c.score, unit_question=c.unit_question, qty=c.qty, unit=c.unit, note=c.note[:300],
            alternatives=[{"code": a.code, "name": a.name, "score": a.score} for a in c.alternatives]))
    db.add(o)
    db.commit()
    return _draft_json(o, db)


def _own_draft(db: Session, retailer: Retailer, order_id: int) -> Order:
    o = db.get(Order, order_id)
    if not o or o.retailer_id != retailer.id or o.status != "draft_ai":
        raise HTTPException(404, "Draft not found.")
    return o


@app.get("/api/capture/{order_id}")
def get_draft(order_id: int, retailer: Retailer = Depends(auth.current_retailer), db: Session = Depends(get_db)):
    return _draft_json(_own_draft(db, retailer, order_id), db)


@app.post("/api/capture/{order_id}/confirm")
def confirm_draft(order_id: int, body: ConfirmRequest, retailer: Retailer = Depends(auth.current_retailer),
                  db: Session = Depends(get_db)):
    """Nothing becomes an order until a person confirms every line (with an explicit SKU and unit)."""
    o = _own_draft(db, retailer, order_id)
    by_id = {l.id: l for l in o.lines}
    if {c.line_id for c in body.lines} != set(by_id):
        raise HTTPException(422, "Please review every line before confirming.")
    skus = {s.code: s for s in db.scalars(select(Sku))}
    items: list[CartItem] = []
    for c in body.lines:
        sku = skus.get(c.sku_code)
        if not sku:
            raise HTTPException(422, f"Unknown item {c.sku_code}.")
        cases = c.qty
        if c.unit == "piece":
            if c.qty % sku.pieces_per_case:
                raise HTTPException(422, f"{sku.name} is sold by the case of {sku.pieces_per_case}. "
                                         f"{c.qty} pieces is not a whole number of cases.")
            cases = c.qty // sku.pieces_per_case
        items.append(CartItem(sku_code=c.sku_code, cases=cases))
        by_id[c.line_id].sku_code, by_id[c.line_id].unit_question = c.sku_code, False
    priced = _price(db, retailer, items)
    if not priced.placeable:
        db.rollback()
        raise HTTPException(422, {"message": "Some items cannot be ordered.", "cart": _cart_json(priced)})
    hold = check_credit(retailer.credit_limit_paise, retailer.outstanding_paise, priced.total_paise)
    first = {}
    for l in o.lines:
        first.setdefault(l.sku_code, l)
    prov = {code: (l.original_text, l.confidence, l.score, l.alternatives, l.qty, l.unit, l.note)
            for code, l in first.items()}
    o.lines.clear()
    for l in priced.lines:
        p = prov.get(l.sku_code, ("", "", 0.0, [], None, None, ""))
        o.lines.append(OrderLine(sku_code=l.sku_code, cases=l.cases, free_cases=l.free_cases,
                                 gross_paise=l.gross_paise, discount_paise=l.discount_paise,
                                 net_paise=l.net_paise, original_text=p[0], confidence=p[1], score=p[2],
                                 alternatives=p[3], qty=p[4], unit=p[5], note=p[6]))
    o.total_paise = priced.total_paise
    o.status, o.credit_hold_reason = ("on_hold", hold) if hold else ("placed", "")
    db.commit()
    return _order_json(o)


# ---------- order desk ----------
import csv  # noqa: E402
import io  # noqa: E402

from fastapi.responses import PlainTextResponse  # noqa: E402

desk_only = auth.require_role("desk")
EDITABLE = ("placed", "on_hold")


def _desk_order_json(o: Order, db: Session):
    j = _order_json(o)
    r = o.retailer
    j["retailer"] = {"id": r.id, "shop_name": r.shop_name, "salesman": r.salesman_name,
                     "credit_limit_paise": r.credit_limit_paise, "outstanding_paise": r.outstanding_paise}
    j["raw_text"] = o.raw_text
    j["desk_note"] = o.desk_note
    for out, l in zip(j["lines"], o.lines):
        out.update({"line_id": l.id, "original_text": l.original_text, "confidence": l.confidence,
                    "alternatives": l.alternatives, "note": l.note,
                    "stock_cases": l.sku.stock_cases if l.sku else None})
    return j


def _desk_order(db: Session, order_id: int) -> Order:
    o = db.get(Order, order_id)
    if not o or o.status == "draft_ai":
        raise HTTPException(404, "Order not found.")
    return o


@app.get("/api/desk/orders")
def desk_queue(status: str | None = None, _: User = Depends(desk_only), db: Session = Depends(get_db)):
    q = select(Order).where(Order.status != "draft_ai")
    if status:
        q = q.where(Order.status == status)
    rows = db.scalars(q.order_by(Order.created_at.desc()).limit(200))
    return [{"id": o.id, "status": o.status, "source": o.source, "shop_name": o.retailer.shop_name,
             "total_paise": o.total_paise, "credit_hold_reason": o.credit_hold_reason,
             "created_at": o.created_at.isoformat(), "line_count": len(o.lines)} for o in rows]


@app.get("/api/desk/skus")
def desk_skus(_: User = Depends(desk_only), db: Session = Depends(get_db)):
    return [{"code": k.code, "name": k.name, "stock_cases": k.stock_cases}
            for k in db.scalars(select(Sku).order_by(Sku.category, Sku.name))]


@app.get("/api/desk/orders/{order_id}")
def desk_order(order_id: int, _: User = Depends(desk_only), db: Session = Depends(get_db)):
    return _desk_order_json(_desk_order(db, order_id), db)


class DeskLine(BaseModel):
    line_id: int | None = None  # existing line (lets us learn an alias if the SKU is changed)
    sku_code: str = Field(max_length=24)
    cases: int = Field(ge=1, le=10000)


class DeskEdit(BaseModel):
    lines: list[DeskLine] = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=300)


@app.put("/api/desk/orders/{order_id}/lines")
def desk_edit(order_id: int, body: DeskEdit, _: User = Depends(desk_only), db: Session = Depends(get_db)):
    o = _desk_order(db, order_id)
    if o.status not in EDITABLE:
        raise HTTPException(409, f"A {o.status} order can no longer be edited.")
    old = {l.id: l for l in o.lines}
    priced = _price(db, o.retailer, [CartItem(sku_code=l.sku_code, cases=l.cases) for l in body.lines])
    if not priced.placeable:
        raise HTTPException(422, {"message": "Some items cannot be ordered.", "cart": _cart_json(priced)})
    # Learn aliases: the desk changed which SKU a retailer's own words refer to.
    for dl in body.lines:
        prev = old.get(dl.line_id) if dl.line_id else None
        if prev and prev.original_text and prev.sku_code != dl.sku_code:
            phrase = cap.clean_phrase(cap.strip_quantity(prev.original_text))
            if phrase:
                existing = db.scalar(select(Alias).where(Alias.retailer_id == o.retailer_id,
                                                         Alias.phrase == phrase))
                if existing:
                    existing.sku_code = dl.sku_code
                else:
                    db.add(Alias(retailer_id=o.retailer_id, phrase=phrase, sku_code=dl.sku_code,
                                 from_desk_correction=True))
    prov = {l.sku_code: l for l in o.lines}
    o.lines.clear()
    for l in priced.lines:
        p = prov.get(l.sku_code)
        o.lines.append(OrderLine(sku_code=l.sku_code, cases=l.cases, free_cases=l.free_cases,
                                 gross_paise=l.gross_paise, discount_paise=l.discount_paise,
                                 net_paise=l.net_paise,
                                 original_text=p.original_text if p else "", confidence=p.confidence if p else "",
                                 score=p.score if p else 0.0, alternatives=p.alternatives if p else [],
                                 qty=p.qty if p else None, unit=p.unit if p else None,
                                 note=p.note if p else ""))
    o.total_paise = priced.total_paise
    if body.note:
        o.desk_note = body.note
    if o.status == "on_hold":
        o.credit_hold_reason = check_credit(o.retailer.credit_limit_paise, o.retailer.outstanding_paise,
                                            o.total_paise) or "Within credit limit after edit; ready to release."
    db.commit()
    return _desk_order_json(o, db)


class DeskNote(BaseModel):
    note: str = Field(default="", max_length=300)


@app.post("/api/desk/orders/{order_id}/release")
def desk_release(order_id: int, body: DeskNote, _: User = Depends(desk_only), db: Session = Depends(get_db)):
    o = _desk_order(db, order_id)
    if o.status != "on_hold":
        raise HTTPException(409, "Only orders on hold can be released.")
    o.status = "placed"
    o.desk_note = body.note or o.desk_note
    db.commit()
    return _desk_order_json(o, db)


@app.post("/api/desk/orders/{order_id}/confirm")
def desk_confirm(order_id: int, _: User = Depends(desk_only), db: Session = Depends(get_db)):
    """Confirm = stock is re-checked now, deducted (bought + free cases), and credit exposure increases."""
    o = _desk_order(db, order_id)
    if o.status == "on_hold":
        raise HTTPException(409, "Release the credit hold before confirming.")
    if o.status != "placed":
        raise HTTPException(409, f"A {o.status} order cannot be confirmed.")
    priced = _price(db, o.retailer, [CartItem(sku_code=l.sku_code, cases=l.cases) for l in o.lines if l.sku_code])
    if not priced.placeable:
        raise HTTPException(409, {"message": "Stock has changed since this order was placed.",
                                  "cart": _cart_json(priced)})
    for l in o.lines:
        db.get(Sku, l.sku_code).stock_cases -= l.cases + l.free_cases
    o.retailer.outstanding_paise += o.total_paise
    o.status = "confirmed"
    db.commit()
    return _desk_order_json(o, db)


@app.post("/api/desk/orders/{order_id}/cancel")
def desk_cancel(order_id: int, body: DeskNote, _: User = Depends(desk_only), db: Session = Depends(get_db)):
    o = _desk_order(db, order_id)
    if o.status not in EDITABLE:
        raise HTTPException(409, f"A {o.status} order cannot be cancelled here.")
    o.status, o.desk_note = "cancelled", body.note or o.desk_note
    db.commit()
    return _desk_order_json(o, db)


def _csv_safe(v) -> str:
    """Stop spreadsheet formula injection from retailer-controlled text."""
    s = str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


TALLY_COLUMNS = ["Voucher No", "Date", "Party Name", "Item Code", "Item Name", "Qty (cases)",
                 "Free Qty (cases)", "Rate per case (excl GST)", "Discount", "Amount (excl GST)", "Narration"]


@app.post("/api/desk/export/tally", response_class=PlainTextResponse)
def tally_export(_: User = Depends(desk_only), db: Session = Depends(get_db)):
    """CSV of all confirmed orders (our assumed Tally layout), then mark them exported."""
    orders = list(db.scalars(select(Order).where(Order.status == "confirmed").order_by(Order.id)))
    if not orders:
        raise HTTPException(404, "No confirmed orders to export.")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(TALLY_COLUMNS)
    for o in orders:
        for l in o.lines:
            rate = l.gross_paise // l.cases if l.cases else 0
            w.writerow([f"ORD-{o.id:06d}", o.created_at.strftime("%d-%m-%Y"), _csv_safe(o.retailer.shop_name),
                        l.sku_code, _csv_safe(l.sku.name), l.cases, l.free_cases, f"{rate / 100:.2f}",
                        f"{l.discount_paise / 100:.2f}", f"{l.net_paise / 100:.2f}",
                        _csv_safe(o.desk_note)])
        o.status = "exported"
    db.commit()
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="tally-orders.csv"'})


# ---------- web app shell (static files, installable PWA) ----------
@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # Must be served from the root so it can control the whole app.
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
