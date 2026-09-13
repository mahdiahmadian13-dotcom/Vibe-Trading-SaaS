"""Vibe-Trading SaaS — Gateway (FastAPI)"""

from __future__ import annotations

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
from shared.models import (
    init_db, get_db, User, Subscription, Task, VibeSession,
    UsageLog, SwarmRun, PlanTier, SubscriptionStatus, TaskStatus, _utcnow,
    EngineNode, WorkerNode, Payment, SettingKV, LoginLog, ServerNode
)
from app.fleet import EnginePool, get_engine_pool, start_background_loops, stop_background_loops
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

    start_background_loops()
    yield
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

    pool = get_pool()
    result = await pool.request(db, "POST", "/sessions", json={"name": f"tg_{user.id}"})
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

    pool = get_pool()
    result = await pool.request(db, "POST", f"/sessions/{session_id}/messages", json=body)

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

    pool = get_pool()
    result = await pool.request(db, "POST", "/swarm/runs", json=body)
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
        qdepth = await r.zcard("arq:queue")  # type: ignore
        await r.aclose()
    except Exception:
        pass
    return {
        "tasks": {"total": total, "today": today_c, "pending": pending, "running": running, "completed": completed, "failed": failed, "queue_depth": qdepth},
        "engines": [{"id": n.id, "name": n.name, "is_healthy": n.is_healthy, "is_enabled": n.is_enabled, "active": n.active_concurrency} for n in engines],
        "workers": [{"name": w.name, "status": w.status, "last_seen_at": w.last_seen_at.isoformat() if w.last_seen_at else None} for w in workers],
    }


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


@app.get("/api/v1/admin/servers")
async def admin_list_servers(admin: User = Depends(_require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ServerNode).order_by(ServerNode.id))).scalars().all()
    out = []
    for s in rows:
        workers = (await db.execute(select(WorkerNode).where(WorkerNode.name.ilike(f"{s.name}-%")))).scalars().all()
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
        })
    return out


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
    s = f"""#!/usr/bin/env bash
set -euo pipefail
# Vibe-Trading SaaS — node bootstrap (token-authenticated)
# This script: installs docker if missing, downloads the node bundle,
# writes .env, and starts the node agent + workers.
export VT_JOIN_TOKEN="{token}"
export VT_CONTROL_URL="{base}"
command -v docker >/dev/null 2>&1 || curl -fsSL https://get.docker.com | sh
mkdir -p /opt/vibe-node && cd /opt/vibe-node
curl -fsSL "$VT_CONTROL_URL/api/v1/node/{token}/bundle" -o node.tar.gz
tar xzf node.tar.gz
source .env 2>/dev/null || true
export VT_JOIN_TOKEN="{token}" VT_CONTROL_URL="{base}"
# start the agent only — it fetches config from the panel and then brings up workers
docker compose -p vibe-node -f docker-compose.node.yml up -d --build --quiet-pull agent
echo "Vibe node agent started. Workers will register within ~30s."
"""
    return PlainTextResponse(s, media_type="text/x-shellscript")


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
    await db.commit()
    return {"ok": True}


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
