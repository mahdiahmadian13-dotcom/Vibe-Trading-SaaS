"""Vibe-Trading SaaS — 30-day retention janitor (T051, FR-019).

Daily job: delete completed tasks older than 30 days (params/result —
the heavy payloads), keep a summary row's worth of aggregates in the
existing UsageLog counters (already incremented at completion time, so
nothing extra to write). Also purges raw fleet metrics older than 24h
(via metrics.purge_old — fine-grained samples; dashboard uses rollups).

Runs inside the gateway lifespan (asyncio, once per 24h). Best-effort:
never raises into the lifespan.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

log = logging.getLogger("retention")

RETENTION_DAYS = 30
INTERVAL_S = 24 * 3600


async def _run_once() -> dict:
    import shared.models as _m
    from shared.config import get_settings
    from shared.models import Task, TaskStatus
    from sqlalchemy import select
    settings = get_settings()
    if _m._session_factory is None:
        await _m.init_db(settings.DATABASE_URL)
    out = {"tasks_deleted": 0, "metrics_deleted": 0}
    try:
        async with _m._session_factory() as db:
            cutoff = _m._utcnow() - timedelta(days=RETENTION_DAYS)
            old = (await db.execute(
                select(Task).where(
                    Task.status.in_([TaskStatus.COMPLETED, TaskStatus.FAILED]),
                    Task.completed_at.is_not(None),
                    Task.completed_at < cutoff,
                )
            )).scalars().all()
            for t in old:
                await db.delete(t)
            out["tasks_deleted"] = len(old)
            try:
                from app.metrics import purge_old
                out["metrics_deleted"] = await purge_old(db)
            except Exception as exc:
                log.warning("retention: metrics purge failed: %s", exc)
            await db.commit()
    except Exception as exc:
        log.error("retention run failed: %s", exc)
    if out["tasks_deleted"] or out["metrics_deleted"]:
        log.warning("retention: deleted %d old tasks, %d metric rows",
                    out["tasks_deleted"], out["metrics_deleted"])
    return out


async def _retention_loop():
    await asyncio.sleep(300)  # let startup settle
    while True:
        try:
            await _run_once()
        except Exception as exc:
            log.error("retention loop error: %s", exc)
        await asyncio.sleep(INTERVAL_S)


_bg: list[asyncio.Task] = []


def start_retention_loop():
    _bg.append(asyncio.get_event_loop().create_task(_retention_loop()))


async def stop_retention_loop():
    for t in _bg:
        t.cancel()
