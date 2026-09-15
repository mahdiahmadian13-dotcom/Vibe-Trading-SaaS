"""Vibe-Trading SaaS — Gateway (FastAPI)"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from shared.config import get_settings
import hashlib
import hmac as _hmac

from shared.models import (
    init_db, get_db, User, Subscription, Task, VibeSession,
    UsageLog, SwarmRun, PlanTier, SubscriptionStatus, TaskStatus, _utcnow,
    EngineNode, WorkerNode, Payment, SettingKV, LoginLog, ServerNode, Referral, FleetUpdate
)
from sqlalchemy import update as _sa_update

from app import coupons as coupons_mod
from app.fleet import EnginePool, get_engine_pool, start_background_loops, stop_background_loops
import app.crypto as crypto_mod
import app.provision as provision_mod
from app import roles as roles_mod
from shared.security import (
    create_access_token, hash_password, verify_password,
    require_auth, get_current_user
)


# ============================================================================
# Plan Limits — all keys use {action}s_per_day pattern
# ============================================================================

PLAN_LIMITS = {
    PlanTier.FREE:       {"sessions_per_day": 50,  "messages_per_day": 500, "backtests_per_day": 20, "swarm_per_day": 5,  "live": False},
    PlanTier.BASIC:      {"sessions_per_day": 20,  "messages_per_day": 150, "backtests_per_day": 10,  "swarm_per_day": 3,  "live": False},
    PlanTier.PRO:        {"sessions_per_day": 100, "messages_per_day": 500, "backtests_per_day": 50,  "swarm_per_day": 20, "live": True},
    PlanTier.ENTERPRISE: {"sessions_per_day": -1,  "messages_per_day": -1,  "backtests_per_day": -1,  "swarm_per_day": -1, "live": True},
}

# Maps action name → UsageLog column name
ACTION_FIELD_MAP = {
    "session":   "sessions_created",
    "message":   "messages_sent",
    "backtest":  "backtests_run",
    "swarm":     "swarm_runs",
}

# Maps action name → PLAN_LIMITS key (not all follow the {action}s_per_day pattern)
ACTION_LIMIT_KEY = {
    "session":   "sessions_per_day",
    "message":   "messages_per_day",
    "backtest":  "backtests_per_day",
    "swarm":     "swarm_per_day",
}


# ============================================================================
# Rate Limiter (in-memory, per-user)
# ============================================================================

_last_message_times: dict[int, float] = {}


def _check_rate_limit(user_id: int, min_interval: float) -> None:
    now = time.monotonic()
    last = _last_message_times.get(user_id, 0)
    if now - last < min_interval:
        raise HTTPException(
            status_code=429,
            detail=f"لطفاً {int(min_interval - (now - last))} ثانیه صبر کنید"
        )
    _last_message_times[user_id] = now


# ============================================================================
# Usage Tracking
# ============================================================================

async def _get_usage(db: AsyncSession, user_id: int) -> UsageLog:
    """Get or create today's usage row. Handles race conditions."""
    today = _utcnow().strftime("%Y-%m-%d")
    result = await db.execute(
        select(UsageLog).where(UsageLog.user_id == user_id, UsageLog.date == today)
    )
    usage = result.scalar_one_or_none()
    if usage:
        return usage

    # Create with race-condition protection
    usage = UsageLog(user_id=user_id, date=today)
    db.add(usage)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        result = await db.execute(
            select(UsageLog).where(UsageLog.user_id == user_id, UsageLog.date == today)
        )
        usage = result.scalar_one()
    return usage


async def _check_limit(db: AsyncSession, user: User, action: str) -> None:
    """Check if user has remaining quota for the given action."""
    plan = user.current_plan
    limits = PLAN_LIMITS[plan]

    limit_key = ACTION_LIMIT_KEY.get(action, f"{action}s_per_day")
    limit = limits.get(limit_key, -1)
    if limit == -1:
        return  # unlimited

    usage = await _get_usage(db, user.id)
    field_name = ACTION_FIELD_MAP[action]
    current = getattr(usage, field_name, 0) or 0

    if current >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"سقف {action} روزانه شما تمام شده. پلن خود را ارتقا دهید."
        )


# ============================================================================
# Vibe Engine Client
# ============================================================================

class VibeClient:
    """Async HTTP client for Vibe-Trading engine."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.request(method, url, headers=self.headers, **kwargs)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()


_vibe_client = None


def get_vibe() -> VibeClient:
    global _vibe_client
    if not _vibe_client:
        settings = get_settings()
        _vibe_client = VibeClient(settings.VIBE_ENGINE_URL, settings.VIBE_ENGINE_API_KEY)
    return _vibe_client


# ============================================================================
# Multi-Tenant Isolation
# ============================================================================

async def _owned_session_ids(db: AsyncSession, user: User) -> set[str]:
    result = await db.execute(
        select(VibeSession.vibe_session_id).where(VibeSession.user_id == user.id)
    )
    return {row[0] for row in result.all()}


async def _require_owned_session(db: AsyncSession, user: User, session_id: str) -> None:
    owned = await _owned_session_ids(db, user)
    if session_id not in owned and not user.is_admin:
        raise HTTPException(status_code=403, detail="این جلسه متعلق به شما نیست")


# ============================================================================
# App Lifecycle
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_db(settings.DATABASE_URL)

    # Seed the .env engine as node #1 when the fleet table is empty
    from shared.models import _session_factory
    async with _session_factory() as sdb:
        count = (await sdb.execute(select(func.count()).select_from(EngineNode))).scalar() or 0
        if count == 0 and settings.VIBE_ENGINE_URL:
            sdb.add(EngineNode(
                name="engine-primary",
                url=settings.VIBE_ENGINE_URL,
                api_key=None,
                is_enabled=True,
                is_healthy=True,
            ))
            await sdb.commit()

    # Smart task dispatcher (least-loaded routing + reaper)
    from app.dispatch import start_dispatcher, stop_dispatcher
    await start_dispatcher()

    start_background_loops()
    yield
    await stop_dispatcher()
    await stop_background_loops()


app = FastAPI(
    title="Vibe-Trading SaaS Gateway",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Health Check
# ============================================================================

@app.get("/health")
async def health():
    return {"status": "ok", "service": "gateway"}


# ============================================================================
# Engine Fleet (multi-server) — engine pool accessor
# ============================================================================

def get_pool() -> EnginePool:
    return get_engine_pool()


# ============================================================================
# Auth Routes
# ============================================================================

class RegisterRequest(BaseModel):
    username: str
    password: str
    phone: str | None = None
    device_id: str | None = None
    telegram_id: int | None = None
    tg_init_data: str | None = None   # signed initData — required when telegram_id is sent


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
    plan: str


@app.post("/api/v1/auth/register", response_model=TokenResponse)
async def register(req: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    settings = get_settings()

    # ---- Single-account policy (2026-09): registration is Telegram-only ----
    # The public web app auto-creates accounts via POST /api/v1/auth/telegram with
    # a signed initData payload. This legacy endpoint no longer mints accounts
    # on its own — it only attaches a verified Telegram identity, or lets an
    # ADMIN mint users (admin has its own guarded endpoint). Unauthenticated
    # browser sign-ups are closed to stop free-coupon farming.
    raise HTTPException(
        403,
        "ثبت‌نام فقط از داخل ربات تلگرام انجام می‌شود — ربات را باز کنید و «ورود به پلتفرم» را بزنید",
    )


@app.post("/api/v1/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(401, "نام کاربری یا رمز عبور اشتباه است")

    token = create_access_token({"sub": user.id, "username": user.username})
    try:
        _ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else None)
        _ua = request.headers.get("user-agent", "")[:500]
        db.add(LoginLog(user_id=user.id, ip=_ip, user_agent=_ua))
        await db.commit()
    except Exception:
        await db.rollback()
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        plan=user.current_plan.value,
    )


# ============================================================================
# Telegram WebApp Auth (initData HMAC-SHA256 validation, per Telegram spec)
# ============================================================================

class TelegramAuthRequest(BaseModel):
    init_data: str
    ref_code: str | None = None
    start_param: str | None = None  # t.me/bot?start=<code> — bot forwards it


def _verify_init_data(init_data: str, bot_token: str, max_age_s: int = 86400) -> dict | None:
    """Validate Telegram WebApp initData signature (HMAC-SHA256 over sorted kv)."""
    try:
        from urllib.parse import unquote, parse_qsl
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", "")
        if not received_hash:
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret_key = _hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc = _hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(calc, received_hash):
            return None
        # freshness: auth_date within 24h
        auth_date = int(pairs.get("auth_date", "0"))
        if auth_date and (time.time() - auth_date) > max_age_s:
            return None
        import json as _json
        if "user" in pairs:
            pairs["user"] = _json.loads(pairs["user"])
        return pairs
    except Exception:
        return None


def _gen_ref_code(user_id: int) -> str:
    """Short unique referral code: base36 of uid + 4 random chars (unique-retry upstream)."""
    import random as _rnd
    import string as _string
    digits = _string.digits + _string.ascii_lowercase
    b36 = ""
    n = user_id
    while n:
        n, r = divmod(n, 36)
        b36 = digits[r] + b36
    suffix = "".join(_rnd.choices("abcdefghjkmnpqrstuvwxyz23456789", k=4))
    return f"{b36 or '0'}{suffix}"[:16]


@app.post("/api/v1/auth/telegram", response_model=TokenResponse)
async def auth_telegram(req: TelegramAuthRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """One-tap login for the Telegram WebApp: validate initData, find-or-create user."""
    settings = get_settings()
    bot_token = settings.TELEGRAM_BOT_TOKEN
    if not bot_token:
        raise HTTPException(500, "ورود تلگرامی روی سرور پیکربندی نشده")

    data = _verify_init_data(req.init_data, bot_token)
    if not data or "user" not in data:
        raise HTTPException(401, "اعتبارنامه تلگرام نامعتبر است — دوباره از ربات باز کن")

    tg_user = data["user"]
    tg_id = int(tg_user.get("id", 0))
    if not tg_id:
        raise HTTPException(401, "کاربر تلگرام شناسایی نشد")
    display = (tg_user.get("first_name") or "").strip() or f"tg{tg_id}"

    # find-or-create by telegram_id
    res = await db.execute(select(User).where(User.telegram_id == tg_id))
    user = res.scalar_one_or_none()

    created = False
    if user is None:
        # unique username from tg id; password random (never used — auth via initData)
        username = f"tg{tg_id}"
        _dupe = await db.execute(select(User).where(User.username == username))
        if _dupe.scalar_one_or_none():
            username = f"tg{tg_id}_{secrets.token_hex(2)}"
        user = User(
            username=username,
            hashed_password=hash_password(secrets.token_hex(16)),
            telegram_id=tg_id,
            telegram_name=display[:128],
            device_id=f"tg:{tg_id}",
        )
        db.add(user)
        await db.flush()
        db.add(Subscription(user_id=user.id, plan_tier=PlanTier.FREE,
                            status=SubscriptionStatus.ACTIVE,
                            expires_at=_utcnow() + timedelta(days=365 * 10)))
        await db.flush()
        created = True

    # referral attribution: only on first creation, not self-referral.
    # The bot stores ?start=<code> deep links in Redis (tg:{uid}:ref) — read
    # them as a fallback when the WebApp didn't send start_param itself
    # (initData start_param only exists for attachment-menu launches).
    ref_code = (req.start_param or req.ref_code or "").strip()[:16]
    if ref_code.startswith("ref_"):  # legacy deep-link format
        ref_code = ref_code[4:]
    if created and not ref_code:
        try:
            import redis.asyncio as _redis
            _r = _redis.from_url(get_settings().REDIS_URL, decode_responses=True)
            ref_code = (await _r.get(f"tg:{tg_id}:ref") or "").strip()[:16]
            if ref_code.startswith("ref_"):
                ref_code = ref_code[4:]
            await _r.delete(f"tg:{tg_id}:ref")
            await _r.aclose()
        except Exception:
            ref_code = ""
    referral_done = False
    if created and ref_code:
        res_r = await db.execute(select(User).where(User.ref_code == ref_code))
        referrer = res_r.scalar_one_or_none()
        if referrer and referrer.id != user.id:
            try:
                db.add(Referral(referrer_id=referrer.id, invited_user_id=user.id,
                                invited_telegram_id=tg_id, invited_username=user.username,
                                reward_granted=True))
                await db.flush()
                referral_done = True
            except Exception:
                await db.rollback()
                # re-anchor user in this session after rollback
                user = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    # grant the referrer reward AFTER the user row is safely committed —
    # grant_bonus commits internally and would expire this session's objects
    if referral_done:
        await coupons_mod.grant_bonus(db, referrer.id, 2, reason=f"referral:{user.id}")

    # keep display name fresh + ensure ref_code exists
    if not user.ref_code:
        user.ref_code = _gen_ref_code(user.id)
        try:
            await db.flush()
        except Exception:
            await db.rollback()
            await db.refresh(user)

    # welcome pack for brand-new users (no-ops if already granted)
    if created:
        await coupons_mod.ensure_welcome(db, user.id)
        user = (await db.execute(select(User).where(User.id == user.id))).scalar_one()

    token = create_access_token({"sub": user.id, "username": user.username})
    try:
        _ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else None)
        _ua = request.headers.get("user-agent", "")[:500]
        db.add(LoginLog(user_id=user.id, ip=_ip, user_agent=_ua))
        await db.commit()
    except Exception:
        await db.rollback()
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        plan=user.current_plan.value,
    )


@app.get("/api/v1/referrals/me", response_model=None)
async def my_referrals(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Referral wallet: my code, link, invited count, reward coupons."""
    # NOTE: the require_auth `user` belongs to a closed nested session —
    # re-fetch inside THIS session so writes actually persist.
    me = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    if not me.ref_code:
        me.ref_code = _gen_ref_code(me.id)
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            await db.refresh(me)
    count = await db.execute(
        select(func.count()).select_from(Referral).where(Referral.referrer_id == me.id)
    )
    total = count.scalar() or 0
    rewarded = await db.execute(
        select(func.count()).select_from(Referral).where(
            (Referral.referrer_id == me.id) & (Referral.reward_granted.is_(True))
        )
    )
    settings = get_settings()
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    link = f"{base}/app/" if base else "/app/"
    return {
        "ref_code": me.ref_code,
        "tg_link": f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={me.ref_code}" if settings.TELEGRAM_BOT_USERNAME else None,
        "link": link,
        "invited": total,
        "reward_coupons": (rewarded.scalar() or 0) * 2,
    }


# ============================================================================
# Subscription Routes
# ============================================================================

@app.get("/api/v1/coupons")
async def get_coupons(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """User's coupon wallet: backtest + swarm counts + refill times."""
    state = await coupons_mod.coupon_state(db, user)
    return {
        "backtest": state["backtest"],
        "swarm": state["swarm"],
        "plan": user.current_plan.value,
        "metered": coupons_mod.uses_coupons(user),
    }


@app.get("/api/v1/subscription/plans")
async def list_plans():
    return [
        {"tier": "free",       "name": "رایگان",    "price": 0,          "limits": PLAN_LIMITS[PlanTier.FREE]},
        {"tier": "basic",      "name": "پایه",      "price": 299_000,    "limits": PLAN_LIMITS[PlanTier.BASIC]},
        {"tier": "pro",        "name": "حرفه‌ای",    "price": 799_000,    "limits": PLAN_LIMITS[PlanTier.PRO]},
        {"tier": "enterprise", "name": "سازمانی",    "price": 1_999_000,  "limits": PLAN_LIMITS[PlanTier.ENTERPRISE]},
    ]


@app.get("/api/v1/subscription/current")
async def current_subscription(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    return {
        "plan": user.current_plan.value,
        "limits": PLAN_LIMITS[user.current_plan],
    }


# ============================================================================
# Vibe-Trading Proxy Routes
# ============================================================================

@app.post("/api/v1/vibe/sessions")
async def create_session(
    body: dict | None = None,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a new Vibe-Trading session (with ownership tracking)."""
    await _check_limit(db, user, "session")

    raw_title = str((body or {}).get("title", "")).strip()
    title = raw_title[:80] if raw_title else f"گفتگوی {user.username}"
    pool = get_pool()
    result = await pool.request(db, "POST", "/sessions", json={"title": title})
    session_id = result.get("session_id") or result.get("id")

    # Record ownership
    vs = VibeSession(user_id=user.id, vibe_session_id=session_id)
    db.add(vs)
    usage = await _get_usage(db, user.id)
    usage.sessions_created += 1
    await db.commit()

    return {"session_id": session_id, "status": "created"}


@app.post("/api/v1/vibe/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: dict,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Send a message to Vibe-Trading agent (with rate limiting)."""
    settings = get_settings()
    _check_rate_limit(user.id, settings.MESSAGE_MIN_INTERVAL_SECONDS)
    await _require_owned_session(db, user, session_id)
    await _check_limit(db, user, "message")

    # Free-tier: one backtest coupon per analysis message
    used_coupon = None
    if coupons_mod.uses_coupons(user):
        used_coupon = await coupons_mod.try_consume(db, user, "backtest", action=f"chat:{session_id[:12]}")
        if used_coupon is None:
            raise HTTPException(
                status_code=429,
                detail="کوپن بک‌تست شما به پایان رسید. ۲۴ ساعت دیگر (نیمه‌شب) کوپن جدید شارژ می‌شود.",
            )

    pool = get_pool()
    try:
        result = await pool.request(db, "POST", f"/sessions/{session_id}/messages", json=body)
    except HTTPException:
        # engine rejected (busy/409/5xx) → give the coupon back
        if used_coupon is not None:
            await coupons_mod.refund_coupon(db, user.id, used_coupon.action or "")
        raise

    usage = await _get_usage(db, user.id)
    usage.messages_sent += 1
    await db.commit()

    return result


@app.get("/api/v1/vibe/sessions/{session_id}/events")
async def stream_events(
    session_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """SSE proxy for real-time streaming."""
    await _require_owned_session(db, user, session_id)

    # Release DB lock before long-lived stream
    await db.close()

    settings = get_settings()
    from app.fleet import get_engine_pool
    _pool = get_engine_pool()
    node = await _pool._pick(db)
    base = (node.url if node else settings.VIBE_ENGINE_URL).rstrip("/")
    key = (node.api_key if node and node.api_key else settings.VIBE_ENGINE_API_KEY)
    url = f"{base}/sessions/{session_id}/events"
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    async def event_generator():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                async for line in resp.aiter_lines():
                    yield f"{line}\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/vibe/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await _require_owned_session(db, user, session_id)
    pool = get_pool()
    return await pool.request(db, "GET", f"/sessions/{session_id}/messages")


@app.get("/api/v1/vibe/runs")
async def list_runs(
    session_id: str | None = Query(None),
    backtests_only: bool = Query(False),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    pool = get_pool()
    params: dict = {"limit": 200}
    if session_id:
        params["session_id"] = session_id
    runs = await pool.request(db, "GET", "/runs", params=params)

    # Filter to user's sessions (multi-tenant)
    owned = await _owned_session_ids(db, user)
    if not user.is_admin:
        runs = [r for r in runs if r.get("session_id") in owned]

    # Reports page shows only real backtests (runs that produced metrics).
    # A new chat alone must NOT create a report entry — only completed
    # backtests with metrics qualify. Chat-only runs stay visible in the
    # chat view; the reports page is for downloadables (PDF + strategy code).
    if backtests_only:
        runs = [
            r for r in runs
            if (r.get("total_return") is not None or r.get("sharpe") is not None)
        ]

    return runs


@app.get("/api/v1/vibe/swarm/presets")
async def swarm_presets(user: User = Depends(require_auth)):
    pool = get_pool()
    return await pool.request(None, "GET", "/swarm/presets")


@app.post("/api/v1/vibe/swarm/runs")
async def create_swarm_run(
    body: dict,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await _check_limit(db, user, "swarm")

    # Free-tier: 1 swarm coupon per week
    used_coupon = None
    if coupons_mod.uses_coupons(user):
        used_coupon = await coupons_mod.try_consume(db, user, "swarm", action="swarm-run")
        if used_coupon is None:
            refill = coupons_mod.next_weekly_utc()
            raise HTTPException(
                status_code=429,
                detail=f"کوپن سوارم هفتگی شما تمام شده. کوپن بعدی {refill.astimezone(coupons_mod._TEHRAN).strftime('%Y-%m-%d')} (دوشنبه ۰۰:۰۰ تهران) شارژ می‌شود.",
            )

    pool = get_pool()
    try:
        result = await pool.request(db, "POST", "/swarm/runs", json=body)
    except HTTPException:
        if used_coupon is not None:
            await coupons_mod.refund_coupon(db, user.id, used_coupon.action or "")
        raise
    run_id = result.get("id")

    if run_id:
        sr = SwarmRun(user_id=user.id, swarm_run_id=run_id, preset_name=body.get("preset_name", ""))
        db.add(sr)
        usage = await _get_usage(db, user.id)
        usage.swarm_runs += 1
        await db.commit()

    return result


@app.get("/api/v1/vibe/swarm/runs/{run_id}")
async def get_swarm_run(
    run_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SwarmRun).where(SwarmRun.swarm_run_id == run_id, SwarmRun.user_id == user.id)
    )
    if not result.scalar_one_or_none() and not user.is_admin:
        raise HTTPException(403, "این اجرا متعلق به شما نیست")

    pool = get_pool()
    return await pool.request(db, "GET", f"/swarm/runs/{run_id}")


@app.get("/api/v1/vibe/swarm/runs/{run_id}/pdf")
async def swarm_run_pdf(
    run_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Swarm report as PDF (ownership-checked) — same builder as the Telegram bot."""
    result = await db.execute(
        select(SwarmRun).where(SwarmRun.swarm_run_id == run_id, SwarmRun.user_id == user.id)
    )
    if not result.scalar_one_or_none() and not user.is_admin:
        raise HTTPException(403, "این اجرا متعلق به شما نیست")

    pool = get_pool()
    status = await pool.request(db, "GET", f"/swarm/runs/{run_id}")
    report = (status or {}).get("final_report", "")
    if not report:
        raise HTTPException(400, "این اجرا هنوز گزارشی ندارد (تکمیل نشده)")
    from app.pdf_report import build_swarm_pdf  # type: ignore

    try:
        preset = (status or {}).get("preset_name", "swarm")
        pdf_bytes = build_swarm_pdf(preset, preset, report, (status or {}).get("tasks", []))
    except Exception as exc:
        raise HTTPException(500, f"خطا در ساخت PDF: {exc}")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="swarm_{run_id[:16]}.pdf"',
            "Cache-Control": "no-store",
        },
    )


# ============================================================================
# Task Queue Routes (for heavy async tasks via workers)
# ============================================================================

class TaskRequest(BaseModel):
    task_type: str  # backtest, swarm, chat
    params: dict = {}

    @classmethod
    def _strip_internal(cls, params: dict | None) -> dict:
        """Remove _-prefixed internal keys (e.g. _coupon_id) from user input."""
        return {k: v for k, v in (params or {}).items() if not k.startswith("_")}


@app.post("/api/v1/tasks")
async def create_task(
    req: TaskRequest,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue a heavy task for background worker processing."""
    await _check_limit(db, user, req.task_type)

    # Free-tier coupon metering: chat/backtest → backtest coupon, swarm → weekly swarm coupon
    used_coupon = None
    if coupons_mod.uses_coupons(user):
        ckind = "swarm" if req.task_type == "swarm" else "backtest"
        used_coupon = await coupons_mod.try_consume(db, user, ckind, action=f"task:{req.task_type}")
        if used_coupon is None:
            if ckind == "swarm":
                refill = coupons_mod.next_weekly_utc().astimezone(coupons_mod._TEHRAN)
                raise HTTPException(429, f"کوپن سوارم هفتگی شما تمام شده. کوپن بعدی {refill.strftime('%Y-%m-%d')} (دوشنبه ۰۰:۰۰ تهران) شارژ می‌شود.")
            raise HTTPException(429, "کوپن بک‌تست شما به پایان رسید. ۲۴ ساعت دیگر (نیمه‌شب) کوپن جدید شارژ می‌شود.")

    task_id = str(uuid.uuid4())
    task = Task(
        task_id=task_id,
        user_id=user.id,
        task_type=req.task_type,
        status=TaskStatus.PENDING,
        params={**TaskRequest._strip_internal(req.params),
                # FR-014b: which coupon paid for this task (for platform-fault refunds)
                "_coupon_id": used_coupon.id if used_coupon else None},
    )
    db.add(task)

    # Update usage
    field = ACTION_FIELD_MAP.get(req.task_type)
    if field:
        usage = await _get_usage(db, user.id)
        setattr(usage, field, (getattr(usage, field, 0) or 0) + 1)

    await db.commit()

    # Smart dispatch: route to the least-loaded ready worker's dedicated
    # queue, honoring the user's plan tier for priority.
    from app.dispatch import get_dispatcher
    plan = getattr(user, "plan_tier", None) or "free"
    routing = await get_dispatcher().dispatch(
        f"task_{req.task_type}",
        (task_id, user.id, req.params),
        plan=plan,
        priority=getattr(user, "is_admin", False) and 1 or 0,
    )

    return {"task_id": task_id, "status": "pending", "arq_job_id": routing["job_id"],
            "worker": routing["worker"], "queue": routing["queue"]}


@app.get("/api/v1/tasks/{task_id}")
async def get_task_status(
    task_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Check task status and result."""
    result = await db.execute(
        select(Task).where(Task.task_id == task_id, Task.user_id == user.id)
    )
    task = result.scalar_one_or_none()
    if not task and not user.is_admin:
        raise HTTPException(404, "تسک یافت نشد")

    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "status": task.status.value,
        "worker": task.worker_name,
        "progress": task.progress,
        "result": task.result,
        "error": task.error_message,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


# ============================================================================
# Settings Proxy
# ============================================================================

@app.get("/api/v1/vibe/settings/llm")
async def get_llm_settings(user: User = Depends(require_auth)):
    if not user.is_admin:
        raise HTTPException(403, "فقط ادمین")
    pool = get_pool()
    return await pool.request(None, "GET", "/settings/llm")


@app.put("/api/v1/vibe/settings/llm")
async def update_llm_settings(
    body: dict,
    user: User = Depends(require_auth),
):
    if not user.is_admin:
        raise HTTPException(403, "فقط ادمین")
    pool = get_pool()
    return await pool.request(None, "PUT", "/settings/llm", json=body)


# ============================================================================
# Admin — dependency
# ============================================================================

async def _require_admin(user: User = Depends(require_auth)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "فقط ادمین")
    if not user.is_active:
        raise HTTPException(403, "حساب شما غیرفعال است")
    return user


# ============================================================================
# Admin — Overview / Monitoring
# ============================================================================

@app.get("/api/v1/admin/overview")
async def admin_overview(
    admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Single-call dashboard payload: KPIs + engine/worker health + recent logins."""
    total_users = (await db.execute(select(func.count()).select_from(User))).scalar() or 0
    active_subs = (await db.execute(
        select(func.count()).select_from(Subscription).where(
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.expires_at > _utcnow(),
        )
    )).scalar() or 0
    today0 = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    tasks_today = (await db.execute(select(func.count()).select_from(Task).where(Task.created_at >= today0))).scalar() or 0
    pending = (await db.execute(select(func.count()).select_from(Task).where(Task.status == TaskStatus.PENDING))).scalar() or 0
    running = (await db.execute(select(func.count()).select_from(Task).where(Task.status == TaskStatus.RUNNING))).scalar() or 0
    engines = (await db.execute(select(EngineNode).order_by(EngineNode.id))).scalars().all()
    workers = (await db.execute(select(WorkerNode).order_by(WorkerNode.name))).scalars().all()
    recent_logins = (await db.execute(select(LoginLog).order_by(LoginLog.created_at.desc()).limit(10))).scalars().all()
    by_type: dict[str, int] = {}
    for tt in ("backtest", "swarm", "chat"):
        by_type[tt] = (await db.execute(select(func.count()).select_from(Task).where(Task.task_type == tt))).scalar() or 0
    return {
        "kpi": {"total_users": total_users, "active_subs": active_subs, "tasks_today": tasks_today, "pending": pending, "running": running, "by_type": by_type},
        "engines": [{"id": n.id, "name": n.name, "url": n.url, "is_healthy": n.is_healthy, "is_enabled": n.is_enabled, "active": n.active_concurrency, "max_concurrency": n.max_concurrency, "last_health_detail": n.last_health_detail} for n in engines],
        "workers": [{"name": w.name, "status": w.status, "last_seen_at": w.last_seen_at.isoformat() if w.last_seen_at else None} for w in workers],
        "recent_logins": [{"id": r.id, "user_id": r.user_id, "ip": r.ip, "user_agent": (r.user_agent or "")[:120], "created_at": r.created_at.isoformat() if r.created_at else None} for r in recent_logins],
    }


@app.get("/api/v1/admin/monitor/summary")
async def admin_monitor_summary(
    admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    today0 = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    total = (await db.execute(select(func.count()).select_from(Task))).scalar() or 0
    today_c = (await db.execute(select(func.count()).select_from(Task).where(Task.created_at >= today0))).scalar() or 0
    pending = (await db.execute(select(func.count()).select_from(Task).where(Task.status == TaskStatus.PENDING))).scalar() or 0
    running = (await db.execute(select(func.count()).select_from(Task).where(Task.status == TaskStatus.RUNNING))).scalar() or 0
    completed = (await db.execute(select(func.count()).select_from(Task).where(Task.status == TaskStatus.COMPLETED))).scalar() or 0
    failed = (await db.execute(select(func.count()).select_from(Task).where(Task.status == TaskStatus.FAILED))).scalar() or 0
    engines = (await db.execute(select(EngineNode))).scalars().all()
    workers = (await db.execute(select(WorkerNode))).scalars().all()
    qdepth = None
    try:
        import redis.asyncio as _redis
        settings = get_settings()
        r = _redis.from_url(settings.REDIS_URL, decode_responses=True)
        import redis.asyncio as _r2
        keys = await _r2.from_url(settings.REDIS_URL, decode_responses=True).keys("arq:q:*")
        qdepth = sum(await r.zcard(k) for k in keys) if keys else 0  # type: ignore
        await r.aclose()
    except Exception:
        pass
    return {
        "tasks": {"total": total, "today": today_c, "pending": pending, "running": running, "completed": completed, "failed": failed, "queue_depth": qdepth},
        "engines": [{"id": n.id, "name": n.name, "is_healthy": n.is_healthy, "is_enabled": n.is_enabled, "active": n.active_concurrency} for n in engines],
        "workers": [{"name": w.name, "status": w.status, "last_seen_at": w.last_seen_at.isoformat() if w.last_seen_at else None} for w in workers],
    }

@app.get("/api/v1/admin/monitor/full")
async def admin_monitor_full(
    admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Rich monitoring: per-server / per-worker task stats + live queue + resources."""
    now = _utcnow()
    from datetime import timedelta as _td
    hour_ago_dt = now - _td(hours=1)

    # ---- tasks in the last hour, grouped per worker (who got what) ----
    rows = (await db.execute(
        select(Task.worker_name, Task.status, Task.task_type, Task.started_at, Task.completed_at, Task.error_message)
        .where(Task.created_at >= hour_ago_dt)
    )).all()

    workers = (await db.execute(select(WorkerNode))).scalars().all()
    servers = (await db.execute(select(ServerNode).order_by(ServerNode.id))).scalars().all()

    # worker -> server mapping: name prefix BEFORE first "-" after server name, or exact server name
    def worker_server(wname: str | None) -> str:
        if not wname:
            return "unassigned"
        for s in servers:
            if wname == s.name or wname.startswith(s.name + "-"):
                return s.name
        return "central"

    per_worker: dict = {}
    for wn, st, tt, sat, cat, emsg in rows:
        key = wn or "(none)"
        d = per_worker.setdefault(key, {"server": worker_server(wn), "total": 0, "completed": 0,
                                        "failed": 0, "running": 0, "pending": 0, "durations": []})
        d["total"] += 1
        if st == TaskStatus.COMPLETED:
            d["completed"] += 1
        elif st == TaskStatus.FAILED:
            d["failed"] += 1
        elif st == TaskStatus.RUNNING:
            d["running"] += 1
        elif st == TaskStatus.PENDING:
            d["pending"] += 1
        if sat and cat and (st == TaskStatus.COMPLETED or st == TaskStatus.FAILED):
            d["durations"].append((cat - sat).total_seconds())
    for d in per_worker.values():
        dur = d.pop("durations")
        d["avg_sec"] = round(sum(dur) / len(dur), 2) if dur else None
        d["success_pct"] = round(d["completed"] * 100 / d["total"], 1) if d["total"] else 0

    # ---- per-server rollup (tasks + worker counts + resources from heartbeat) ----
    per_server = []
    for s in servers:
        sworkers = [w for w in workers if w.name == s.name or w.name.startswith(s.name + "-")]
        online = [w for w in sworkers if w.status == "ready"]
        t_stats = [per_worker[w.name] for w in sworkers if w.name in per_worker]
        tasks_total = sum(t["total"] for t in t_stats)
        tasks_failed = sum(t["failed"] for t in t_stats)
        hi = s.host_info or {}
        per_server.append({
            "id": s.id, "name": s.name, "region": s.region,
            "status": s.status, "desired_workers": s.desired_workers,
            "online_workers": len(online), "registered_workers": len(sworkers),
            "tasks_1h": tasks_total, "failed_1h": tasks_failed,
            "cpu_limit": s.cpu_limit, "mem_limit": s.mem_limit,
            "host": {"cpu_count": hi.get("cpu_count"), "mem_total_gb": hi.get("mem_total_gb"),
                     "disk_total_gb": hi.get("disk_total_gb"), "disk_used_pct": hi.get("disk_used_pct"),
                     "hostname": hi.get("hostname")},
            "docker_ok": s.docker_ok, "last_heartbeat_at": s.last_heartbeat_at.isoformat() if s.last_heartbeat_at else None,
        })

    # central workers (not on any server)
    central = [w for w in workers if worker_server(w.name) == "central"]
    c_online = [w for w in central if w.status == "ready"]
    c_stats = [per_worker[w.name] for w in central if w.name in per_worker]
    per_server.insert(0, {
        "id": 0, "name": "central", "region": None,
        "status": "online" if c_online else "idle", "desired_workers": len(c_online),
        "online_workers": len(c_online), "registered_workers": len(central),
        "tasks_1h": sum(t["total"] for t in c_stats), "failed_1h": sum(t["failed"] for t in c_stats),
        "cpu_limit": None, "mem_limit": None, "host": {}, "docker_ok": True,
        "last_heartbeat_at": None,
    })

    # ---- 15-min bucket series (last hour) for throughput chart ----
    buckets: dict = {}
    for wn, st, tt, sat, cat, emsg in rows:
        ts = (sat or cat or now).timestamp()
        b = int(ts // 300) * 300
        buckets.setdefault(b, {"total": 0, "completed": 0, "failed": 0})
        buckets[b]["total"] += 1
        if st == TaskStatus.COMPLETED:
            buckets[b]["completed"] += 1
        elif st == TaskStatus.FAILED:
            buckets[b]["failed"] += 1
    series = [{"t": b, "total": v["total"], "completed": v["completed"], "failed": v["failed"]}
              for b, v in sorted(buckets.items())]

    # ---- recent task feed (last 15, newest first) ----
    recent = (await db.execute(
        select(Task).order_by(Task.created_at.desc()).limit(15)
    )).scalars().all()
    feed = [{
        "task_id": t.task_id, "type": t.task_type, "status": t.status.value if t.status else None,
        "worker": t.worker_name, "server": worker_server(t.worker_name),
        "user": t.user_id, "created_at": t.created_at.isoformat() if t.created_at else None,
        "duration_sec": round((t.completed_at - t.started_at).total_seconds(), 1) if (t.started_at and t.completed_at) else None,
        "error": (t.error_message or "")[:120] or None,
    } for t in recent]

    qdepth = 0
    dispatch_view: dict = {"workers": [], "stats": {}, "fallback_depth": 0}
    try:
        import redis.asyncio as _redis
        r = _redis.from_url(get_settings().REDIS_URL, decode_responses=True)
        for qk in await r.keys("arq:q:*"):
            try:
                qdepth += int(await r.zcard(qk))  # type: ignore
            except Exception:
                pass
        # ---- dispatcher live view: per-worker dedicated queue depth + inflight ----
        from app.dispatch import REGISTRY_KEY, STATS_KEY, FALLBACK_QUEUE, INFLIGHT_PREFIX
        # registry of live workers (name -> info)
        reg = {}
        for name, blob in (await r.hgetall(REGISTRY_KEY)).items():
            try:
                reg[name] = json.loads(blob)
            except Exception:
                pass
        # explicit fix: keys() may return str already (decode_responses=True)
        dworkers = []
        for wname in sorted(reg):
            queue_key = f"arq:q:{wname}"
            try:
                depth = int(await r.zcard(queue_key))
            except Exception:
                depth = 0
            try:
                inflight = int(await r.hlen(INFLIGHT_PREFIX + wname))
            except Exception:
                inflight = 0
            info = reg[wname] or {}
            t = await r.type(queue_key)
            if isinstance(t, bytes):
                t = t.decode()
            if t == "zset" or depth == 0:
                dworkers.append({
                    "name": wname,
                    "status": info.get("status", "ready"),
                    "concurrency": int(info.get("concurrency", 4)),
                    "queue_depth": depth,
                    "inflight": inflight,
                    "load": depth + inflight,
                })
        dstats = {k: int(v) for k, v in (await r.hgetall(STATS_KEY)).items()}
        try:
            fb_depth = int(await r.zcard(FALLBACK_QUEUE))
        except Exception:
            fb_depth = 0
        dispatch_view = {
            "workers": dworkers,
            "stats": dstats,
            "fallback_depth": fb_depth,
        }
        await r.aclose()
    except Exception:
        pass

    return {
        "tasks_1h": sum(d["total"] for d in per_worker.values()),
        "failed_1h": sum(d["failed"] for d in per_worker.values()),
        "queue_depth": qdepth,
        "per_server": per_server,
        "per_worker": [{"name": k, **v} for k, v in sorted(per_worker.items())],
        "series_15min": series,
        "recent": feed,
        "dispatcher": dispatch_view,
        "ts": now.isoformat(),
    }


@app.get("/api/v1/admin/fleet/metrics/live")
async def admin_fleet_live(admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    """US10 T028: live snapshot for 5s dashboard polling (cheap: Redis + light DB).

    Returns fleet totals + per-server load/queue/workers + live running tasks.
    """
    import redis.asyncio as _redis
    now = _utcnow()
    servers = (await db.execute(select(ServerNode).order_by(ServerNode.id))).scalars().all()
    live_tasks = (await db.execute(
        select(Task).where(Task.status.in_((TaskStatus.RUNNING, TaskStatus.PENDING))).order_by(Task.created_at.desc()).limit(30)
    )).scalars().all()
    # queue+inflight per worker from Redis
    qmap: dict[str, int] = {}
    try:
        r = _redis.from_url(get_settings().REDIS_URL, decode_responses=True)
        from app.dispatch import INFLIGHT_PREFIX
        for qk in await r.keys("arq:q:*"):
            qk_s = qk.decode() if isinstance(qk, bytes) else qk
            if ":" in qk_s.split("arq:q:", 1)[1]:
                continue
            wname = qk_s.split("arq:q:", 1)[1]
            try:
                qmap[wname] = int(await r.zcard(qk_s)) + int(await r.hlen(INFLIGHT_PREFIX + wname))
            except Exception:
                qmap[wname] = 0
        await r.aclose()
    except Exception:
        pass
    # worker → server map
    wrows = (await db.execute(select(WorkerNode))).scalars().all()
    wserver = {w.name: w.server_id for w in wrows}
    sid_name = {s.id: s.name for s in servers}
    out_servers = []
    for s in servers:
        loads = [qmap.get(w.name, 0) for w in wrows if w.server_id == s.id or w.name.startswith(s.name + "-")]
        out_servers.append({
            "id": s.id, "name": s.name, "status": s.status,
            "load": sum(loads), "queue": sum(loads),
            "workers": len([w for w in wrows if w.server_id == s.id]),
            "engine_healthy": s.engine_healthy, "capability_warning": s.capability_warning,
        })
    # central (workers with no server)
    central_load = sum(v for k, v in qmap.items() if k not in wserver or not wserver.get(k))
    tasks_live = [{
        "task_id": t.task_id[:8], "type": t.task_type,
        "status": t.status.value if t.status else None,
        "worker": t.worker_name,
        "server": sid_name.get(wserver.get(t.worker_name or ""), "central") if t.worker_name else "central",
        "elapsed_s": round((now - (t.started_at or t.created_at)).total_seconds()) if (t.started_at or t.created_at) else None,
    } for t in live_tasks]
    return {
        "ts": now.isoformat(),
        "fleet": {"load": sum(qmap.values()), "queue": sum(qmap.values()),
                  "workers": len(wrows), "tasks_running": sum(1 for t in live_tasks if t.status == TaskStatus.RUNNING)},
        "servers": out_servers,
        "central_load": central_load,
        "tasks_live": tasks_live,
    }


@app.get("/api/v1/admin/fleet/metrics/history")
async def admin_fleet_history(server_id: int, metric: str = "load",
                              hours: int = 72,
                              admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    """US10 T028: downsampled history (≤ ~500 points) for charts + mobile."""
    from datetime import timedelta as _td
    from app import metrics as metrics_mod
    now = _utcnow()
    pts = await metrics_mod.history(db, server_id, metric, now - _td(hours=hours), now, max_points=500)
    return {"server_id": server_id, "metric": metric, "hours": hours, "points": pts}


# ============================================================================
# Admin — Users (full CRUD + audit)
# ============================================================================

class AdminUserCreate(BaseModel):
    username: str
    password: str
    phone: str | None = None
    is_admin: bool = False
    is_active: bool = True
    plan_tier: str | None = None
    plan_days: int = 30


class AdminUserUpdate(BaseModel):
    username: str | None = None
    phone: str | None = None
    is_active: bool | None = None
    is_admin: bool | None = None


class AdminPasswordReset(BaseModel):
    new_password: str


@app.get("/api/v1/admin/users")
async def admin_list_users(
    q: str | None = Query(None, description="search username/phone"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
    if q:
        like = f"%{q}%"
        stmt = select(User).where((User.username.ilike(like)) | (User.phone.ilike(like))).order_by(User.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    out = []
    for u in rows:
        now = _utcnow()
        best = None
        for s in (u.subscriptions or []):
            if s.status == SubscriptionStatus.ACTIVE and s.expires_at and s.expires_at > now:
                if best is None or s.expires_at > best.expires_at:
                    best = s
        last_login = (await db.execute(select(LoginLog).where(LoginLog.user_id == u.id).order_by(LoginLog.created_at.desc()).limit(1))).scalar_one_or_none()
        out.append({
            "id": u.id, "username": u.username, "phone": u.phone, "telegram_id": u.telegram_id,
            "is_active": u.is_active, "is_admin": u.is_admin,
            "plan": (best.plan_tier.value if best else PlanTier.FREE.value),
            "plan_expires_at": best.expires_at.isoformat() if best and best.expires_at else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": last_login.created_at.isoformat() if last_login and last_login.created_at else None,
            "last_login_ip": last_login.ip if last_login else None,
        })
    return out


@app.post("/api/v1/admin/users")
async def admin_create_user(req: AdminUserCreate, admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    if (await db.execute(select(User).where(User.username == req.username))).scalar_one_or_none():
        raise HTTPException(400, "نام کاربری تکراری است")
    u = User(username=req.username, hashed_password=hash_password(req.password), phone=req.phone, is_admin=req.is_admin, is_active=req.is_active)
    db.add(u)
    await db.flush()
    if req.plan_tier and req.plan_tier != "free":
        db.add(Subscription(user_id=u.id, plan_tier=PlanTier(req.plan_tier), status=SubscriptionStatus.ACTIVE, expires_at=_utcnow() + timedelta(days=req.plan_days)))
    else:
        db.add(Subscription(user_id=u.id, plan_tier=PlanTier.FREE, status=SubscriptionStatus.ACTIVE, expires_at=_utcnow() + timedelta(days=365*10)))
    await db.commit()
    await db.refresh(u)
    return {"id": u.id, "username": u.username}


@app.get("/api/v1/admin/users/{user_id}")
async def admin_get_user(user_id: int, admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "کاربر یافت نشد")
    now = _utcnow()
    best = None
    for s in (u.subscriptions or []):
        if s.status == SubscriptionStatus.ACTIVE and s.expires_at and s.expires_at > now:
            if best is None or s.expires_at > best.expires_at:
                best = s
    subs = [{"id": s.id, "plan": s.plan_tier.value, "status": s.status.value if hasattr(s.status, "value") else str(s.status), "expires_at": s.expires_at.isoformat() if s.expires_at else None} for s in (u.subscriptions or [])]
    last_login = (await db.execute(select(LoginLog).where(LoginLog.user_id == u.id).order_by(LoginLog.created_at.desc()).limit(1))).scalar_one_or_none()
    return {"id": u.id, "username": u.username, "phone": u.phone, "telegram_id": u.telegram_id, "is_active": u.is_active, "is_admin": u.is_admin,
            "plan": (best.plan_tier.value if best else PlanTier.FREE.value), "plan_expires_at": best.expires_at.isoformat() if best and best.expires_at else None,
            "created_at": u.created_at.isoformat() if u.created_at else None, "last_login_at": last_login.created_at.isoformat() if last_login and last_login.created_at else None, "subscriptions": subs}


@app.put("/api/v1/admin/users/{user_id}")
async def admin_update_user(user_id: int, req: AdminUserUpdate, admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "کاربر یافت نشد")
    if req.username is not None and req.username != u.username:
        if (await db.execute(select(User).where(User.username == req.username))).scalar_one_or_none():
            raise HTTPException(400, "نام کاربری تکراری است")
        u.username = req.username
    if req.phone is not None:
        u.phone = req.phone
    if req.is_active is not None:
        if u.id == admin.id and req.is_active is False:
            raise HTTPException(400, "نمی‌توانید خودتان را غیرفعال کنید")
        u.is_active = req.is_active
    if req.is_admin is not None:
        u.is_admin = req.is_admin
    await db.commit()
    return {"ok": True}


@app.delete("/api/v1/admin/users/{user_id}")
async def admin_delete_user(user_id: int, admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    if user_id == admin.id:
        raise HTTPException(400, "نمی‌توانید خودتان را حذف کنید")
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "کاربر یافت نشد")
    from sqlalchemy import delete as _delete
    await db.execute(_delete(LoginLog).where(LoginLog.user_id == user_id))
    await db.execute(_delete(Payment).where(Payment.user_id == user_id))
    await db.execute(_delete(UsageLog).where(UsageLog.user_id == user_id))
    await db.execute(_delete(Task).where(Task.user_id == user_id))
    await db.execute(_delete(VibeSession).where(VibeSession.user_id == user_id))
    await db.execute(_delete(SwarmRun).where(SwarmRun.user_id == user_id))
    await db.execute(_delete(Subscription).where(Subscription.user_id == user_id))
    await db.delete(u)
    await db.commit()
    return {"ok": True}


@app.post("/api/v1/admin/users/{user_id}/reset-password")
async def admin_reset_password(user_id: int, req: AdminPasswordReset, admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "کاربر یافت نشد")
    if len(req.new_password) < 6:
        raise HTTPException(400, "رمز عبور باید حداقل ۶ کاراکتر باشد")
    u.hashed_password = hash_password(req.new_password)
    await db.commit()
    return {"ok": True}


@app.get("/api/v1/admin/login-logs")
async def admin_login_logs(
    user_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(LoginLog).order_by(LoginLog.created_at.desc()).limit(limit).offset(offset)
    if user_id:
        stmt = select(LoginLog).where(LoginLog.user_id == user_id).order_by(LoginLog.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return [{"id": r.id, "user_id": r.user_id, "ip": r.ip, "user_agent": (r.user_agent or "")[:160], "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]


@app.get("/api/v1/admin/tasks")
async def admin_list_tasks(
    admin: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Task).order_by(Task.created_at.desc()).limit(100))
    return result.scalars().all()


class TaskRefundIn(BaseModel):
    reason: str = "platform-fault"  # admin note (logged in coupon.action)


@app.post("/api/v1/admin/tasks/{task_id}/refund")
async def admin_refund_task(task_id: str, req: TaskRefundIn, admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    """FR-014b: refund the coupon consumed by a platform-faulted task.

    Only tasks in FAILED status with a recorded _coupon_id qualify, and only
    once (coupon flips back to active → second refund finds nothing to do).
    PENDING/RUNNING tasks are rejected — the worker may still consume them.
    """
    from shared.models import Coupon as _Coupon
    task = (await db.execute(select(Task).where(Task.task_id == task_id))).scalar_one_or_none()
    if not task:
        raise HTTPException(404, "تسک یافت نشد")
    if task.status != TaskStatus.FAILED:
        raise HTTPException(400, "فقط تسک ناموفق قابل برگشت است (تسک در حال اجرا/صف را نمی‌توان برگرداند)")
    coupon_id = (task.params or {}).get("_coupon_id")
    if not coupon_id:
        raise HTTPException(400, "این تسک با کوپن پرداخت نشده (یا پلن پولی)")
    c = (await db.execute(select(_Coupon).where(_Coupon.id == coupon_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "کوپن یافت نشد")
    if c.status != "used":
        return {"ok": True, "refunded": False, "detail": "کوپن قبلاً برگشته است"}
    # expired daily? extend to end of today so the refund is usable
    if c.expires_at is not None and c.expires_at <= _utcnow():
        c.expires_at = coupons_mod.tehran_midnight_utc()
    c.status = "active"
    c.used_at = None
    c.action = f"refund:{req.reason}:by-admin-{admin.id}"
    await db.commit()
    return {"ok": True, "refunded": True, "coupon_id": c.id, "kind": c.kind}


# ============================================================================
# Run Detail Proxy (full backtest report)
# ============================================================================

@app.get("/api/v1/vibe/runs/{run_id}")
async def get_run_detail(
    run_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    full: bool = Query(False, description="Include heavy series (price/indicators/CSVs)"),
):
    """Get backtest run detail — ownership-checked (multi-tenant).

    Default response is a LIGHT summary (~100KB): metrics, equity_curve,
    run_context, trade_log. Heavy fields (price_series, indicator_series,
    artifacts_*_csv, ...) are stripped unless ?full=true — the bot Telegram
    flow needs them for charts/tables, the web dashboard does not.
    """
    pool = get_pool()
    detail = await pool.request(db, "GET", f"/runs/{run_id}")

    # Privacy: the run must belong to one of this user's sessions
    run_session = (
        detail.get("session_id")
        or (detail.get("run_context") or {}).get("raw_context", {}).get("session_id")
    )
    if not user.is_admin:
        owned = await _owned_session_ids(db, user)
        if run_session and run_session not in owned:
            raise HTTPException(403, "این گزارش متعلق به شما نیست")
        if not run_session and not owned:
            raise HTTPException(403, "این گزارش متعلق به شما نیست")

    if not full:
        _HEAVY = (
            "artifacts", "artifacts_equity_csv", "artifacts_metrics_csv",
            "artifacts_positions_csv", "artifacts_target_positions_csv",
            "artifacts_trades_csv", "price_series", "indicator_series",
            "trade_markers", "run_logs", "planner_output", "strategy_spec",
            "rag_selection", "run_card", "validation", "run_directory",
            "llm_usage", "rebalance_notes", "risk_xray",
        )
        detail = {k: v for k, v in detail.items() if k not in _HEAVY}
        # Downsample equity for the web chart (browser only draws ~250 points)
        eq = detail.get("equity_curve")
        if isinstance(eq, list) and len(eq) > 250:
            step = len(eq) / 250
            detail["equity_curve"] = [eq[int(i * step)] for i in range(250)]
    return detail


# ============================================================================
# Alpha Zoo Proxy (browse / detail / bench)
# ============================================================================

@app.get("/api/v1/vibe/alpha/list")
async def alpha_list(
    zoo: str | None = Query(None),
    user: User = Depends(require_auth),
):
    pool = get_pool()
    params = {"zoo": zoo} if zoo else {}
    return await pool.request(None, "GET", "/alpha/list", params=params)


# ============================================================================
# Strategy Discovery Proxy (evidence-gated: which strategies are alive/dead)
# ============================================================================

@app.get("/api/v1/vibe/strategies")
async def strategies_list(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    source: str | None = Query(None),
    user: User = Depends(require_auth),
):
    """Unified strategy catalog (alpha_zoo + sdm) with evidence flags."""
    pool = get_pool()
    params: dict = {"limit": limit, "offset": offset}
    if source:
        params["source"] = source
    return await pool.request(None, "GET", "/strategies", params=params)


@app.get("/api/v1/vibe/strategies/query")
async def strategies_query(
    regime: str | None = Query(None),
    min_sharpe: float | None = Query(None),
    min_evidence_quality: str = Query("adequate"),
    min_trades: int = Query(10, ge=0),
    cost_feasible: bool = Query(True),
    limit: int = Query(10, ge=1, le=100),
    include_stale: bool = Query(False),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Evidence-gated recommendations — stale/weak rows excluded by default."""
    pool = get_pool()
    params: dict = {
        "min_evidence_quality": min_evidence_quality,
        "min_trades": min_trades,
        "cost_feasible": cost_feasible,
        "limit": limit,
        "include_stale": include_stale,
    }
    if regime:
        params["regime"] = regime
    if min_sharpe is not None:
        params["min_sharpe"] = min_sharpe
    return await pool.request(db, "GET", "/strategies/query", params=params)


@app.get("/api/v1/vibe/strategies/{strategy_id}/evidence")
async def strategy_evidence(
    strategy_id: str,
    regime: str | None = Query(None),
    user: User = Depends(require_auth),
):
    """Full per-regime evidence breakdown for one strategy (unfiltered)."""
    pool = get_pool()
    params = {"regime": regime} if regime else {}
    # strategy_id contains a colon (alpha_zoo:xxx) — path-safe, no slash
    return await pool.request(None, 
        "GET", f"/strategies/{strategy_id}/evidence", params=params
    )


@app.post("/api/v1/vibe/strategies/evidence/refresh")
async def strategies_refresh(
    body: dict,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Rebuild evidence cache from run artifacts.

    Body: {"runs": [{"strategy_id": "my-btc-macd", "run_dir": "<run_id>"}]} —
    run_dir accepts a run_id shorthand (resolved server-side). Ownership of
    every run is verified against this user's sessions first.
    """
    runs = body.get("runs") if isinstance(body, dict) else None
    if runs is not None:
        owned = await _owned_session_ids(db, user)
        for entry in runs if isinstance(runs, list) else []:
            if not isinstance(entry, dict):
                continue
            rd = str(entry.get("run_dir", ""))
            if rd and "/" not in rd and "\\" not in rd and not user.is_admin:
                # run_id shorthand — verify the run belongs to this user
                try:
                    detail = await get_run_detail(run_id=rd, user=user, db=db)
                    _ = detail
                except HTTPException:
                    raise HTTPException(
                        403, f"این گزارش متعلق به شما نیست: {rd}"
                    )
                # full ownership check done inside get_run_detail; also make
                # sure the session is actually owned (non-admin path)
                run_session = detail.get("session_id") or (
                    detail.get("run_context") or {}
                ).get("raw_context", {}).get("session_id")
                if run_session and run_session not in owned:
                    raise HTTPException(
                        403, f"این گزارش متعلق به شما نیست: {rd}"
                    )
    await _check_limit(db, user, "backtest")
    pool = get_pool()
    return await pool.request(db, 
        "POST", "/strategies/evidence/refresh", json=body
    )


@app.get("/api/v1/vibe/alpha/{alpha_id}")
async def alpha_detail(
    alpha_id: str,
    user: User = Depends(require_auth),
):
    pool = get_pool()
    return await pool.request(None, "GET", f"/alpha/{alpha_id}")


@app.post("/api/v1/vibe/alpha/bench")
async def alpha_bench(
    body: dict,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Start an alpha bench job. Checks quota."""
    await _check_limit(db, user, "backtest")
    pool = get_pool()
    result = await pool.request(db, "POST", "/alpha/bench", json=body)
    return result


@app.get("/api/v1/vibe/alpha/bench/{job_id}/stream")
async def alpha_bench_stream(
    job_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """SSE stream for alpha bench progress."""
    await db.close()
    settings = get_settings()
    url = f"{settings.VIBE_ENGINE_URL}/alpha/bench/{job_id}/stream"
    headers = {"Authorization": f"Bearer {settings.VIBE_ENGINE_API_KEY}"} if settings.VIBE_ENGINE_API_KEY else {}

    async def event_generator():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                async for line in resp.aiter_lines():
                    yield f"{line}\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ============================================================================
# Swarm presets with full detail (titles, agents, variables)
# ============================================================================

@app.get("/api/v1/vibe/swarm/runs")
async def list_swarm_runs(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    pool = get_pool()
    runs = await pool.request(db, "GET", "/swarm/runs")

    # Filter to user's swarm runs (multi-tenant)
    result = await db.execute(
        select(SwarmRun.swarm_run_id).where(SwarmRun.user_id == user.id)
    )
    owned = {row[0] for row in result.all()}
    if not user.is_admin:
        runs = [r for r in runs if r.get("id") in owned]
    return runs


# ============================================================================
# Session Hub (for bot chat history)
# ============================================================================

@app.get("/api/v1/vibe/sessions")
async def list_sessions(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List the user's chat sessions (multi-tenant: only owned)."""
    pool = get_pool()
    owned = await _owned_session_ids(db, user)
    if user.is_admin and not owned:
        return await pool.request(db, "GET", "/sessions")
    sessions = await pool.request(db, "GET", "/sessions")
    if not user.is_admin:
        sessions = [s for s in sessions if s.get("id") in owned or s.get("session_id") in owned]
    return sessions


@app.patch("/api/v1/vibe/sessions/{session_id}")
async def rename_session(
    session_id: str,
    body: dict,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Rename a chat session (ownership-checked; forwards to engine PATCH)."""
    await _require_owned_session(db, user, session_id)
    title = str(body.get("title", "")).strip()
    if not title or len(title) > 80:
        raise HTTPException(400, "عنوان گفتگو باید بین ۱ تا ۸۰ نویسه باشد")
    pool = get_pool()
    await pool.request(db, "PATCH", f"/sessions/{session_id}", json={"title": title})
    return {"status": "renamed", "session_id": session_id, "title": title}


@app.get("/api/v1/vibe/sessions/{session_id}/history")
async def session_history(
    session_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Full chat history of a session (ownership-checked)."""
    await _require_owned_session(db, user, session_id)
    pool = get_pool()
    return await pool.request(db, "GET", f"/sessions/{session_id}/messages")


# ============================================================================
# Web App (static dashboard + report chart/PDF)
# ============================================================================


# ============================================================================
# Server Fleet — one-line join + panel-controlled worker scaling
# ============================================================================

class ServerNodeIn(BaseModel):
    name: str
    region: str | None = None
    desired_workers: int = 1
    worker_concurrency: int = 4
    cpu_limit: str = "2.0"
    mem_limit: str = "2G"


class ServerProvisionIn(BaseModel):
    """Register a server + auto-provision it over SSH (US-09, FR-020)."""
    name: str
    ssh_host: str
    ssh_user: str = "root"
    auth_type: str = "password"  # password | key
    ssh_password: str | None = None
    ssh_key: str | None = None
    tailscale_ip: str | None = None
    region: str | None = None
    min_workers: int = 1
    max_workers: int = 8


@app.post("/api/v1/admin/fleet/servers")
async def admin_provision_server(req: ServerProvisionIn, admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    """Register a server + auto-provision a FULL node over SSH (US-09, FR-020/021).

    The SSH secret is encrypted before storage and never logged. The install
    runs as a background job; poll GET .../provision for live progress.
    """
    from shared.models import ProvisionJob as _ProvisionJob
    if req.auth_type not in ("password", "key"):
        raise HTTPException(400, "نوع احراز نامعتبر است")
    secret_plain = req.ssh_password if req.auth_type == "password" else req.ssh_key
    if not secret_plain:
        raise HTTPException(400, "رمز عبور یا کلید خصوصی لازم است")
    if (await db.execute(select(ServerNode).where(ServerNode.name == req.name))).scalar_one_or_none():
        raise HTTPException(400, "نام سرور تکراری است")
    s = ServerNode(
        name=req.name, region=req.region,
        join_token=secrets.token_urlsafe(24),
        desired_workers=req.min_workers,
        min_workers=req.min_workers, max_workers=req.max_workers,
        ssh_host=req.ssh_host, ssh_user=req.ssh_user,
        ssh_auth_type=req.auth_type, tailscale_ip=req.tailscale_ip,
        provision_state="running", provision_step="connect",
        status="pending",
    )
    db.add(s)
    await db.flush()  # need s.id for per-server key derivation
    try:
        ct, kid = crypto_mod.encrypt_secret(secret_plain, s.id)
    except RuntimeError:
        await db.rollback()
        raise HTTPException(500, "کلید رمزنگاری سرور تنظیم نشده (FLEET_MASTER_KEY)")
    finally:
        secret_plain = ""
    s.ssh_secret = ct
    s.ssh_key_id = kid
    job = _ProvisionJob(server_id=s.id, status="running", current_step="connect", steps=[], triggered_by=admin.id)
    db.add(job)
    await db.commit()
    await db.refresh(s)
    await db.refresh(job)

    def _bundle_url(server: ServerNode) -> str:
        base = (get_settings().PUBLIC_BASE_URL or str(request_url_base())).rstrip("/")
        return f"{base}/api/v1/node/{server.join_token}/bundle"

    asyncio.create_task(provision_mod.run_provision_job(job.id, _bundle_url))
    return {"id": s.id, "provision_job_id": job.id, "status": "running", "current_step": "connect"}


@app.get("/api/v1/admin/fleet/servers/{server_id}/provision")
async def admin_provision_status(server_id: int, admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    """Live provision progress for the panel (poll every ~3s during install)."""
    from shared.models import ProvisionJob as _ProvisionJob
    job = (await db.execute(
        select(_ProvisionJob).where(_ProvisionJob.server_id == server_id).order_by(_ProvisionJob.id.desc())
    )).scalars().first()
    if not job:
        raise HTTPException(404, "کاری برای این سرور ثبت نشده")
    return {"status": job.status, "current_step": job.current_step, "steps": job.steps or [],
            "job_id": job.id}


@app.post("/api/v1/admin/fleet/servers/{server_id}/provision/retry")
async def admin_provision_retry(server_id: int, admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    """Retry a failed provision from the failed step (successful steps are skipped)."""
    from shared.models import ProvisionJob as _ProvisionJob
    job = (await db.execute(
        select(_ProvisionJob).where(_ProvisionJob.server_id == server_id).order_by(_ProvisionJob.id.desc())
    )).scalars().first()
    if not job:
        raise HTTPException(404, "کاری برای این سرور ثبت نشده")
    if job.status == "running":
        raise HTTPException(409, "نصب در حال اجراست")
    if job.status == "ready":
        return {"ok": True, "status": "ready"}
    s = (await db.execute(select(ServerNode).where(ServerNode.id == server_id))).scalar_one_or_none()
    if not s or not s.ssh_secret:
        raise HTTPException(400, "مشخصات اتصال این سرور موجود نیست")
    await provision_mod.retry_provision_job(job, db)

    def _bundle_url(server: ServerNode) -> str:
        base = (get_settings().PUBLIC_BASE_URL or str(request_url_base())).rstrip("/")
        return f"{base}/api/v1/node/{server.join_token}/bundle"

    asyncio.create_task(provision_mod.run_provision_job(job.id, _bundle_url))
    return {"ok": True, "status": "running", "current_step": job.current_step}


@app.get("/api/v1/admin/servers")
async def admin_list_servers(admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ServerNode).order_by(ServerNode.id))).scalars().all()
    out = []
    for s in rows:
        workers = (await db.execute(select(WorkerNode).where(
            (WorkerNode.server_id == s.id) | (WorkerNode.name.ilike(f"{s.name}-%"))
        ))).scalars().all()
        online = [w for w in workers if w.status == "ready"]
        out.append({
            "id": s.id, "name": s.name, "region": s.region,
            "join_token": s.join_token,
            "desired_workers": s.desired_workers,
            "worker_concurrency": s.worker_concurrency,
            "cpu_limit": s.cpu_limit, "mem_limit": s.mem_limit,
            "status": s.status,
            "observed_workers": s.observed_workers,
            "online_workers": len(online),
            "worker_names": sorted(w.name for w in online),
            "docker_ok": s.docker_ok,
            "host_info": s.host_info,
            "last_heartbeat_at": s.last_heartbeat_at.isoformat() if s.last_heartbeat_at else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            # fleet US3: caps + capability + engine + provision at a glance
            "min_workers": s.min_workers, "max_workers": s.max_workers,
            "autoscale_enabled": s.autoscale_enabled,
            "engine_healthy": s.engine_healthy,
            "engine_url_local": s.engine_url_local,
            "capability": s.capability, "capability_warning": s.capability_warning,
            "provision_state": s.provision_state, "provision_step": s.provision_step,
            "tailscale_ip": s.tailscale_ip, "node_role": s.node_role,
            "has_ssh": bool(s.ssh_secret),
        })
    return out


class ServerManageIn(BaseModel):
    """US3 management: caps, autoscale, drain (one PATCH for the panel)."""
    min_workers: int | None = Field(None, ge=0, le=64)
    max_workers: int | None = Field(None, ge=1, le=64)
    autoscale_enabled: bool | None = None
    drain: bool | None = None  # True → draining (no new tasks); False → back online


@app.patch("/api/v1/admin/servers/{server_id}")
async def admin_manage_server(server_id: int, req: ServerManageIn, admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    """US3: caps + autoscale + drain from the panel, no SSH needed."""
    s = (await db.execute(select(ServerNode).where(ServerNode.id == server_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "سرور یافت نشد")
    if req.min_workers is not None:
        s.min_workers = req.min_workers
    if req.max_workers is not None:
        if req.max_workers < (req.min_workers if req.min_workers is not None else (s.min_workers or 0)):
            raise HTTPException(400, "سقف نمی‌تواند از کمینه کمتر باشد")
        s.max_workers = req.max_workers
    if req.autoscale_enabled is not None:
        s.autoscale_enabled = req.autoscale_enabled
    if req.drain is True:
        s.status = "draining"
    elif req.drain is False and s.status == "draining":
        s.status = "online"
    await db.commit()
    return {"ok": True, "id": s.id, "status": s.status,
            "min_workers": s.min_workers, "max_workers": s.max_workers,
            "autoscale_enabled": s.autoscale_enabled}


@app.get("/api/v1/admin/servers/{server_id}")
async def admin_server_detail(server_id: int, admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    """US3: full detail — capability bench, provision log, workers, engine."""
    from shared.models import ProvisionJob as _ProvisionJob
    s = (await db.execute(select(ServerNode).where(ServerNode.id == server_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "سرور یافت نشد")
    workers = (await db.execute(select(WorkerNode).where(
        (WorkerNode.server_id == s.id) | (WorkerNode.name.ilike(f"{s.name}-%"))
    ))).scalars().all()
    job = (await db.execute(
        select(_ProvisionJob).where(_ProvisionJob.server_id == s.id).order_by(_ProvisionJob.id.desc())
    )).scalars().first()
    return {
        "id": s.id, "name": s.name, "region": s.region, "status": s.status,
        "desired_workers": s.desired_workers, "observed_workers": s.observed_workers,
        "min_workers": s.min_workers, "max_workers": s.max_workers,
        "autoscale_enabled": s.autoscale_enabled,
        "worker_concurrency": s.worker_concurrency, "cpu_limit": s.cpu_limit, "mem_limit": s.mem_limit,
        "docker_ok": s.docker_ok, "host_info": s.host_info,
        "engine_healthy": s.engine_healthy, "engine_url_local": s.engine_url_local,
        "capability": s.capability, "capability_warning": s.capability_warning,
        "provision_state": s.provision_state, "provision_step": s.provision_step,
        "provision_log": s.provision_log or [],
        "tailscale_ip": s.tailscale_ip, "node_role": s.node_role, "has_ssh": bool(s.ssh_secret),
        "last_heartbeat_at": s.last_heartbeat_at.isoformat() if s.last_heartbeat_at else None,
        "workers": [{"name": w.name, "status": w.status,
                     "last_seen": w.last_seen_at.isoformat() if w.last_seen_at else None} for w in workers],
        "provision_job": {"id": job.id, "status": job.status, "steps": job.steps or []} if job else None,
    }


@app.post("/api/v1/admin/servers/{server_id}/token/rotate")
async def admin_rotate_join_token(server_id: int, admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    """FR-011: revoke + regenerate a server's join token (old token dies immediately)."""
    s = (await db.execute(select(ServerNode).where(ServerNode.id == server_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "سرور یافت نشد")
    s.join_token = secrets.token_urlsafe(24)
    await db.commit()
    return {"ok": True, "id": s.id, "join_token": s.join_token}


@app.post("/api/v1/admin/servers")
async def admin_create_server(req: ServerNodeIn, admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    if (await db.execute(select(ServerNode).where(ServerNode.name == req.name))).scalar_one_or_none():
        raise HTTPException(400, "نام سرور تکراری است")
    s = ServerNode(
        name=req.name, region=req.region,
        join_token=secrets.token_urlsafe(24),
        desired_workers=req.desired_workers,
        worker_concurrency=req.worker_concurrency,
        cpu_limit=req.cpu_limit, mem_limit=req.mem_limit,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return {"id": s.id, "name": s.name, "join_token": s.join_token}


@app.delete("/api/v1/admin/servers/{server_id}")
async def admin_delete_server(server_id: int, admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    s = (await db.execute(select(ServerNode).where(ServerNode.id == server_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "سرور یافت نشد")
    s.desired_workers = 0
    s.status = "decommissioned"
    await db.commit()
    await db.delete(s)
    await db.commit()
    return {"ok": True}


class ServerScale(BaseModel):
    desired_workers: int = Field(..., ge=0, le=64)
    worker_concurrency: int | None = Field(None, ge=1, le=64)
    cpu_limit: str | None = None
    mem_limit: str | None = None


@app.post("/api/v1/admin/servers/{server_id}/scale")
async def admin_scale_server(server_id: int, req: ServerScale, admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    """Set desired worker count / per-worker limits — the node agent applies it within ~10s."""
    s = (await db.execute(select(ServerNode).where(ServerNode.id == server_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "سرور یافت نشد")
    s.desired_workers = req.desired_workers
    if req.worker_concurrency:
        s.worker_concurrency = req.worker_concurrency
    if req.cpu_limit:
        s.cpu_limit = req.cpu_limit
    if req.mem_limit:
        s.mem_limit = req.mem_limit
    await db.commit()
    return {"ok": True, "desired_workers": s.desired_workers}


@app.get("/install/{token}", response_class=PlainTextResponse)
async def node_install_script(token: str, db: AsyncSession = Depends(get_db)):
    """One-line installer served to the new server — token identifies the server row."""
    s = (await db.execute(select(ServerNode).where(ServerNode.join_token == token))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "توکن نامعتبر است")
    settings = get_settings()
    base = settings.PUBLIC_BASE_URL or str(request_url_base())
    name = s.name
    script = f"""#!/usr/bin/env bash
set -euo pipefail
# ============================================================================
# Vibe-Trading SaaS — node installer for server "{name}"
# Installs ALL requirements automatically (docker engine + compose plugin,
# curl, tar, git), downloads the node bundle and starts the agent.
# The agent registers this server with the panel and brings up its workers.
# ============================================================================
export VT_JOIN_TOKEN="{token}"
export VT_CONTROL_URL="{base}"
export VT_SERVER_NAME="{name}"
NODE_DIR=/opt/vibe-node

say() {{ echo -e "\033[1;32m[vibe]\033[0m $*"; }}
err() {{ echo -e "\033[1;31m[vibe]\033[0m $*" >&2; exit 1; }}

say "1/5 checking control plane connectivity…"
curl -fsSL -o /dev/null "$VT_CONTROL_URL/api/v1/node/$VT_JOIN_TOKEN/state" || err "cannot reach $VT_CONTROL_URL or token invalid"

say "2/5 installing base requirements (curl tar git)…"
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y -qq curl ca-certificates tar git >/dev/null
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y -q curl tar git >/dev/null
elif command -v yum >/dev/null 2>&1; then
  yum install -y -q curl tar git >/dev/null
fi

say "3/5 installing Docker Engine + Compose plugin (skipped if present)…"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh || err "docker install failed"
fi
if ! docker compose version >/dev/null 2>&1; then
  err "docker compose plugin missing — install docker-compose-plugin manually"
fi
systemctl enable --now docker >/dev/null 2>&1 || service docker start >/dev/null 2>&1 || true

say "4/5 downloading node bundle (agent + worker + compose)…"
mkdir -p "$NODE_DIR" && cd "$NODE_DIR"
curl -fsSL "$VT_CONTROL_URL/api/v1/node/$VT_JOIN_TOKEN/bundle" -o node.tar.gz
tar xzf node.tar.gz

say "5/5 starting node agent for '{name}'…"
docker compose -p vibe-node -f docker-compose.node.yml up -d --build --quiet-pull agent
say "done. agent is joining the panel now — server '{name}' + workers appear in ~30s."
say "manage this server's worker count from the panel (Nodes → Servers, +/− buttons)."
"""
    return PlainTextResponse(script, media_type="text/x-shellscript")


def request_url_base() -> str:
    # best-effort public base (host header + port); settings override wins
    return "http://206.245.166.14:9001"


@app.get("/api/v1/node/{token}/bundle")
async def node_bundle(token: str, db: AsyncSession = Depends(get_db)):
    """Tarball of agent/ + worker/ + shared/ + compose for the joining server."""
    import io, tarfile
    from pathlib import Path as _P
    # repo layout differs local vs docker image: find dir containing docker-compose.node.yml
    here = _P(__file__).resolve()
    candidates = [here.parents[2], _P("/app/worker_bundle"), _P("/app"), _P("/repo")]
    root = next((c for c in candidates if (c / "docker-compose.node.yml").exists()), candidates[0])
    s = (await db.execute(select(ServerNode).where(ServerNode.join_token == token))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "توکن نامعتبر است")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for sub, names in {
            "agent": ["app/main.py", "Dockerfile"],
            "worker": ["Dockerfile"],
            ".": ["docker-compose.node.yml"],
        }.items():
            for n in names:
                fp = root / sub / n
                if fp.exists():
                    tar.add(fp, arcname=f"{sub}/{n}" if sub != "." else n)
        # worker sources + shared
        for fp in (root / "worker" / "app").glob("*.py"):
            tar.add(fp, arcname=f"worker/app/{fp.name}")
        for fp in (root / "shared").glob("*.py"):
            tar.add(fp, arcname=f"shared/{fp.name}")
        # requirements files
        for f in ("worker/requirements.txt", "agent/requirements.txt"):
            fp = root / f
            if fp.exists():
                tar.add(fp, arcname=f)
        # .env placeholder — agent rewrites it from control-plane state anyway
        env = f"VT_JOIN_TOKEN={s.join_token}\nVT_CONTROL_URL={request_url_base()}\nVT_SERVER_NAME={s.name}\n"
        data = env.encode()
        ti = tarfile.TarInfo(".env")
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/gzip", headers={"Content-Disposition": f'attachment; filename="node.tar.gz"'})


@app.get("/api/v1/node/{token}/state")
async def node_state(token: str, db: AsyncSession = Depends(get_db)):
    """Agent pulls desired state (worker count + limits) + effective env here."""
    s = (await db.execute(select(ServerNode).where(ServerNode.join_token == token))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "توکن نامعتبر است")
    settings = get_settings()
    return {
        "server": s.name,
        "desired_workers": s.desired_workers,
        "worker_concurrency": s.worker_concurrency,
        "cpu_limit": s.cpu_limit,
        "mem_limit": s.mem_limit,
        "redis_url": settings.REDIS_URL_PUBLIC or settings.REDIS_URL,
        "database_url": settings.DATABASE_URL_PUBLIC or "",
        "engine_url": settings.VIBE_ENGINE_URL_PUBLIC or settings.VIBE_ENGINE_URL,
        "engine_api_key": settings.VIBE_ENGINE_API_KEY if settings.VIBE_NODE_SHARE_ENGINE_KEY else "",
        "workers_epoch": s.worker_epoch,
    }


@app.post("/api/v1/node/{token}/heartbeat")
async def node_heartbeat(token: str, body: dict, db: AsyncSession = Depends(get_db)):
    s = (await db.execute(select(ServerNode).where(ServerNode.join_token == token))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "توکن نامعتبر است")
    s.status = "online"
    s.observed_workers = int(body.get("observed_workers", 0) or 0)
    s.docker_ok = bool(body.get("docker_ok", False))
    s.host_info = body.get("host_info") or {}
    s.last_heartbeat_at = _utcnow()
    # fleet-update convergence: which worker bundle epoch this server runs
    if body.get("workers_epoch"):
        s.workers_epoch_reported = int(body["workers_epoch"])
    await db.commit()
    return {"ok": True}


class FleetMirrorIn(BaseModel):
    """Run artifacts mirrored from a remote full node (fleet T023)."""
    run_id: str
    node: str = ""
    metrics: dict = {}
    artifacts: dict = {}


@app.post("/api/v1/fleet/mirror")
async def fleet_mirror(request: Request, body: FleetMirrorIn, db: AsyncSession = Depends(get_db)):
    """Receive a completed run's small artifacts from a remote node worker.

    Auth: the posting worker proves node membership via the node's
    join_token in the X-Node-Token header. Best-effort by design.
    Full files (PDF) are rebuilt centrally on demand like today.
    """
    token = request.headers.get("x-node-token", "")
    if token:
        s = (await db.execute(select(ServerNode).where(ServerNode.join_token == token))).scalar_one_or_none()
        if not s:
            raise HTTPException(404, "توکن گره نامعتبر است")
    # without a token we still accept (metrics-only mirror from center-local
    # dev setups) — the run_id itself is the idempotency key downstream.
    return {"ok": True, "run_id": body.run_id, "node": body.node,
            "mirrored_metrics": list((body.metrics or {}).keys())}


# ============================================================================
# Fleet Updater — one-click core update (engine + workers on all servers)
# ============================================================================

class FleetUpdateTrigger(BaseModel):
    include_platform: bool = False


def _require_updater_token(token: str) -> None:
    settings = get_settings()
    if not settings.VT_UPDATER_TOKEN or token != settings.VT_UPDATER_TOKEN:
        raise HTTPException(401, "updater token invalid")


@app.get("/api/v1/updater/poll")
async def updater_poll(token: str, db: AsyncSession = Depends(get_db)):
    """Updater service claims the oldest pending job (atomic pending→running)."""
    _require_updater_token(token)
    row = (await db.execute(
        select(FleetUpdate).where(FleetUpdate.status == "pending")
        .order_by(FleetUpdate.id).limit(1).with_for_update(skip_locked=True)
    )).scalar_one_or_none()
    if not row:
        return None
    row.status = "running"
    row.step = "claimed"
    row.started_at = _utcnow()
    await db.commit()
    return {
        "id": row.id,
        "include_platform": row.include_platform,
        "scope": row.scope,
    }


@app.post("/api/v1/updater/report")
async def updater_report(body: dict, db: AsyncSession = Depends(get_db)):
    """Updater streams progress/log lines/terminal status here."""
    _require_updater_token(body.get("token", ""))
    job = (await db.execute(select(FleetUpdate).where(FleetUpdate.id == int(body["job_id"])))).scalar_one_or_none()
    if not job:
        raise HTTPException(404, "job not found")
    if body.get("step"):
        job.step = str(body["step"])[:64]
    if body.get("line"):
        entries = list(job.log or [])
        entries.append({"ts": _utcnow().isoformat(), "line": str(body["line"])[:500]})
        job.log = entries[-200:]
    if body.get("to_commit"):
        job.to_commit = str(body["to_commit"])[:64]
    if body.get("from_commit"):
        job.from_commit = str(body["from_commit"])[:64]
    if body.get("changed") is not None:
        job.changed = bool(body["changed"])
    if body.get("error"):
        job.error = str(body["error"])[:2000]
    status = body.get("status")
    if status:
        job.status = str(status)
        if status in ("success", "failed", "rolled_back", "up_to_date"):
            job.finished_at = _utcnow()
            # On successful engine update: bump worker epoch on ALL servers so
            # every agent rebuilds its workers from the fresh bundle. Central
            # server's own worker is rebuilt by the updater's platform step or
            # manually; remote agents converge within ~10s via state.
            if status == "success" and job.changed:
                epoch = int(_utcnow().timestamp())
                job.workers_epoch = epoch
                await db.execute(
                    _sa_update(ServerNode).values(worker_epoch=epoch)
                )
                # central worker (this host) — bump a setting so its next
                # rebuild is attributable; the updater itself rebuilds it
                # in include_platform mode, else admin rebuilds on deploy.
    await db.commit()
    return {"ok": True}


@app.post("/api/v1/admin/fleet/update")
async def admin_fleet_update_trigger(req: FleetUpdateTrigger, admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    """One-click: update engine core to latest upstream + roll out to workers."""
    active = (await db.execute(
        select(FleetUpdate).where(FleetUpdate.status.in_(["pending", "running"]))
    )).scalar_one_or_none()
    if active:
        raise HTTPException(409, f"یک آپدیت در حال اجراست (#{active.id})")
    job = FleetUpdate(
        include_platform=req.include_platform,
        scope="engine+platform" if req.include_platform else "engine",
        triggered_by=admin.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return {"id": job.id, "status": job.status, "scope": job.scope}


@app.get("/api/v1/admin/fleet/update/status")
async def admin_fleet_update_status(admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    """Current/last update job + engine version info + server convergence."""
    job = (await db.execute(
        select(FleetUpdate).order_by(FleetUpdate.id.desc()).limit(1)
    )).scalar_one_or_none()
    servers = (await db.execute(select(ServerNode).order_by(ServerNode.id))).scalars().all()
    return {
        "job": {
            "id": job.id, "status": job.status, "step": job.step, "scope": job.scope,
            "from_commit": job.from_commit, "to_commit": job.to_commit,
            "changed": job.changed, "log": job.log or [],
            "error": job.error,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "created_at": job.created_at.isoformat() if job.created_at else None,
        } if job else None,
        "servers": [
            {
                "id": s.id, "name": s.name, "status": s.status,
                "worker_epoch": s.worker_epoch,
                "workers_epoch_reported": s.workers_epoch_reported,
                "converged": s.workers_epoch_reported >= s.worker_epoch,
                "observed_workers": s.observed_workers,
            } for s in servers
        ],
    }


# ============================================================================
# Admin — Engine Fleet / Worker Registry (multi-server management)
# ============================================================================

class EngineNodeIn(BaseModel):
    name: str
    url: str
    api_key: str | None = None
    region: str | None = None
    max_concurrency: int = 10
    is_enabled: bool = True


@app.get("/api/v1/admin/fleet")
async def admin_fleet(user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    if not user.is_admin:
        raise HTTPException(403, "فقط ادمین")
    nodes = (await db.execute(select(EngineNode).order_by(EngineNode.id))).scalars().all()
    return [
        {
            "id": n.id, "name": n.name, "url": n.url,
            "region": n.region, "max_concurrency": n.max_concurrency,
            "active": n.active_concurrency, "is_enabled": n.is_enabled,
            "is_healthy": n.is_healthy, "fail_count": n.health_fail_count,
            "last_health_at": n.last_health_at.isoformat() if n.last_health_at else None,
            "last_health_detail": n.last_health_detail,
        } for n in nodes
    ]


@app.post("/api/v1/admin/fleet")
async def admin_fleet_add(req: EngineNodeIn, user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Add a new engine server (any host:port running Vibe-Trading)."""
    if not user.is_admin:
        raise HTTPException(403, "فقط ادمین")
    exists = (await db.execute(select(EngineNode).where(EngineNode.name == req.name))).scalar_one_or_none()
    if exists:
        raise HTTPException(400, "نام تکراری است")
    node = EngineNode(
        name=req.name, url=req.url.rstrip("/"), api_key=req.api_key,
        region=req.region, max_concurrency=req.max_concurrency,
        is_enabled=req.is_enabled,
    )
    # immediate health probe before accepting
    key = req.api_key or get_settings().VIBE_ENGINE_API_KEY
    ok, detail = False, ""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"{node.url.rstrip('/')}/health",
                                    headers={"Authorization": f"Bearer {key}"} if key else {})
        ok = resp.status_code == 200
        detail = f"HTTP {resp.status_code}"
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:120]}"
    node.is_healthy = ok
    node.last_health_at = _utcnow()
    node.last_health_detail = detail
    db.add(node)
    await db.commit()
    return {"id": node.id, "name": node.name, "healthy": ok, "detail": detail}


@app.put("/api/v1/admin/fleet/{node_id}")
async def admin_fleet_update(node_id: int, req: EngineNodeIn, user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    if not user.is_admin:
        raise HTTPException(403, "فقط ادمین")
    node = (await db.execute(select(EngineNode).where(EngineNode.id == node_id))).scalar_one_or_none()
    if not node:
        raise HTTPException(404, "گره یافت نشد")
    node.name = req.name
    node.url = req.url.rstrip("/")
    if req.api_key is not None:
        node.api_key = req.api_key
    node.region = req.region
    node.max_concurrency = req.max_concurrency
    node.is_enabled = req.is_enabled
    await db.commit()
    return {"ok": True}


@app.delete("/api/v1/admin/fleet/{node_id}")
async def admin_fleet_delete(node_id: int, user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    if not user.is_admin:
        raise HTTPException(403, "فقط ادمین")
    node = (await db.execute(select(EngineNode).where(EngineNode.id == node_id))).scalar_one_or_none()
    if not node:
        raise HTTPException(404, "گره یافت نشد")
    await db.delete(node)
    await db.commit()
    return {"ok": True}


@app.post("/api/v1/admin/fleet/{node_id}/health")
async def admin_fleet_recheck(node_id: int, user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Force an immediate health probe of one node."""
    if not user.is_admin:
        raise HTTPException(403, "فقط ادمین")
    node = (await db.execute(select(EngineNode).where(EngineNode.id == node_id))).scalar_one_or_none()
    if not node:
        raise HTTPException(404, "گره یافت نشد")
    pool = get_pool()
    tmp = EngineNode(
        id=node.id, name=node.name, url=node.url, api_key=node.api_key,
        is_enabled=True,
    )
    # reuse health_check_all on a mini-query
    settings = get_settings()
    key = node.api_key or settings.VIBE_ENGINE_API_KEY
    ok, detail = False, ""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"{node.url.rstrip('/')}/health",
                                    headers={"Authorization": f"Bearer {key}"} if key else {})
        ok = resp.status_code == 200
        detail = f"HTTP {resp.status_code} {resp.text[:160]}"
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:160]}"
    if ok:
        node.health_fail_count = 0
        node.is_healthy = True
    else:
        node.health_fail_count = (node.health_fail_count or 0) + 1
        if node.health_fail_count >= 2:
            node.is_healthy = False
    node.last_health_at = _utcnow()
    node.last_health_detail = detail[:500]
    await db.commit()
    return {"id": node.id, "name": node.name, "healthy": ok, "detail": detail}


@app.get("/api/v1/admin/workers")
async def admin_workers(user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Live worker registry (DB mirror of Redis + gateway scan)."""
    if not user.is_admin:
        raise HTTPException(403, "فقط ادمین")
    rows = (await db.execute(select(WorkerNode).order_by(WorkerNode.name))).scalars().all()
    return [
        {
            "name": w.name, "status": w.status,
            "last_seen_at": w.last_seen_at.isoformat() if w.last_seen_at else None,
            "info": w.info,
        } for w in rows
    ]


# ============================================================================
# Admin — Subscriptions & Users management
# ============================================================================

class PlanGrant(BaseModel):
    plan_tier: str       # basic | pro | enterprise
    days: int = 30


@app.post("/api/v1/admin/users/{user_id}/grant")
async def admin_grant_plan(user_id: int, req: PlanGrant, user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Manually grant/extend a user's subscription (admin)."""
    if not user.is_admin:
        raise HTTPException(403, "فقط ادمین")
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "کاربر یافت نشد")
    tier = PlanTier(req.plan_tier)
    # find current active sub of that tier to extend, else create
    now = _utcnow()
    sub = (await db.execute(
        select(Subscription).where(
            Subscription.user_id == target.id,
            Subscription.plan_tier == tier,
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.expires_at > now,
        ).order_by(Subscription.expires_at.desc())
    )).scalar_one_or_none()
    base = sub.expires_at if sub else now
    if sub:
        sub.expires_at = base + timedelta(days=req.days)
    else:
        db.add(Subscription(
            user_id=target.id, plan_tier=tier,
            status=SubscriptionStatus.ACTIVE,
            expires_at=base + timedelta(days=req.days),
        ))
    await db.commit()
    return {"ok": True, "plan": tier.value, "expires": (base + timedelta(days=req.days)).isoformat()}


@app.put("/api/v1/admin/users/{user_id}/toggle")
async def admin_toggle_user(user_id: int, user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    if not user.is_admin:
        raise HTTPException(403, "فقط ادمین")
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "کاربر یافت نشد")
    if target.id == user.id:
        raise HTTPException(400, "نمی‌توانید خودتان را غیرفعال کنید")
    target.is_active = not target.is_active
    await db.commit()
    return {"ok": True, "is_active": target.is_active}


# ============================================================================
# Payments — IDPay (create → gateway redirect → callback → activate plan)
# ============================================================================

PLAN_PRICES = {
    PlanTier.BASIC: 299_000,
    PlanTier.PRO: 799_000,
    PlanTier.ENTERPRISE: 1_999_000,
}

PLAN_DAYS = {PlanTier.BASIC: 30, PlanTier.PRO: 30, PlanTier.ENTERPRISE: 30}


class PaymentRequest(BaseModel):
    plan_tier: str  # basic | pro | enterprise


def _idpay_headers():
    settings = get_settings()
    if not settings.IDPAY_API_KEY:
        return None
    return {"X-API-KEY": settings.IDPAY_API_KEY, "Content-Type": "application/json"}


@app.post("/api/v1/payments/create")
async def payment_create(req: PaymentRequest, user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Create an IDPay payment and return the payment link."""
    tier = PlanTier(req.plan_tier) if req.plan_tier != "free" else None
    if tier is None or tier == PlanTier.FREE or tier not in PLAN_PRICES:
        raise HTTPException(400, "پلن نامعتبر")
    amount = PLAN_PRICES[tier]
    pay = Payment(user_id=user.id, plan_tier=tier, amount=amount)
    db.add(pay)
    await db.commit()

    headers = _idpay_headers()
    if not headers:
        raise HTTPException(503, "درگاه پرداخت تنظیم نشده (IDPAY_API_KEY خالی است) — با پشتیبانی تماس بگیرید یا از ادمین بخواهید پلن را دستی فعال کند")

    settings = get_settings()
    cb = settings.PAYMENT_CALLBACK_URL or f"{settings.PUBLIC_BASE_URL}/api/v1/payments/callback"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post("https://api.idpay.ir/v1.1/payment", headers=headers, json={
                "order_id": f"pay-{pay.id}",
                "amount": amount,  # Tomans
                "callback": cb,
            })
        data = resp.json()
    except Exception as exc:
        pay.status = "failed"
        await db.commit()
        raise HTTPException(502, f"خطای درگاه: {exc}")

    if str(data.get("status")) not in ("100", "1"):
        pay.status = "failed"
        pay.gateway_ref = str(data.get("error_message", ""))[:200]
        await db.commit()
        raise HTTPException(502, f"درگاه پرداخت خطا داد: {data.get('error_message') or data}")

    pay.authority = str(data.get("id", ""))
    pay.gateway_ref = str(data.get("link", ""))[:250]
    await db.commit()
    return {"payment_id": pay.id, "link": data.get("link"), "authority": pay.authority, "amount": amount}


@app.get("/api/v1/payments/callback")
async def payment_callback(
    id: str = Query(...), order_id: str = Query(...),
    track_id: str = Query(...), status: str = Query(...),
    amount: str = Query(""),
):
    """IDPay redirects the user here after payment; then verify server-side."""
    if status not in ("100", "2", "3", "10"):  # 100/2/3/10 = paid-ish states on return
        return Response(
            content=f"<html dir='rtl'><body style='font-family:Vazir,sans-serif;text-align:center;padding-top:60px'><h2>❌ پرداخت ناموفق بود (کد {status})</h2></body></html>",
            media_type="text/html",
        )
    # verify via IDPay verify API
    headers = _idpay_headers()
    if not headers:
        return Response(content="<h2>درگاه تنظیم نشده</h2>", media_type="text/html")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post("https://api.idpay.ir/v1.1/payment/verify", headers=headers, json={
                "id": id, "order_id": order_id,
            })
        data = resp.json()
    except Exception as exc:
        return Response(content=f"<h2>خطای تأیید: {exc}</h2>", media_type="text/html")

    ok_verify = str(data.get("status")) == "100" or (str(data.get("status")) == "101" and data.get("payment"))
    pay_id = int(order_id.split("-")[1]) if order_id.startswith("pay-") else None
    if not pay_id:
        return Response(content="<h2>سفارش نامعتبر</h2>", media_type="text/html")

    from shared.models import _session_factory
    async with _session_factory() as db:
        pay = (await db.execute(select(Payment).where(Payment.id == pay_id))).scalar_one_or_none()
        if not pay:
            return Response(content="<h2>پرداخت یافت نشد</h2>", media_type="text/html")
        if ok_verify:
            if pay.status != "paid":
                pay.status = "paid"
                pay.payment_ref = str(data.get("track_id", ""))[:250]
                pay.verified_at = _utcnow()
                days = PLAN_DAYS[PlanTier(pay.plan_tier.value if hasattr(pay.plan_tier, 'value') else pay.plan_tier)]
                tier = PlanTier(pay.plan_tier.value if hasattr(pay.plan_tier, 'value') else pay.plan_tier)
                sub = (await db.execute(
                    select(Subscription).where(
                        Subscription.user_id == pay.user_id,
                        Subscription.plan_tier == tier,
                        Subscription.status == SubscriptionStatus.ACTIVE,
                        Subscription.expires_at > _utcnow(),
                    )
                )).scalar_one_or_none()
                base = sub.expires_at if sub else _utcnow()
                if sub:
                    sub.expires_at = base + timedelta(days=days)
                else:
                    db.add(Subscription(
                        user_id=pay.user_id, plan_tier=tier,
                        status=SubscriptionStatus.ACTIVE,
                        expires_at=base + timedelta(days=days),
                    ))
                await db.commit()
            html = "<html dir='rtl'><body style='font-family:Vazir,sans-serif;text-align:center;padding-top:60px'><h2>✅ پرداخت موفق! اشتراک شما فعال شد.</h2><p>می‌توانید این صفحه را ببندید.</p></body></html>"
        else:
            pay.status = "failed"
            await db.commit()
            html = f"<html dir='rtl'><body style='font-family:Vazir,sans-serif;text-align:center;padding-top:60px'><h2>❌ تأیید پرداخت ناموفق</h2><p>{data.get('status') or ''}</p></body></html>"
    return Response(content=html, media_type="text/html")


@app.get("/api/v1/payments/plans")
async def payment_plans():
    """Public plan catalog with prices (Toman) for bot/webapp purchase UI."""
    return [
        {"tier": t.value, "price": price, "days": PLAN_DAYS[t]}
        for t, price in PLAN_PRICES.items()
    ]


@app.get("/api/v1/payments/history")
async def payment_history(user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Payment).where(Payment.user_id == user.id).order_by(Payment.created_at.desc()).limit(50)
    )).scalars().all()
    return [
        {
            "id": p.id, "plan": p.plan_tier.value, "amount": p.amount,
            "status": p.status, "created_at": p.created_at.isoformat() if p.created_at else None,
        } for p in rows
    ]


from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware


class NoCacheHTMLMiddleware(BaseHTTPMiddleware):
    """Never cache HTML entry points (index.html, legacy.html).

    The JS/CSS bundles are content-hashed by Vite, so they are safe to
    cache — but a cached index.html pins the browser to an OLD bundle
    forever (this exact stale-cache bug bit us twice: old PDF-era
    legacy.html and the pre-fuzzy swarm bundle). HTML must always be
    revalidated so clients pick up new bundle filenames immediately.
    """

    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        resp = await call_next(request)
        path = request.url.path
        if path.endswith(".html") or path in ("/app", "/app/"):
            resp.headers["Cache-Control"] = "no-store"
        elif path.startswith("/app/assets/"):
            # Hashed Vite bundles: safe for long caching.
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


app.add_middleware(NoCacheHTMLMiddleware)

import os as _os
_STATIC_DIR = _os.getenv("WEBAPP_STATIC_DIR", "/app/app/static")
if _os.path.isdir(_STATIC_DIR):
    app.mount("/app", StaticFiles(directory=_STATIC_DIR, html=True), name="webapp")


@app.get("/app")
@app.get("/webapp")
async def webapp_redirect():
    """Convenience: /app → /app/ (index)."""
    return Response(status_code=307, headers={"Location": "/app/"})


@app.get("/api/v1/vibe/runs/{run_id}/chart")
async def run_chart(run_id: str, user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Equity-curve chart PNG for a run (ownership-checked) — rendered by the engine's data."""
    detail = await get_run_detail(run_id=run_id, user=user, db=db)
    equity_raw = detail.get("equity_curve") or []
    metrics = detail.get("metrics") or {}

    # Normalize equity: engine sends [{time, equity, drawdown}, ...] or plain numbers
    equity = []
    for point in equity_raw:
        if isinstance(point, dict):
            try:
                equity.append(float(point.get("equity")))
            except (TypeError, ValueError):
                continue
        else:
            try:
                equity.append(float(point))
            except (TypeError, ValueError):
                continue
    if len(equity) < 2:
        raise HTTPException(404, "نموداری برای این گزارش موجود نیست")

    # Render minimal PNG server-side with matplotlib (already a gateway dep via bot? fallback: SVG)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import io

        fig, ax = plt.subplots(figsize=(10, 4.2), dpi=110)
        xs = list(range(len(equity)))
        ax.plot(xs, equity, linewidth=1.6, color="#4f8cff")
        ax.fill_between(xs, min(equity), equity, alpha=0.12, color="#4f8cff")
        ax.set_facecolor("#0b0e14")
        fig.patch.set_facecolor("#131722")
        ax.tick_params(colors="#8b93a7", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#232b3f")
        ax.grid(alpha=0.15, color="#8b93a7")
        ax.set_title(f"Equity Curve — {metrics.get('total_return', 0):+.1%}", color="#e6e9f0", fontsize=11)
        buf = io.BytesIO()
        fig.tight_layout()
        fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
        plt.close(fig)
        # Immutable per run → let the browser cache it (repeat opens are instant)
        return Response(content=buf.getvalue(), media_type="image/png",
                        headers={"Cache-Control": "private, max-age=86400"})
    except Exception as exc:  # matplotlib missing → SVG fallback
        w, h = 640, 220
        lo, hi = min(equity), max(equity)
        rng = (hi - lo) or 1
        pts = " ".join(
            f"{i * w / max(len(equity) - 1, 1):.1f},{h - 20 - (v - lo) * (h - 40) / rng:.1f}"
            for i, v in enumerate(equity)
        )
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">'
            f'<rect width="100%" height="100%" fill="#131722"/>'
            f'<polyline points="{pts}" fill="none" stroke="#4f8cff" stroke-width="2"/></svg>'
        )
        return Response(content=svg, media_type="image/svg+xml")





@app.get("/api/v1/vibe/runs/{run_id}/pdf")
async def run_pdf(run_id: str, user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Backtest report as PDF (ownership-checked) — reuses the bot's fpdf2 builder."""
    # full=True: the tearsheet sections need artifacts_equity_csv + positions
    detail = await get_run_detail(run_id=run_id, user=user, db=db, full=True)
    # factor research is a separate engine endpoint — attach if the run has it
    if detail.get("has_factor_artifacts"):
        try:
            pool = get_pool()
            detail["factor_report"] = await pool.request(db, "GET", f"/runs/{run_id}/factor")
        except Exception:
            pass
    from app.pdf_report import build_backtest_pdf  # type: ignore

    try:
        pdf_bytes = build_backtest_pdf(detail)
    except Exception as exc:
        raise HTTPException(500, f"خطا در ساخت PDF: {exc}")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="backtest_{run_id}.pdf"',
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# One-time download tokens — Telegram WebApp can't fetch() blob downloads from
# cross-origin iframes on all clients, so we mint a short-lived token that lets
# the browser open the PDF with a plain <a href> / openLink.
# Tokens live in Redis (gateway runs 4 uvicorn workers — memory dict is NOT shared).
# ---------------------------------------------------------------------------

async def _dl_redis():
    import redis.asyncio as _redis
    return _redis.from_url(get_settings().REDIS_URL, decode_responses=True)


@app.post("/api/v1/vibe/runs/{run_id}/pdf-token")
async def run_pdf_token(
    run_id: str,
    body: dict | None = None,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Mint a one-time token (60s) to fetch this run's PDF or code file without auth headers."""
    await get_run_detail(run_id=run_id, user=user, db=db)  # ownership check
    kind = (body or {}).get("kind", "pdf")
    if kind not in ("pdf", "code"):
        raise HTTPException(400, "نوع دانلود نامعتبر است")
    file = str((body or {}).get("file") or "signal_engine.py")[:64]
    token = secrets.token_urlsafe(24)
    r = await _dl_redis()
    try:
        # MULTI-USE within the TTL window, capped use-count (see _check_dl_token):
        # Telegram's downloader fetches the URL MORE THAN ONCE (sniff + save,
        # sometimes a retry) — a single-use GETDEL made every 2nd fetch 403
        # and the downloader died SILENTLY (popup OK, no file saved).
        await r.set(f"dl:{token}", f"{run_id}|{kind}|{file}|0", ex=600)
    finally:
        await r.aclose()
    url = (
        f"/api/v1/vibe/runs/{run_id}/pdf-open?token={token}"
        if kind == "pdf"
        else f"/api/v1/vibe/runs/{run_id}/code-open?token={token}&file={file}"
    )
    return {"token": token, "url": url, "expires_in": 600}


_DL_MAX_USES = 20  # generous cap: covers sniff+save+retries, still stops abuse loops


async def _check_dl_token(token: str, run_id: str, kind: str) -> str | None:
    """Validate a download token; returns the bound file (for code) or ''.

    Multi-use by design (max _DL_MAX_USES fetches within the 600s TTL):
    Telegram's native downloader re-fetches the same URL (Chromium does an
    initial sniff pass, then the real save, then possible range-retry), so
    consuming the token on the first GET broke every download after the
    first fetch. GET + SET(racy between the 4 workers, but the cap is
    generous and only guards against abuse loops); KEEPTTL keeps the
    window fixed from mint time.
    """
    import logging
    r = await _dl_redis()
    try:
        raw = await r.get(f"dl:{token}")
        if not raw:
            logging.getLogger("uvicorn.error").warning(
                "[dl-token] REJECTED (missing/expired) run=%s kind=%s token=%s", run_id, kind, token[:8]
            )
            return None
        parts = raw.split("|")
        # legacy single-use format: run_id|kind|file (no use counter)
        if len(parts) == 3:
            parts.append("0")
        if len(parts) != 4 or parts[0] != run_id or parts[1] != kind:
            logging.getLogger("uvicorn.error").warning(
                "[dl-token] REJECTED (mismatch) run=%s kind=%s token=%s raw=%r", run_id, kind, token[:8], raw[:64]
            )
            return None
        used = int(parts[3] or "0") + 1
        if used > _DL_MAX_USES:
            logging.getLogger("uvicorn.error").warning(
                "[dl-token] REJECTED (over-use %d) run=%s token=%s", used, run_id, token[:8]
            )
            return None
        await r.set(f"dl:{token}", f"{parts[0]}|{parts[1]}|{parts[2]}|{used}", xx=True, keepttl=True)
        logging.getLogger("uvicorn.error").info(
            "[dl-token] ACCEPTED use=%d run=%s kind=%s", used, run_id, kind
        )
        return parts[2]
    finally:
        await r.aclose()


@app.get("/api/v1/vibe/runs/{run_id}/pdf-open")
async def run_pdf_open(run_id: str, token: str, db: AsyncSession = Depends(get_db)):
    """Fetch the PDF via download token (no Authorization header needed)."""
    if await _check_dl_token(token, run_id, "pdf") is None:
        raise HTTPException(403, "لینک دانلود منقضی یا نامعتبر است — دوباره تلاش کن")

    pool = get_pool()
    detail = await pool.request(db, "GET", f"/runs/{run_id}")
    from app.pdf_report import build_backtest_pdf  # type: ignore
    try:
        pdf_bytes = build_backtest_pdf(detail)
    except Exception as exc:
        raise HTTPException(500, f"خطا در ساخت PDF: {exc}")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="backtest_{run_id}.pdf"',
            "Cache-Control": "no-store",
            # Telegram WebApp downloadFile() requires this per official docs
            "Access-Control-Allow-Origin": "https://web.telegram.org",
        },
    )


@app.get("/api/v1/vibe/runs/{run_id}/code-open")
async def run_code_open(run_id: str, token: str, file: str = "signal_engine.py", db: AsyncSession = Depends(get_db)):
    """Fetch a strategy source file via download token (no Authorization header needed)."""
    bound = await _check_dl_token(token, run_id, "code")
    if bound is None:
        raise HTTPException(403, "لینک دانلود منقضی یا نامعتبر است — دوباره تلاش کن")
    if file != bound:
        raise HTTPException(403, "فایل درخواستی با توکن هم‌خوان نیست")

    pool = get_pool()
    files: dict = {}
    try:
        files = await pool.request(db, "GET", f"/runs/{run_id}/code") or {}
    except Exception:
        files = {}
    src = files.get(file)
    if src is None:
        raise HTTPException(404, "فایل یافت نشد")
    return Response(
        content=src,
        media_type="text/x-python; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{file}"',
            "Cache-Control": "no-store",
            # Telegram WebApp downloadFile() requires this per official docs
            "Access-Control-Allow-Origin": "https://web.telegram.org",
        },
    )


@app.post("/api/v1/vibe/swarm/runs/{run_id}/pdf-token")
async def swarm_pdf_token(
    run_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Mint a one-time token (60s) to fetch a swarm PDF without auth headers."""
    result = await db.execute(
        select(SwarmRun).where(SwarmRun.swarm_run_id == run_id, SwarmRun.user_id == user.id)
    )
    if not result.scalar_one_or_none() and not user.is_admin:
        raise HTTPException(403, "این اجرا متعلق به شما نیست")
    token = secrets.token_urlsafe(24)
    r = await _dl_redis()
    try:
        # multi-use (Telegram downloader re-fetches; see _check_dl_token)
        await r.set(f"dl:{token}", f"{run_id}|pdf|pdf|0", ex=600)
    finally:
        await r.aclose()
    return {"token": token, "url": f"/api/v1/vibe/swarm/runs/{run_id}/pdf-open?token={token}", "expires_in": 600}


@app.get("/api/v1/vibe/swarm/runs/{run_id}/pdf-open")
async def swarm_pdf_open(run_id: str, token: str, db: AsyncSession = Depends(get_db)):
    """Fetch the swarm PDF via download token (no Authorization header needed)."""
    if await _check_dl_token(token, run_id, "pdf") is None:
        raise HTTPException(403, "لینک دانلود منقضی یا نامعتبر است — دوباره تلاش کن")

    pool = get_pool()
    status = await pool.request(db, "GET", f"/swarm/runs/{run_id}")
    report = (status or {}).get("final_report", "")
    if not report:
        raise HTTPException(400, "این اجرا هنوز گزارشی ندارد (تکمیل نشده)")
    from app.pdf_report import build_swarm_pdf  # type: ignore
    try:
        preset = (status or {}).get("preset_name", "swarm")
        pdf_bytes = build_swarm_pdf(preset, preset, report, (status or {}).get("tasks", []))
    except Exception as exc:
        raise HTTPException(500, f"خطا در ساخت PDF: {exc}")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="swarm_{run_id[:16]}.pdf"',
            "Cache-Control": "no-store",
            # Telegram WebApp downloadFile() requires this per official docs
            "Access-Control-Allow-Origin": "https://web.telegram.org",
        },
    )


@app.get("/api/v1/vibe/runs/{run_id}/code")
async def run_code(run_id: str, user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Strategy source files for a run (ownership-checked) — same data as the main WebUI Code tab.

    Returns {"files": {filename: source}, "pine": {exists, content}} mirroring the
    engine's /runs/{id}/code + /runs/{id}/pine responses.
    """
    # Ownership check first (raises 403 for foreign runs)
    await get_run_detail(run_id=run_id, user=user, db=db)
    pool = get_pool()
    files: dict = {}
    try:
        files = await pool.request(db, "GET", f"/runs/{run_id}/code") or {}
    except Exception:
        files = {}
    pine: dict = {"exists": False, "content": None}
    try:
        pine = await pool.request(db, "GET", f"/runs/{run_id}/pine") or pine
    except Exception:
        pass
    if isinstance(files, dict) and "files" in files:
        files = files.get("files") or {}
    return {"files": files if isinstance(files, dict) else {}, "pine": pine}


@app.get("/api/v1/vibe/runs/{run_id}/code/download")
async def run_code_download(
    run_id: str,
    file: str = Query("signal_engine.py"),
    user: User = Depends(require_auth),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
):
    """Download one strategy source file as .py (ownership-checked)."""
    data = await run_code(run_id=run_id, user=user, db=db)
    files = data.get("files") or {}
    if file == "strategy.pine" or file.endswith(".pine"):
        pine = data.get("pine") or {}
        if not pine.get("exists"):
            raise HTTPException(404, "فایل Pine برای این گزارش موجود نیست")
        return Response(
            content=pine.get("content") or "",
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="strategy_{run_id[:12]}.pine"',
                "Cache-Control": "no-store",
            },
        )
    if file not in files:
        raise HTTPException(404, "فایل کد برای این گزارش موجود نیست")
    safe = "".join(c for c in file if c.isalnum() or c in "._-") or "strategy.py"
    return Response(
        content=files[file],
        media_type="text/x-python; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{safe}"',
            "Cache-Control": "no-store",
        },
    )
