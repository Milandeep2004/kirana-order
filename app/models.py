from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(16))  # retailer | desk
    phone: Mapped[str] = mapped_column(String(15), unique=True)
    name: Mapped[str] = mapped_column(String(80))


class Retailer(Base):
    __tablename__ = "retailers"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    shop_name: Mapped[str] = mapped_column(String(120))
    town: Mapped[str] = mapped_column(String(60), default="Jabalpur")
    salesman_name: Mapped[str] = mapped_column(String(80), default="")
    credit_limit_paise: Mapped[int] = mapped_column(Integer)
    outstanding_paise: Mapped[int] = mapped_column(Integer, default=0)
    # Retailer-specific pricing: basis points off dealer price (100 bp = 1%).
    price_discount_bp: Mapped[int] = mapped_column(Integer, default=0)
    user: Mapped[User] = relationship()


class Sku(Base):
    __tablename__ = "skus"
    code: Mapped[str] = mapped_column(String(24), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    name_hi: Mapped[str] = mapped_column(String(160), default="")  # Hindi display name
    category: Mapped[str] = mapped_column(String(40))
    pieces_per_case: Mapped[int] = mapped_column(Integer)
    piece_price_paise: Mapped[int] = mapped_column(Integer)  # dealer price per piece, ex-GST
    stock_cases: Mapped[int] = mapped_column(Integer)
    keywords: Mapped[str] = mapped_column(Text, default="")  # comma-separated search terms for matching
    color: Mapped[str] = mapped_column(String(9), default="#e8eef7")

    @property
    def case_price_paise(self) -> int:
        return self.piece_price_paise * self.pieces_per_case


class Scheme(Base):
    __tablename__ = "schemes"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku_code: Mapped[str] = mapped_column(ForeignKey("skus.code"))
    type: Mapped[str] = mapped_column(String(16))  # free_goods | slab
    buy_qty: Mapped[int] = mapped_column(Integer, default=0)
    free_qty: Mapped[int] = mapped_column(Integer, default=0)
    min_cases: Mapped[int] = mapped_column(Integer, default=0)
    discount_bp: Mapped[int] = mapped_column(Integer, default=0)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    retailer_id: Mapped[int] = mapped_column(ForeignKey("retailers.id"))
    # draft_ai | placed | on_hold | confirmed | exported | cancelled
    status: Mapped[str] = mapped_column(String(16), default="placed")
    source: Mapped[str] = mapped_column(String(16), default="app")  # app | ai_text
    raw_text: Mapped[str] = mapped_column(Text, default="")
    credit_hold_reason: Mapped[str] = mapped_column(String(200), default="")
    desk_note: Mapped[str] = mapped_column(String(300), default="")
    total_paise: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    retailer: Mapped[Retailer] = relationship()
    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderLine.id"
    )


class OrderLine(Base):
    __tablename__ = "order_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    sku_code: Mapped[str | None] = mapped_column(ForeignKey("skus.code"), nullable=True)
    cases: Mapped[int] = mapped_column(Integer, default=0)
    free_cases: Mapped[int] = mapped_column(Integer, default=0)
    gross_paise: Mapped[int] = mapped_column(Integer, default=0)
    discount_paise: Mapped[int] = mapped_column(Integer, default=0)
    net_paise: Mapped[int] = mapped_column(Integer, default=0)
    # AI capture provenance
    original_text: Mapped[str] = mapped_column(String(300), default="")
    confidence: Mapped[str] = mapped_column(String(8), default="")  # high | medium | low
    score: Mapped[float] = mapped_column(Float, default=0.0)
    alternatives: Mapped[list] = mapped_column(JSON, default=list)
    unit_question: Mapped[bool] = mapped_column(Boolean, default=False)
    qty: Mapped[int | None] = mapped_column(Integer, nullable=True)  # as written by the retailer
    unit: Mapped[str | None] = mapped_column(String(8), nullable=True)  # case | piece | None
    note: Mapped[str] = mapped_column(String(300), default="")
    order: Mapped[Order] = relationship(back_populates="lines")
    sku: Mapped[Sku | None] = relationship()


class Alias(Base):
    """A retailer's own wording for a SKU. Written when the desk corrects an AI line."""

    __tablename__ = "aliases"
    __table_args__ = (UniqueConstraint("retailer_id", "phrase"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    retailer_id: Mapped[int] = mapped_column(ForeignKey("retailers.id"))
    phrase: Mapped[str] = mapped_column(String(120))
    sku_code: Mapped[str] = mapped_column(ForeignKey("skus.code"))
    from_desk_correction: Mapped[bool] = mapped_column(Boolean, default=False)
