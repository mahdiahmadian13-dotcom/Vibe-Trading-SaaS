"""Vibe-Trading SaaS — per-server autoscaler (US5+US7, T050).

60s control loop: for each autoscale_enabled online server,
  want = ceil(pending_for_server / TARGET_PER_WORKER)
  clamped into [min_workers, max_workers] with hysteresis
  and cooldown; manual override always wins.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select

from shared.config import get_settings
from shared.models import ServerNode, WorkerNode, get_session_factory

logger = logging.getLogger("autoscale")

TICK_SEC = 60
HISTORY_SEC = 300
HISTORY_POINTS = 12
TARGET_PER_WORKER = 6
MIN_WORKERS = 1
MAX_WORKERS = 16
COOLDOWN_SEC = 300
SCALE_UP_SEC = 300
HYSTERESIS = 1


def _desired(pending: int) -> int:
    return max(MIN_WORKERS, math.ceil(pending / TARGET_PER_WORKER))


async def _tick_once() -> list[dict]:
    import shared.models as _m
    from shared.config import get_settings as _gs
    from shared.models import ServerNode, WorkerNode, _session_factory as _sf
    settings = _gs()
    actions: list[dict] = []
    async with _sf()(settings.database_url) as db:
        servers = (await db.execute(select(ServerNode))).scalars().all()
        now = datetime.now(timezone.utc)
        for s in servers:
            if s.status != "online" or not s.autoscale_enabled:
                continue
            key = f"fleet:autoscale:{s.id}"
            last = await db.execute(
                select(ServerNode.autoscale_last_action)
                .where(ServerNode.id == s.id)
            )
            la = last.scalar()
            if la and (now - la).total_seconds() < COOLDOWN_SEC:
                continue
            # Pending queue depth for this server's workers.
            ws = (await db.execute(
                select(WorkerNode.name).where(WorkerNode.server_id == s.id)
            )).scalars().all()
            pending = 0
            r = aioredis.from_url(settings.redis_url, decode_responses=True)
            try:
                for w in ws:
                    pending += await r.zcard(f"arq:q:{w}")
            finally:
                await r.aclose()
            want = _desired(pending)
            if want == s.desired_workers and not s._autoscale_pending:
                continue
            # Hysteresis band (±1)
            if abs(want - s.desired_workers) <= HYSTERESIS and not s._autoscale_pending:
                continue
            # Clamp
            want = max(MIN_WORKERS, min(MAX_WORKERS, want))
            # Cooldown / scale-up gate
            if want > s.desired_workers and s.autoscale_last_action:
                if (now - s.autoscale_last_action).total_seconds() < SCALE_UP_SEC:
                    continue
            actions.append({
                "server_id": s.id, "name": s.name,
                "pending": pending, "current": s.desired_workers,
                "desired": want,
            })
            try:
                await db.execute(
                    ServerNode.__table__.update()
                    .where(ServerNode.id == s.id)
                    .values(
                        desired_workers=want,
                        autoscale_last_action=now,
                        autoscale_pending=(want != s.desired_workers),
                    )
                )
                await db.commit()
                s.desired_workers = want
                s.autoscale_last_action = now
                s.autoscale_pending = (want != s.desired_workers)
                logger.info(
                    "autoscale %s: pending=%d current=%d -> desired=%d",
                    s.name, pending, s.desired_workers, want,
                )
            except Exception:
                await db.rollback()
                logger.exception("autoscale tick failed for server %s", s.id)
    return actions


async def start_autoscale_loop() -> None:
    """Background task — runs forever."""
    while True:
        try:
            await _tick_once()
        except Exception:
            logger.exception("autoscale loop error")
        await asyncio.sleep(TICK_SEC)


async def stop_autoscale_loop() -> None:
    pass
