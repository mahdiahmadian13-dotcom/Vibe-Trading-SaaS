"""JWT Security + Password Hashing"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shared.config import get_settings


# ============================================================================
# Password Hashing (PBKDF2-SHA256)
# ============================================================================

def hash_password(password: str) -> str:
    """Hash password with random salt."""
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}${h.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against hash."""
    try:
        salt, h = hashed.split("$", 1)
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
        return hmac.compare_digest(check.hex(), h)
    except Exception:
        return False


# ============================================================================
# JWT
# ============================================================================

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    settings = get_settings()
    to_encode = data.copy()
    # PyJWT 2.13+ requires 'sub' to be a string
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.JWT_EXPIRY_MINUTES))
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "توکن منقضی شده")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "توکن نامعتبر")


# ============================================================================
# Auth Dependency
# ============================================================================

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
):
    """Extract user from JWT Bearer token. Uses nested DB dependency."""
    if not credentials:
        raise HTTPException(401, "توکن احراز هویت ارسال نشده")

    payload = decode_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "توکن نامعتبر")

    # PyJWT 2.13+ returns sub as string; convert to int for DB query
    try:
        user_id = int(user_id)
    except (ValueError, TypeError):
        raise HTTPException(401, "توکن نامعتبر")

    # Fetch user from DB using the shared get_db dependency
    from sqlalchemy import select
    from shared.models import User, get_db

    # Manually iterate the async generator to get one session
    gen = get_db()
    db = await gen.__anext__()
    try:
        result = await db.execute(select(User).where(User.id == int(user_id)))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(401, "کاربر یافت نشد یا غیرفعال است")
        return user
    finally:
        try:
            await gen.aclose()
        except Exception:
            pass


async def require_auth(user=Depends(get_current_user)):
    return user
