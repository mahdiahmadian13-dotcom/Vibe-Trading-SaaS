"""Vibe-Trading SaaS — per-server autoscaler (US5+US7, T050).

60s control loop: for each autoscale_enabled online server,
  want = ceil(total_pending_for_server / TARGET_PER_WORKER)
  clamped into [min_workers, max_workers], applied only when it differs
  from desired by HYSTERESIS (>=1) and COOLDOWN seconds passed since the
  last change. Manual panel edits always win: any PATCH /servers/{id}
  refreshes the cooldown stamp (autoscale stays quiet for COOLDOWN after
  a human touch).

Queue signal: sum of ZCARD over that server's workers' dedicated queues
+ fallback share (fallback belongs to no server; ignored — reaper drains
it anyway).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time

import redis.asyncio as aioredis
from sqlalchemy import select

log = logging.getLogger("autoscale")

TARGET_PER_WORKER = int(os.getenv("AUTOSCALE_TARGET_PER_WORKER", "6"))
COOLDOWN_S = int(os.getenv("AUTOSCALE_COOLDOWN_S", "300"))
HYSTERESIS = int(os.getenv("AUTOSCALE_HYSTERESIS", "1"))
INTERVAL_S = int(os.getenv("AUTOSCALE_INTERVAL_S", "60"))

_last_change: dict[int, float] = {}   # server_id -> epoch of last desired_workers write
_manual_touch: dict[int, float] = {}  # server_id -> epoch of last manual PATCH


def note_manual_change(server_id: int) -> None:
    """Called by PATCH /servers/{id}: human override silences autoscale."""
    _manual_touch[int(server_id)] = time.time()


async def _tick_once() -> list[dict]:
    import shared.models as _m
    from shared.config import get_settings
    from shared.models import ServerNode, WorkerNode
    settings = get_settings()
    actions: list[dict] = []
    if _m._session_factory is None:
        await _m.init_db(settings.DATABASE_URL)
    _sf = _m._session_factory
    try:
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    except Exception as exc:
        log.warning("autoscale: redis unavailable: %s", exc)
        return actions
    try:
        async with _sf() as db:
            servers = (await db.execute(
                select(ServerNode).where(
                    ServerNode.autoscale_enabled.is_(True),
                    ServerNode.status == "online",
                )
            )).scalars().all()
            workers = (await db.execute(
                select(WorkerNode).where(WorkerNode.status == "ready")
            )).scalars().all()
            by_server: dict[int, list[str]] = {}
            for w in workers:
                if w.server_id:
                    by_server.setdefault(w.server_id, []).append(w.name)
            now = time.time()
            for s in servers:
                names = by_server.get(s.id, [])
                pending = 0
                for n in names:
                    try:
                        pending += int(await r.zcard(f"arq:q:{n}")) or 0
                    except Exception:
                        pass
                want = math.ceil(pending / TARGET_PER_WORKER) if pending else 0
                want = max(s.min_workers or 0, min(want, s.max_workers or 0))
                cur = s.desired_workers or 0
                if abs(want - cur) < HYSTERESIS:
                    continue
                if now - _last_change.get(s.id, 0) < COOLDOWN_S:
                    continue
                if now - _manual_touch.get(s.id, 0) < COOLDOWN_S:
                    continue
                s.desired_workers = want
                _last_change[s.id] = now
                actions.append({"server": s.name, "from": cur, "to": want, "pending": pending})
                log.warning("autoscale %s: desired %d → %d (pending %d)", s.name, cur, want, pending)
            await db.commit()
    except Exception as exc:
        log.error("autoscale tick error: %s", exc)
    finally:
        try:
            await r.aclose()
        except Exception:
            pass
    return actions


async def _autoscale_loop():
    while True:
        try:
            await _tick_once()
        except Exception as exc:
            log.error("autoscale loop error: %s", exc)
        await asyncio.sleep(INTERVAL_S)


_bg: list[asyncio.Task] = []


def start_autoscale_loop():
    t = asyncio.get_event_loop().create_task(_autoscale_loop())
    _bg.append(t)


async def stop_autoscale_loop():
    for t in _bg:
        t.cancel()
