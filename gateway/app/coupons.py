"""Free-tier coupon metering for Vibe-Trading SaaS.

A free user gets:
  - 3 welcome backtest coupons (never expire)
  - +1 backtest coupon per day, valid until Tehran midnight (23:59:59 +3:30)
  - 1 swarm coupon per week

Paid plans (basic/pro/enterprise) bypass coupons — plan daily limits apply.
All grants/claims are race-safe via the grant_key unique constraint.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import Coupon, User, PlanTier

WELCOME_BACKTEST_COUPONS = 3

# Tehran is UTC+3:30 (no DST since 2022)
_TEHRAN = timezone(timedelta(hours=3, minutes=30))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def tehran_today() -> str:
    """Current date in Tehran, YYYY-MM-DD."""
    return _now().astimezone(_TEHRAN).strftime("%Y-%m-%d")


def tehran_iso_week() -> str:
    """Current ISO week in Tehran, e.g. 2026-W37."""
    d = _now().astimezone(_TEHRAN)
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def tehran_midnight_utc() -> datetime:
    """Next Tehran midnight, expressed in UTC (when daily coupons die)."""
    t = _now().astimezone(_TEHRAN)
    next_midnight = (t + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_midnight.astimezone(timezone.utc)


def next_weekly_utc() -> datetime:
    """When the current Tehran ISO week ends (next Monday 00:00 Tehran → UTC)."""
    t = _now().astimezone(_TEHRAN)
    days_until_monday = (7 - t.isoweekday()) % 7  # Mon=1 → 0
    if days_until_monday == 0:
        days_until_monday = 7
    next_monday = (t + timedelta(days=days_until_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_monday.astimezone(timezone.utc)


def uses_coupons(user: User) -> bool:
    """Only FREE-tier users are metered by coupons."""
    return user.current_plan == PlanTier.FREE


async def ensure_welcome(db: AsyncSession, user_id: int) -> None:
    """Idempotently grant the 3 welcome backtest coupons (once per user).

    Each coupon gets its own grant_key (welcome:bt:1..3) so the unique
    constraint never fires inside the pack; the pre-check makes the whole
    pack idempotent.
    """
    rows = await db.execute(
        select(Coupon).where(
            Coupon.user_id == user_id, Coupon.source == "welcome"
        )
    )
    if rows.scalars().first() is not None:
        return  # welcome pack already granted

    for i in range(WELCOME_BACKTEST_COUPONS):
        db.add(Coupon(user_id=user_id, kind="backtest", source="welcome",
                      grant_key=f"welcome:bt:{i + 1}"))
    try:
        await db.flush()
        await db.commit()
    except IntegrityError:
        await db.rollback()


async def ensure_daily(db: AsyncSession, user_id: int) -> None:
    """Grant today's Tehran-dated backtest coupon — only if welcome pack is done.

    Daily refills start AFTER the 3 welcome coupons are used up, and each
    daily coupon is valid only until Tehran midnight.
    """
    # welcome still active? → no daily grant yet
    rows = await db.execute(
        select(Coupon).where(
            Coupon.user_id == user_id, Coupon.kind == "backtest",
            Coupon.source == "welcome", Coupon.status == "active",
        )
    )
    if rows.scalars().first() is not None:
        return

    key = f"daily:{tehran_today()}"
    c = Coupon(user_id=user_id, kind="backtest", source="daily", grant_key=key,
               expires_at=tehran_midnight_utc())
    db.add(c)
    try:
        await db.flush()
        await db.commit()
    except IntegrityError:
        await db.rollback()


async def ensure_weekly(db: AsyncSession, user_id: int) -> None:
    """Grant this ISO-week's swarm coupon if not already granted (max 1 active)."""
    key = f"weekly:{tehran_iso_week()}"
    # if the user still has an ACTIVE weekly coupon from this week, skip
    rows = await db.execute(
        select(Coupon).where(
            Coupon.user_id == user_id, Coupon.kind == "swarm", Coupon.status == "active"
        )
    )
    for c in rows.scalars():
        if c.grant_key == key:
            return
    db.add(Coupon(user_id=user_id, kind="swarm", source="weekly", grant_key=key))
    try:
        await db.flush()
        await db.commit()
    except IntegrityError:
        await db.rollback()


async def coupon_state(db: AsyncSession, user: User) -> dict:
    """Snapshot of a user's coupon wallet + next-refill times (for UI)."""
    uid = user.id
    # welcome is granted lazily for pre-existing accounts too
    await ensure_welcome(db, uid)
    await ensure_daily(db, uid)
    await ensure_weekly(db, uid)

    now = _now()
    rows = (await db.execute(
        select(Coupon).where(Coupon.user_id == uid).order_by(Coupon.created_at.desc())
    )).scalars().all()

    active_bt = [c for c in rows if c.kind == "backtest" and c.status == "active" and (c.expires_at is None or c.expires_at > now)]
    active_sw = [c for c in rows if c.kind == "swarm" and c.status == "active"]
    # daily coupons that expired (Tehran midnight passed) show as expired
    expired_today = [c for c in rows if c.kind == "backtest" and c.status == "active" and c.expires_at is not None and c.expires_at <= now]

    return {
        "backtest": {
            "active": len(active_bt),
            # welcome coupons never expire; daily ones die at midnight
            "expires_at": min((c.expires_at for c in active_bt if c.expires_at), default=None),
            "next_refill_at": tehran_midnight_utc().isoformat(),
            "welcome_left": sum(1 for c in active_bt if c.source == "welcome"),
            "daily": any(c.source == "daily" for c in active_bt),
        },
        "swarm": {
            "active": len(active_sw),
            "next_refill_at": next_weekly_utc().isoformat(),
        },
        "expired_today": len(expired_today),
    }


async def try_consume(db: AsyncSession, user: User, kind: str, action: str = "") -> Coupon | None:
    """Atomically consume one active coupon of the given kind.

    Returns the consumed Coupon, or None if the user has no active coupon
    (caller raises 429 with a friendly Persian message).
    Welcomes first (they never expire), then daily (may expire at claim time).
    """
    await ensure_daily(db, user.id) if kind == "backtest" else None
    await ensure_weekly(db, user.id) if kind == "swarm" else None

    now = _now()
    rows = (await db.execute(
        select(Coupon)
        .where(Coupon.user_id == user.id, Coupon.kind == kind, Coupon.status == "active")
        .order_by(Coupon.created_at.asc())
    )).scalars().all()

    # welcome first (oldest), then daily — validate expiry
    usable = [c for c in rows if c.expires_at is None or c.expires_at > now]
    # prefer welcome (non-expiring) over daily to save the daily for later in the same day
    usable.sort(key=lambda c: (c.expires_at is None, c.created_at))  # welcome first
    if not usable:
        return None

    c = usable[0]
    c.status = "used"
    c.used_at = now
    c.action = action or kind
    await db.commit()
    await db.refresh(c)
    return c


async def refund_coupon(db: AsyncSession, user_id: int, action: str) -> None:
    """Refund the most recently used coupon for this action (task failed → give back)."""
    rows = (await db.execute(
        select(Coupon)
        .where(Coupon.user_id == user_id, Coupon.status == "used", Coupon.action == action)
        .order_by(Coupon.used_at.desc())
    )).scalars().all()
    if rows:
        c = rows[0]
        c.status = "active"
        c.used_at = None
        c.action = None
        await db.commit()


async def expired_cleanup(db: AsyncSession, user_id: int) -> None:
    """Mark stale daily coupons as expired (Tehran midnight passed)."""
    now = _now()
    rows = (await db.execute(
        select(Coupon).where(
            Coupon.user_id == user_id, Coupon.status == "active", Coupon.expires_at is not None
        )
    )).scalars().all()
    changed = False
    for c in rows:
        if c.expires_at <= now:
            c.status = "expired"
            changed = True
    if changed:
        await db.commit()
