"""Mock-OTP login with a signed session cookie.

In DEMO_MODE the OTP is a fixed code (DEMO_OTP). In production an SMS provider would send a
random code; that integration is out of scope and mocked. Only registered phone numbers can log in.
"""
import re
import time

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import DEMO_MODE, DEMO_OTP, SECRET_KEY
from .db import get_db
from .models import Retailer, User

COOKIE = "session"
SESSION_SECONDS = 60 * 60 * 12
PHONE_RE = re.compile(r"^[6-9]\d{9}$")  # Indian mobile number
_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="session")

# Simple in-memory throttle: max 5 wrong OTPs per phone per 10 minutes.
_failures: dict[str, list[float]] = {}


def valid_phone(phone: str) -> bool:
    return bool(PHONE_RE.match(phone or ""))


def make_session(user_id: int) -> str:
    return _serializer.dumps({"uid": user_id})


def read_session(token: str | None) -> int | None:
    if not token:
        return None
    try:
        return _serializer.loads(token, max_age=SESSION_SECONDS)["uid"]
    except (BadSignature, SignatureExpired, KeyError):
        return None


def too_many_failures(phone: str) -> bool:
    cutoff = time.time() - 600
    _failures[phone] = [t for t in _failures.get(phone, []) if t > cutoff]
    return len(_failures[phone]) >= 5


def record_failure(phone: str) -> None:
    _failures.setdefault(phone, []).append(time.time())


def check_otp(phone: str, otp: str) -> bool:
    # Mock: only the demo code is accepted, and only in demo mode.
    return DEMO_MODE and otp == DEMO_OTP


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = read_session(request.cookies.get(COOKIE))
    user = db.get(User, uid) if uid else None
    if not user:
        raise HTTPException(401, "Please log in.")
    return user


def require_role(role: str):
    def dep(user: User = Depends(current_user)) -> User:
        if user.role != role:
            raise HTTPException(403, "You do not have access to this.")
        return user
    return dep


def current_retailer(user: User = Depends(require_role("retailer")), db: Session = Depends(get_db)) -> Retailer:
    r = db.scalar(select(Retailer).where(Retailer.user_id == user.id))
    if not r:
        raise HTTPException(403, "No shop is linked to this login.")
    return r
