"""Vibe-Trading SaaS — Gateway (FastAPI)"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from shared.config import get_settings
from shared.models import (
    init_db, get_db, User, Subscription, Task, VibeSession,
    UsageLog, SwarmRun, PlanTier, SubscriptionStatus, TaskStatus, _utcnow
)
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
    yield


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
# Auth Routes
# ============================================================================

class RegisterRequest(BaseModel):
    username: str
    password: str
    phone: str | None = None
    device_id: str | None = None
    telegram_id: int | None = None


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
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    settings = get_settings()

    # Check existing username
    existing = await db.execute(select(User).where(User.username == req.username))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "نام کاربری قبلاً استفاده شده")

    # Check existing telegram_id
    if req.telegram_id:
        existing_tg = await db.execute(select(User).where(User.telegram_id == req.telegram_id))
        if existing_tg.scalar_one_or_none():
            raise HTTPException(400, "این تلگرام قبلاً ثبت‌نام شده")

    # Anti-abuse: device fingerprint
    if req.device_id and settings.MAX_ACCOUNTS_PER_DEVICE > 0:
        count = await db.execute(
            select(func.count()).where(User.device_id == req.device_id)
        )
        if count.scalar() >= settings.MAX_ACCOUNTS_PER_DEVICE:
            raise HTTPException(429, "این دستگاه قبلاً ثبت‌نام شده است. هر دستگاه فقط می‌تواند یک حساب داشته باشد.")

    # Create user
    user = User(
        username=req.username,
        hashed_password=hash_password(req.password),
        phone=req.phone,
        device_id=req.device_id,
        telegram_id=req.telegram_id,
    )
    db.add(user)
    await db.flush()

    # Auto-create FREE subscription
    sub = Subscription(
        user_id=user.id,
        plan_tier=PlanTier.FREE,
        status=SubscriptionStatus.ACTIVE,
        expires_at=_utcnow() + timedelta(days=365 * 10),
    )
    db.add(sub)
    await db.commit()
    await db.refresh(user)

    token = create_access_token({"sub": user.id, "username": user.username})
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        plan=user.current_plan.value,
    )


@app.post("/api/v1/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(401, "نام کاربری یا رمز عبور اشتباه است")

    token = create_access_token({"sub": user.id, "username": user.username})
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        plan=user.current_plan.value,
    )


# ============================================================================
# Subscription Routes
# ============================================================================

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
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a new Vibe-Trading session (with ownership tracking)."""
    await _check_limit(db, user, "session")

    vibe = get_vibe()
    result = await vibe.request("POST", "/sessions", json={"name": f"tg_{user.id}"})
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

    vibe = get_vibe()
    result = await vibe.request("POST", f"/sessions/{session_id}/messages", json=body)

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
    url = f"{settings.VIBE_ENGINE_URL}/sessions/{session_id}/events"
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


@app.get("/api/v1/vibe/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await _require_owned_session(db, user, session_id)
    vibe = get_vibe()
    return await vibe.request("GET", f"/sessions/{session_id}/messages")


@app.get("/api/v1/vibe/runs")
async def list_runs(
    session_id: str | None = Query(None),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    vibe = get_vibe()
    params: dict = {"limit": 200}
    if session_id:
        params["session_id"] = session_id
    runs = await vibe.request("GET", "/runs", params=params)

    # Filter to user's sessions (multi-tenant)
    owned = await _owned_session_ids(db, user)
    if not user.is_admin:
        runs = [r for r in runs if r.get("session_id") in owned]

    return runs


@app.get("/api/v1/vibe/swarm/presets")
async def swarm_presets(user: User = Depends(require_auth)):
    vibe = get_vibe()
    return await vibe.request("GET", "/swarm/presets")


@app.post("/api/v1/vibe/swarm/runs")
async def create_swarm_run(
    body: dict,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await _check_limit(db, user, "swarm")

    vibe = get_vibe()
    result = await vibe.request("POST", "/swarm/runs", json=body)
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

    vibe = get_vibe()
    return await vibe.request("GET", f"/swarm/runs/{run_id}")


# ============================================================================
# Task Queue Routes (for heavy async tasks via workers)
# ============================================================================

class TaskRequest(BaseModel):
    task_type: str  # backtest, swarm, chat
    params: dict = {}


@app.post("/api/v1/tasks")
async def create_task(
    req: TaskRequest,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue a heavy task for background worker processing."""
    await _check_limit(db, user, req.task_type)

    task_id = str(uuid.uuid4())
    task = Task(
        task_id=task_id,
        user_id=user.id,
        task_type=req.task_type,
        status=TaskStatus.PENDING,
        params=req.params,
    )
    db.add(task)

    # Update usage
    field = ACTION_FIELD_MAP.get(req.task_type)
    if field:
        usage = await _get_usage(db, user.id)
        setattr(usage, field, (getattr(usage, field, 0) or 0) + 1)

    await db.commit()

    # Enqueue to ARQ worker
    from shared.queue import enqueue
    job = await enqueue(
        f"task_{req.task_type}",
        task_id, user.id, req.params
    )

    return {"task_id": task_id, "status": "pending", "arq_job_id": job.job_id if job else None}


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
    vibe = get_vibe()
    return await vibe.request("GET", "/settings/llm")


@app.put("/api/v1/vibe/settings/llm")
async def update_llm_settings(
    body: dict,
    user: User = Depends(require_auth),
):
    if not user.is_admin:
        raise HTTPException(403, "فقط ادمین")
    vibe = get_vibe()
    return await vibe.request("PUT", "/settings/llm", json=body)


# ============================================================================
# Admin Routes
# ============================================================================

@app.get("/api/v1/admin/users")
async def admin_list_users(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    if not user.is_admin:
        raise HTTPException(403, "فقط ادمین")
    result = await db.execute(select(User))
    users = result.scalars().all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "telegram_id": u.telegram_id,
            "plan": u.current_plan.value,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@app.get("/api/v1/admin/tasks")
async def admin_list_tasks(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    if not user.is_admin:
        raise HTTPException(403, "فقط ادمین")
    result = await db.execute(
        select(Task).order_by(Task.created_at.desc()).limit(100)
    )
    return result.scalars().all()


# ============================================================================
# Run Detail Proxy (full backtest report)
# ============================================================================

@app.get("/api/v1/vibe/runs/{run_id}")
async def get_run_detail(
    run_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get full backtest run detail — ownership-checked (multi-tenant)."""
    vibe = get_vibe()
    detail = await vibe.request("GET", f"/runs/{run_id}")

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
    return detail


# ============================================================================
# Alpha Zoo Proxy (browse / detail / bench)
# ============================================================================

@app.get("/api/v1/vibe/alpha/list")
async def alpha_list(
    zoo: str | None = Query(None),
    user: User = Depends(require_auth),
):
    vibe = get_vibe()
    params = {"zoo": zoo} if zoo else {}
    return await vibe.request("GET", "/alpha/list", params=params)


@app.get("/api/v1/vibe/alpha/{alpha_id}")
async def alpha_detail(
    alpha_id: str,
    user: User = Depends(require_auth),
):
    vibe = get_vibe()
    return await vibe.request("GET", f"/alpha/{alpha_id}")


@app.post("/api/v1/vibe/alpha/bench")
async def alpha_bench(
    body: dict,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Start an alpha bench job. Checks quota."""
    await _check_limit(db, user, "backtest")
    vibe = get_vibe()
    result = await vibe.request("POST", "/alpha/bench", json=body)
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
    vibe = get_vibe()
    runs = await vibe.request("GET", "/swarm/runs")

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
    vibe = get_vibe()
    owned = await _owned_session_ids(db, user)
    if user.is_admin and not owned:
        return await vibe.request("GET", "/sessions")
    sessions = await vibe.request("GET", "/sessions")
    if not user.is_admin:
        sessions = [s for s in sessions if s.get("id") in owned or s.get("session_id") in owned]
    return sessions


@app.get("/api/v1/vibe/sessions/{session_id}/history")
async def session_history(
    session_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Full chat history of a session (ownership-checked)."""
    await _require_owned_session(db, user, session_id)
    vibe = get_vibe()
    return await vibe.request("GET", f"/sessions/{session_id}/messages")
