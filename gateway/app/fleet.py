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
from shared.models import EngineNode, ServerNode, WorkerNode, _utcnow

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
                        # fleet T020: link worker → host server when heartbeat carries it
                        srv = (info or {}).get("server") if isinstance(info, dict) else None
                        if srv:
                            snode = (await db.execute(
                                select(ServerNode).where(ServerNode.name == srv)
                            )).scalar_one_or_none()
                            if snode:
                                row.server_id = snode.id
                    else:
                        db.add(WorkerNode(name=name, last_seen_at=now, status="ready", info=info))
                # mark workers silent > 3 min as gone
                cutoff = now.timestamp() - 180
                for row in (await db.execute(select(WorkerNode))).scalars().all():
                    if row.name not in seen and (
                        row.last_seen_at is None or row.last_seen_at.timestamp() < cutoff
                    ):
                        row.status = "gone"
                # fleet T022: mirror each ONLINE full-node server as an engine node
                # (local engine on that server) so EnginePool routes across them
                servers = (await db.execute(
                    select(ServerNode).where(ServerNode.status == "online")
                )).scalars().all()
                for s in servers:
                    if not s.engine_url_local:
                        continue
                    en = (await db.execute(
                        select(EngineNode).where(EngineNode.name == f"node-{s.name}")
                    )).scalar_one_or_none()
                    if en:
                        en.url = s.engine_url_local
                        en.is_enabled = True
                    else:
                        db.add(EngineNode(
                            name=f"node-{s.name}", url=s.engine_url_local,
                            api_key=None, is_enabled=True, is_healthy=True,
                        ))
                await db.commit()
        except Exception as exc:
            log.error("worker sync loop error: %s", exc)
        await asyncio.sleep(60)


_bg_tasks: list[asyncio.Task] = []


async def _tg_alert(text: str) -> None:
    """US13 T040: best-effort Telegram alert to FLEET_ALERT_TG_IDS.

    Never raises — a dead Telegram must not break the health loop.
    """
    settings = get_settings()
    token = (settings.TELEGRAM_BOT_TOKEN or "").strip()
    ids = [x.strip() for x in (settings.FLEET_ALERT_TG_IDS or "").split(",") if x.strip()]
    if not token or not ids:
        return
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for chat_id in ids:
                try:
                    await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                    )
                except Exception as exc:
                    log.warning("fleet tg alert to %s failed: %s", chat_id, exc)
    except Exception as exc:
        log.warning("fleet tg alert failed: %s", exc)


async def _server_watchdog_loop():
    """US13 T040: per-server heartbeat watchdog.

    online/degraded server silent too long → degraded → offline + auto-drain
    (dispatcher already excludes draining/offline, and the reaper moves its
    queued tasks to fallback). On recovery → back online with TG notice.
    Thresholds: degraded after 2 missed beats, offline after 4 (≈2×/4× the
    agent heartbeat interval).
    """
    from shared.models import _session_factory
    BEAT = 30          # agent heartbeat period (seconds)
    DEGRADE_AFTER = 60   # 2 missed beats
    OFFLINE_AFTER = 120  # 4 missed beats
    while True:
        try:
            async with _session_factory() as db:
                now = _utcnow()
                servers = (await db.execute(select(ServerNode))).scalars().all()
                for s in servers:
                    if s.status in ("pending", "decommissioned"):
                        continue
                    if not s.last_heartbeat_at:
                        continue
                    silent = (now - s.last_heartbeat_at).total_seconds()
                    if s.status in ("online", "degraded") and silent > OFFLINE_AFTER:
                        s.status = "offline"
                        await db.commit()
                        log.warning("server %s OFFLINE (silent %.0fs) — auto-drained", s.name, silent)
                        await _tg_alert(
                            f"🔴 سرور <b>{s.name}</b> قطع شد (heartbeat قطع شده، ~{int(silent)} ثانیه).\n"
                            f"ورکرها تخلیه و تسک‌های صف به fallback منتقل می‌شوند."
                        )
                    elif s.status == "online" and silent > DEGRADE_AFTER:
                        s.status = "degraded"
                        await db.commit()
                        log.warning("server %s degraded (silent %.0fs)", s.name, silent)
                    elif s.status in ("offline", "degraded") and silent <= DEGRADE_AFTER:
                        s.status = "online"
                        await db.commit()
                        log.warning("server %s RECOVERED — back online", s.name)
                        await _tg_alert(f"🟢 سرور <b>{s.name}</b> وصل شد و به چرخه برگشت.")
                # prune mirror engine rows for servers no longer online
                live_names = {s.name for s in servers if s.status == "online"}
                for en in (await db.execute(
                    select(EngineNode).where(EngineNode.name.like("node-%"))
                )).scalars().all():
                    short = en.name[len("node-"):]
                    if short not in live_names and en.is_enabled:
                        en.is_enabled = False
                await db.commit()
        except Exception as exc:
            log.error("server watchdog loop error: %s", exc)
        await asyncio.sleep(30)


def start_background_loops():
    for coro in (_health_loop(), _worker_sync_loop(), _server_watchdog_loop()):
        t = asyncio.get_event_loop().create_task(coro)
        _bg_tasks.append(t)


async def stop_background_loops():
    for t in _bg_tasks:
        t.cancel()
