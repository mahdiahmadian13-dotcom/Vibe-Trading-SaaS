"""Coupon metering — consume at BACKTEST COMPLETION (fleet 001, user decision
2026-09-16).

Policy (owner):
  · Plain chat is free.
  · A completed backtest consumes exactly 1 backtest coupon.
  · If the free user has no coupon, a chat message that tries to run a
    backtest is refused immediately (429) — the message never reaches the
    engine, so the backtest never runs.

Implementation:
  · has_backtest_intent() — cheap local classifier used by the gateway's
    send_message to refuse before the engine starts anything.
  · metering scanner (30s loop) — watches the engine's recent runs and
    consumes one coupon per free-tier user the moment a run COMPLETES with
    real metrics, stamped action=run:<run_id> so it happens exactly once
    per run, even if the user never opens the report. Runs that already
    existed when the service started are never metered (cutoff).

Note: the queued-task path (POST /api/v1/tasks → task_backtest) meters
upfront at enqueue (action=task:backtest); the scanner may double-meter a
run from that path — acceptable, no client uses it today.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

log = logging.getLogger("metering")

SCAN_INTERVAL_S = 30

# Persian + English backtest-intent markers (ZWNJ normalized to space).
_KEYWORDS = (
    "بک تست", "بکتست", "backtest",
    "تست کن", "تست بزن", "تستش کن", "تست استراتژی",
)


def has_backtest_intent(text: str) -> bool:
    """Cheap heuristic: does this chat message ask for a backtest run?"""
    if not text:
        return False
    t = str(text).replace("\u200c", " ").lower()
    t = re.sub(r"\s+", " ", t)
    if any(k in t for k in _KEYWORDS):
        return True
    if "استراتژی" in t and any(w in t for w in ("اجرا", "تست", "بزن", "بساز")):
        return True
    if "strategy" in t and any(w in t for w in ("run", "test", "build")):
        return True
    return False


def _parse_created(value) -> datetime | None:
    """Engine RunInfo.created_at is 'YYYY-MM-DD HH:MM:SS' (naive UTC)."""
    if not isinstance(value, str) or not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


_cutoff: datetime | None = None
_bg: list[asyncio.Task] = []


async def _scan_once() -> int:
    from sqlalchemy import select

    import shared.models as _sm
    from app import coupons as coupons_mod
    from app.fleet import get_engine_pool
    from shared.config import get_settings
    from shared.models import Coupon, User, VibeSession

    if _sm._session_factory is None:
        await _sm.init_db(get_settings().DATABASE_URL)

    pool = get_engine_pool()
    runs = await pool.request(None, "GET", "/runs", params={"limit": 50})
    if not isinstance(runs, list) or not runs:
        return 0

    metered = 0
    async with _sm._session_factory() as db:
        smap = {
            v.vibe_session_id: v.user_id
            for v in (await db.execute(select(VibeSession))).scalars().all()
        }
        for run in runs:
            # Completed backtest = real metrics present.
            if run.get("total_return") is None and (run.get("metrics") or {}).get("total_return") is None:
                if run.get("sharpe") is None and (run.get("metrics") or {}).get("sharpe") is None:
                    continue
            rid = str(run.get("run_id") or "")
            if not rid:
                continue
            # Never meter runs that predate this deployment of the policy.
            created = _parse_created(run.get("created_at"))
            if _cutoff and (created is None or created < _cutoff):
                continue
            # Exactly once per run.
            already = (
                await db.execute(
                    select(Coupon).where(
                        Coupon.status == "used",
                        Coupon.action == f"run:{rid}",
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if already:
                continue
            uid = smap.get(run.get("session_id"))
            if not uid:
                continue
            user = (
                await db.execute(select(User).where(User.id == uid))
            ).scalar_one_or_none()
            if user is None or not coupons_mod.uses_coupons(user):
                continue
            # capture identity before any internal commit expires the ORM object
            user_id_val = user.id
            plan_is_free = coupons_mod.uses_coupons(user)
            if not plan_is_free:
                continue
            c = await coupons_mod.try_consume(db, user_id_val, "backtest", action=f"run:{rid}")
            if c is not None:
                metered += 1
                log.info("metered completed run %s → user %s (coupon %s)", rid, uid, c.id)
    return metered


async def _loop() -> None:
    while True:
        try:
            await _scan_once()
        except Exception as exc:
            log.warning("metering scan error: %s", exc)
        await asyncio.sleep(SCAN_INTERVAL_S)


def start_metering_loop() -> None:
    global _cutoff
    _cutoff = datetime.now(timezone.utc)
    _bg.append(asyncio.get_event_loop().create_task(_loop()))


async def stop_metering_loop() -> None:
    for t in _bg:
        t.cancel()
