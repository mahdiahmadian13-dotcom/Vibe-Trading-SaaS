"""Vibe-Trading SaaS — Engine Fleet Manager.

Multi-engine / multi-server support for the gateway:
  • EnginePool — picks a healthy engine node per request (least inflight),
    with automatic health marking and failover on request failure.
  • Background health loop — probes every enabled node's /health every
    HEALTH_INTERVAL seconds, flips is_healthy, and records details.
  • Worker sync — mirrors the Redis `workers:registry` hash into the DB so
    remote workers (that have no DB access) still show up in the admin panel.

All state lives in Postgres (engine_nodes / worker_nodes), so the gateway
itself stays horizontally scalable too.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import httpx
import redis.asyncio as aioredis
from sqlalchemy import select, update

from shared.config import get_settings
from shared.models import EngineNode, WorkerNode, _utcnow

log = logging.getLogger("fleet")

HEALTH_INTERVAL = int(__import__("os").getenv("FLEET_HEALTH_INTERVAL", "30"))
HEALTH_FAILS = int(__import__("os").getenv("FLEET_HEALTH_FAILS", "2"))
INFLIGHT_TTL = 60.0  # seconds after which an inflight slot self-expires


# ============================================================================
# Engine pool — request routing across engine nodes
# ============================================================================

class EnginePool:
    """Async pool that routes API calls to healthy Vibe-Trading engines."""

    def __init__(self):
        self._inflight: dict[int, float] = {}  # node_id -> last inflight change ts

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def list_nodes(self, db, *, enabled_only: bool = True):
        stmt = select(EngineNode).order_by(EngineNode.id)
        if enabled_only:
            stmt = stmt.where(EngineNode.is_enabled.is_(True))
        return (await db.execute(stmt)).scalars().all()

    async def _pick(self, db) -> EngineNode | None:
        """Least-inflight selection among healthy+enabled nodes."""
        nodes = await self.list_nodes(db)
        candidates = [n for n in nodes if n.is_healthy]
        if not candidates:
            # Nothing healthy → try everything enabled (last resort)
            candidates = nodes
        if not candidates:
            return None
        now = time.monotonic()
        # inflight = claimed slots not yet released (expired claims don't count)
        def inflight(n: EngineNode) -> int:
            last = self._inflight.get(n.id, 0)
            if now - last > INFLIGHT_TTL:
                self._inflight.pop(n.id, None)
                return 0
            return 1  # simplified slot: at most one pending claim per node

        candidates.sort(key=lambda n: (inflight(n), n.active_concurrency or 0, n.id))
        return candidates[0]

    # ------------------------------------------------------------------
    # Core request
    # ------------------------------------------------------------------

    async def request(self, db, method: str, path: str, *, prefer_node_id: int | None = None,
                      **kwargs) -> dict:
        """Send a request to an engine, with failover across the fleet.

        Fails over on connect errors / 5xx / 429 to the next healthy node.
        Raises the last HTTPException if every node fails.
        ``db`` may be None for endpoints without a DB session — one is
        created internally then.
        """
        from fastapi import HTTPException

        if db is None:
            from shared.models import _session_factory
            async with _session_factory() as sdb:
                return await self.request(sdb, method, path, prefer_node_id=prefer_node_id, **kwargs)

        nodes = await self.list_nodes(db)
        if not nodes:
            raise HTTPException(503, "هیچ موتوری ثبت نشده — از پنل ادمین موتور اضافه کنید")

        order = [n for n in nodes if n.id == prefer_node_id] if prefer_node_id else []
        order += [n for n in nodes if n not in order]

        # healthy first
        order.sort(key=lambda n: (not n.is_healthy,))

        last_exc: HTTPException | None = None
        errors: list[str] = []
        for node in order[: 3 if len(order) > 3 else len(order)]:
            base = node.url.rstrip("/")
            key = node.api_key or get_settings().VIBE_ENGINE_API_KEY
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.request(method, f"{base}{path}", headers=headers, **kwargs)
                if resp.status_code >= 400:
                    if resp.status_code >= 500 or resp.status_code in (429, 403):
                        errors.append(f"{node.name}:{resp.status_code}")
                        last_exc = HTTPException(resp.status_code, resp.text[:400])
                        continue
                    raise HTTPException(resp.status_code, resp.text[:400])
                return resp.json()
            except HTTPException as exc:
                if exc.status_code < 500 and exc.status_code not in (429, 403):
                    raise
                last_exc = exc
                errors.append(f"{node.name}:conn/{exc.status_code}")
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                await self._mark_unhealthy_by_node(db, node, f"request failed: {type(exc).__name__}")
                errors.append(f"{node.name}:unreachable")
                last_exc = HTTPException(502, f"موتور {node.name} در دسترس نیست")

        raise last_exc or HTTPException(502, f"همه موتورها خطا دادند: {', '.join(errors)}")

    # ------------------------------------------------------------------
    # Health marking
    # ------------------------------------------------------------------

    async def _mark_unhealthy_by_node(self, db, node: EngineNode, detail: str):
        node.health_fail_count = (node.health_fail_count or 0) + 1
        node.last_health_at = _utcnow()
        node.last_health_detail = detail[:500]
        if node.health_fail_count >= HEALTH_FAILS:
            node.is_healthy = False
        await db.commit()

    async def health_check_all(self, db) -> list[dict]:
        """Probe every enabled node once; update health state. Returns report."""
        nodes = await self.list_nodes(db)
        report = []
        settings = get_settings()
        for node in nodes:
            base = node.url.rstrip("/")
            key = node.api_key or settings.VIBE_ENGINE_API_KEY
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            ok, detail = False, ""
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.get(f"{base}/health", headers=headers)
                ok = resp.status_code == 200
                detail = f"HTTP {resp.status_code} {resp.text[:160]}"
            except Exception as exc:
                detail = f"{type(exc).__name__}: {str(exc)[:160]}"

            if ok:
                node.health_fail_count = 0
                node.is_healthy = True
            else:
                node.health_fail_count = (node.health_fail_count or 0) + 1
                if node.health_fail_count >= HEALTH_FAILS:
                    node.is_healthy = False
            node.last_health_at = _utcnow()
            node.last_health_detail = detail[:500]
            report.append({
                "node": node.name, "healthy": ok, "detail": detail[:200],
            })
        await db.commit()
        return report


_pool = EnginePool()


def get_engine_pool() -> EnginePool:
    return _pool


# ============================================================================
# Background loops (started from gateway lifespan)
# ============================================================================

async def _health_loop():
    settings = get_settings()
    from shared.models import _session_factory
    while True:
        try:
            async with _session_factory() as db:
                rep = await _pool.health_check_all(db)
                bad = [r for r in rep if not r["healthy"]]
                if bad:
                    log.warning("fleet health: %d/%d unhealthy → %s",
                                len(bad), len(rep), [r["node"] for r in bad])
        except Exception as exc:
            log.error("fleet health loop error: %s", exc)
        await asyncio.sleep(HEALTH_INTERVAL)


async def _worker_sync_loop():
    """Mirror Redis workers:registry into worker_nodes (DB)."""
    settings = get_settings()
    while True:
        try:
            r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            raw = await r.hgetall("workers:registry")
            await r.aclose()
            now = _utcnow()
            from shared.models import _session_factory
            async with _session_factory() as db:
                seen = set()
                for name, blob in (raw or {}).items():
                    seen.add(name)
                    try:
                        info = json.loads(blob)
                    except Exception:
                        info = {"raw": str(blob)[:400]}
                    row = (await db.execute(
                        select(WorkerNode).where(WorkerNode.name == name)
                    )).scalar_one_or_none()
                    if row:
                        row.last_seen_at = now
                        row.status = "ready"
                        row.info = info
                    else:
                        db.add(WorkerNode(name=name, last_seen_at=now, status="ready", info=info))
                # mark workers silent > 3 min as gone
                cutoff = now.timestamp() - 180
                for row in (await db.execute(select(WorkerNode))).scalars().all():
                    if row.name not in seen and (
                        row.last_seen_at is None or row.last_seen_at.timestamp() < cutoff
                    ):
                        row.status = "gone"
                await db.commit()
        except Exception as exc:
            log.error("worker sync loop error: %s", exc)
        await asyncio.sleep(60)


_bg_tasks: list[asyncio.Task] = []


def start_background_loops():
    for coro in (_health_loop(), _worker_sync_loop()):
        t = asyncio.get_event_loop().create_task(coro)
        _bg_tasks.append(t)


async def stop_background_loops():
    for t in _bg_tasks:
        t.cancel()
