"""Database models — SQLAlchemy ORM"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime,
    ForeignKey, Enum, Text, Index, JSON, UniqueConstraint
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship


def _utcnow():
    return datetime.now(timezone.utc)


# ============================================================================
# Enums
# ============================================================================

class PlanTier(str, enum.Enum):
    FREE = "free"
    BASIC = "basic"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    TRIAL = "trial"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ============================================================================
# Models
# ============================================================================

class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(128), unique=True, nullable=False, index=True)
    phone = Column(String(20), unique=True, nullable=True, index=True)
    email = Column(String(256), nullable=True)
    hashed_password = Column(String(256), nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    language = Column(String(10), default="fa")
    device_id = Column(String(128), nullable=True, index=True)
    telegram_id = Column(Integer, unique=True, nullable=True, index=True)
    ref_code = Column(String(16), unique=True, nullable=True, index=True)  # referral short code
    telegram_name = Column(String(128), nullable=True)  # display name from WebApp initData
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    subscriptions = relationship("Subscription", back_populates="user", lazy="selectin")
    tasks = relationship("Task", back_populates="user", lazy="selectin")
    vibe_sessions = relationship("VibeSession", back_populates="user", lazy="selectin")

    @property
    def current_plan(self) -> PlanTier:
        now = datetime.now(timezone.utc)
        for sub in self.subscriptions:
            if sub.status == SubscriptionStatus.ACTIVE and sub.expires_at > now:
                return sub.plan_tier
        return PlanTier.FREE


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    plan_tier = Column(Enum(PlanTier), nullable=False)
    status = Column(Enum(SubscriptionStatus), default=SubscriptionStatus.ACTIVE)
    started_at = Column(DateTime(timezone=True), default=_utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    user = relationship("User", back_populates="subscriptions")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    task_type = Column(String(50), nullable=False)
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING)
    priority = Column(Integer, default=0)
    params = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    worker_name = Column(String(128), nullable=True)
    progress = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="tasks")

    __table_args__ = (
        Index("ix_tasks_status_priority", "status", "priority"),
    )


class VibeSession(Base):
    """Maps gateway users to Vibe-Trading engine session IDs."""
    __tablename__ = "vibe_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    vibe_session_id = Column(String(256), unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    user = relationship("User", back_populates="vibe_sessions")


class UsageLog(Base):
    """Daily usage tracking per user."""
    __tablename__ = "usage_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    date = Column(String(10), nullable=False)
    messages_sent = Column(Integer, default=0)
    sessions_created = Column(Integer, default=0)
    backtests_run = Column(Integer, default=0)
    swarm_runs = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_usage_user_date"),
    )


class Coupon(Base):
    """Free-tier metering coupons (backtest / swarm).

    Rules:
      - 3 welcome backtest coupons at signup (never expire)
      - +1 backtest coupon per day, valid only until Tehran midnight
      - 1 swarm coupon per week (grant gated 7 days, max 1 active)
      - Paid plans are exempt (plan limits apply instead).
    """
    __tablename__ = "coupons"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String(20), nullable=False)          # backtest | swarm
    source = Column(String(20), nullable=False)        # welcome | daily | weekly
    status = Column(String(12), nullable=False, default="active", index=True)  # active | used
    grant_key = Column(String(48), nullable=True)      # race guard: "daily:2026-09-14" / "weekly:2026-09-14" / "welcome:bt"
    action = Column(String(64), nullable=True)         # what consumed it
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=True)  # daily: Tehran midnight; welcome/weekly: NULL
    used_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "grant_key", name="uq_coupon_user_grant"),
    )


class SwarmRun(Base):
    """Multi-tenant ownership for swarm runs."""
    __tablename__ = "swarm_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    swarm_run_id = Column(String(256), unique=True, nullable=False, index=True)
    preset_name = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class EngineNode(Base):
    """Registered Vibe-Trading engine (multi-engine / multi-server fleet).

    The gateway load-balances chat/backtest/swarm proxy calls across
    healthy nodes. A node is marked unhealthy after HEALTH_FAILS
    consecutive failed health checks and never receives traffic until it
    reports healthy again.
    """
    __tablename__ = "engine_nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    url = Column(String(512), nullable=False)          # http://host:8899
    api_key = Column(String(256), nullable=True)        # per-node override (else global)
    region = Column(String(64), nullable=True)
    max_concurrency = Column(Integer, default=10)
    active_concurrency = Column(Integer, default=0)     # inflight counter (approx)
    is_enabled = Column(Boolean, default=True)
    is_healthy = Column(Boolean, default=True)
    health_fail_count = Column(Integer, default=0)
    last_health_at = Column(DateTime(timezone=True), nullable=True)
    last_health_detail = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class WorkerNode(Base):
    """Registry of ARQ workers (mirrors the Redis workers:registry hash).

    Populated by the gateway when it scans Redis; supports workers that
    cannot write the DB themselves (remote servers).
    """
    __tablename__ = "worker_nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(32), default="ready")        # ready | gone
    info = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class Payment(Base):
    """One payment attempt (IDPay) for a subscription plan."""
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    plan_tier = Column(Enum(PlanTier), nullable=False)
    amount = Column(Integer, nullable=False)            # IRR/Toman
    status = Column(String(32), default="pending")      # pending|paid|failed|canceled
    authority = Column(String(128), nullable=True, index=True)   # IDPay track id
    gateway_ref = Column(String(256), nullable=True)
    payment_ref = Column(String(256), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)



class LoginLog(Base):
    """Every successful login (web or bot-linked) for the admin audit trail."""
    __tablename__ = "login_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    ip = Column(String(64), nullable=True)
    user_agent = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, index=True)

    user = relationship("User", lazy="joined")

    __table_args__ = (
        Index("ix_login_logs_user_time", "user_id", "created_at"),
    )


class Referral(Base):
    """Referral program — one row per (referrer, invited) pair.
    ref_code on User is the shareable short code; this table tracks conversions."""
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    referrer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    invited_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    invited_telegram_id = Column(Integer, nullable=True, index=True)
    invited_username = Column(String(128), nullable=True)
    reward_granted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    referrer = relationship("User", foreign_keys=[referrer_id], lazy="joined")
    invited = relationship("User", foreign_keys=[invited_user_id], lazy="joined")

class ServerNode(Base):
    """A customer-controlled server joined to this SaaS control plane.

    One row per physical/VPS server. The node agent (installed via the
    one-line installer) authenticates with a per-server join token, reports
    heartbeats + worker status, and receives desired_state (worker count)
    which the agent applies locally via `docker compose --scale`.
    """

    __tablename__ = "server_nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    join_token = Column(String(128), unique=True, nullable=False, index=True)
    region = Column(String(64), nullable=True)
    # desired state (admin sets from panel; agent reconciles)
    desired_workers = Column(Integer, default=1)
    worker_concurrency = Column(Integer, default=4)   # per-worker max_jobs
    cpu_limit = Column(String(16), default="2.0")
    mem_limit = Column(String(16), default="2G")
    # observed state (agent reports)
    status = Column(String(32), default="pending")     # pending|online|offline|decommissioned
    observed_workers = Column(Integer, default=0)
    docker_ok = Column(Boolean, default=False)
    host_info = Column(JSON, nullable=True)            # {cpu, mem, docker_ver, ...}
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class SettingKV(Base):
    """Simple key-value store for runtime settings (plan prices, etc.)."""
    __tablename__ = "setting_kv"

    key = Column(String(128), primary_key=True)
    value = Column(JSON, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


# ============================================================================
# Database Engine
# ============================================================================

_engine = None
_session_factory = None


async def init_db(database_url: str):
    global _engine, _session_factory
    _engine = create_async_engine(
        database_url,
        echo=False,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
    )
    _session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

    from sqlalchemy.exc import IntegrityError as _SAIntegrityError

    async with _engine.begin() as conn:
        try:
            await conn.run_sync(Base.metadata.create_all)
        except _SAIntegrityError:
            # concurrent startup race (4 uvicorn workers) — the other worker already created the table/type
            pass
        except Exception as exc:
            # asyncpg DuplicateObjectError surfaces as DBAPIError wrapping UniqueViolationError
            if "already exists" in str(exc):
                pass
            else:
                raise


async def get_db():
    """FastAPI dependency — yields an AsyncSession, auto-closes on exit."""
    async with _session_factory() as session:
        yield session
