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


class SwarmRun(Base):
    """Multi-tenant ownership for swarm runs."""
    __tablename__ = "swarm_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    swarm_run_id = Column(String(256), unique=True, nullable=False, index=True)
    preset_name = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


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

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """FastAPI dependency — yields an AsyncSession, auto-closes on exit."""
    async with _session_factory() as session:
        yield session
