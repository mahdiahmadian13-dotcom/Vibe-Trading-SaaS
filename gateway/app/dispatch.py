"""Vibe-Trading SaaS — Smart Task Dispatcher.

Commercial-grade request routing. Replaces the naive "everyone eats from
one shared sorted-set" behavior:

  ┌──────────────┐      dispatch(user, task, args, plan)      ┌────────────────┐
  │ API / Bot    │ ─────────────────────────────────────────▶ │ DISPATCH ENGINE │
  └──────────────┘                                            └───────┬─────────┘
                                                  least-loaded ready worker
                                      ┌─────────────────────────┼──────────────┐
                                      ▼                         ▼              ▼
                              arq:q:<worker-A>          arq:q:<worker-B>   arq:q:fallback
                              (dedicated queue)         (dedicated queue)  (spill-over +
                                                                       dead-worker rescue)

How it works
------------
1. Worker load = jobs sitting in its dedicated queue (ZCARD arq:q:<name>)
   + jobs currently executing (hlen vibe:dispatch:inflight:<name>).
   Cheapest accurate signal; no O(n) scans.
2. dispatch() picks the least-loaded READY worker and writes the job
   **exactly the way arq does** (pickle {'t','f','a','k','et'} at
   arq:job:<id> with PSETEX + ZADD into the worker's queue), so the
   existing arq worker on that machine picks it up unchanged.
3. Plan priority: paid plans get a LOWER zset score (arq pops ascending),
   so a premium job enqueued after a free job still runs first.
4. Death safety: jobs live in per-worker zsets. If a worker dies, the
   reaper moves its queue into the fallback queue, then continuously
   re-drains fallback into the current healthiest worker. Nothing is
   lost, and worst case retry_delay/max_tries handle transient errors.
5. Backpressure: if the best worker's queue is at MAX_QUEUE_DEPTH, the
   job spills to the shared fallback queue instead of stacking behind
   a saturated box.
"""

from __future__ import annotations

import asyncio
import json
import os
import pickle
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import redis.asyncio as aioredis
from sqlalchemy import select

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
FALLBACK_QUEUE = "arq:q:fallback"
REGISTRY_KEY = "workers:registry"
STATS_KEY = "vibe:dispatch:stats"
INFLIGHT_PREFIX = "vibe:dispatch:inflight:"
MAX_QUEUE_DEPTH_PER_WORKER = int(os.getenv("DISPATCH_MAX_QUEUE_DEPTH", "50"))
JOB_KEY_PREFIX = "arq:job:"
JOB_EXPIRE_MS = 86_400_000  # 24h, matches arq default expires_extra

# Lower score = picked up sooner (arq polls ZRANGEBYSCORE ascending).
# Milliseconds subtracted per plan: premium effectively "arrives earlier".
PLAN_PRIORITY_MS = {"free": 0, "starter": 60_000, "pro": 300_000, "enterprise": 900_000}


# Workers whose heartbeat is older than this are treated as stale at
# dispatch time: they stay in the registry (graceful shutdown deletes the
# entry anyway) but never receive NEW jobs. The reaper rescues their queues.
# 90s = 3 missed 30s heartbeats. Owner decision (fleet test 2026-09-16):
# central worker recreate left ghost names that swallowed jobs.
HEARTBEAT_FRESH_S = int(os.getenv("DISPATCH_HEARTBEAT_FRESH_S", "90"))


@dataclass
class WorkerInfo:
    name: str
    concurrency: int = 4
    status: str = "ready"
    heartbeat_at: str = ""
    engine: str = ""
    queue: str = ""
    server_id: int | None = None  # fleet: host node (resolved from worker_nodes)

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"

    @property
    def is_fresh(self) -> bool:
        """Heartbeat within HEARTBEAT_FRESH_S. Unparseable/missing = stale."""
        if not self.heartbeat_at:
            return False
        try:
            ts = datetime.fromisoformat(self.heartbeat_at)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            return 0 <= age <= HEARTBEAT_FRESH_S
        except (ValueError, TypeError):
            return False

    @property
    def inflight_key(self) -> str:
        return INFLIGHT_PREFIX + self.name


class Dispatcher:
    """Routes each incoming task to the best worker queue in Redis."""

    def __init__(self, redis_url: str = REDIS_URL):
        self._redis: Optional[aioredis.Redis] = None
        self.redis_url = redis_url
        self.reaper: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()

    # ------------------------------------------------------------------ redis
    async def _r(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self.redis_url, decode_responses=False)
        return self._redis

    async def start(self) -> None:
        """Start the background reaper loop (call once at app startup)."""
        if self.reaper is None or self.reaper.done():
            self._stopping.clear()
            self.reaper = asyncio.create_task(self._reaper_loop())
            r = await self._r()
            await r.hset(STATS_KEY, "started_at", int(time.time()))

    async def stop(self) -> None:
        self._stopping.set()
        if self.reaper:
            self.reaper.cancel()
            try:
                await self.reaper
            except (asyncio.CancelledError, Exception):
                pass
            self.reaper = None
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    # ------------------------------------------------------------- discovery
    async def get_workers(self) -> list[WorkerInfo]:
        """All workers from the live registry."""
        r = await self._r()
        raw = await r.hgetall(REGISTRY_KEY)
        out: list[WorkerInfo] = []
        for name_b, blob_b in raw.items():
            try:
                name = name_b.decode() if isinstance(name_b, bytes) else name_b
                blob = blob_b.decode() if isinstance(blob_b, bytes) else blob_b
                d = json.loads(blob)
            except (TypeError, ValueError, UnicodeDecodeError):
                continue
            out.append(WorkerInfo(
                name=name,
                concurrency=int(d.get("concurrency", 4)),
                status=d.get("status", "ready"),
                heartbeat_at=d.get("heartbeat_at", ""),
                engine=d.get("engine", ""),
                queue=f"arq:q:{name}",
            ))
        return out

    async def worker_load(self, w: WorkerInfo) -> int:
        """Queued + inflight count for one worker."""
        r = await self._r()
        qd = await r.zcard(f"arq:q:{w.name}")
        inflight = await r.hlen(w.inflight_key)
        return int(qd) + int(inflight)

    async def pick_worker(self, exclude: set[str] | None = None) -> Optional[WorkerInfo]:
        """Least-loaded READY worker; None = route to fallback.

        Fleet (T020): workers on draining/offline/decommissioned servers are
        excluded — the server map is refreshed from worker_nodes on each
        pick (cheap indexed query, no extra Redis round-trips).
        """
        exclude = exclude or set()
        blocked = await self._blocked_server_workers()
        cands = [w for w in await self.get_workers()
                 if w.is_ready and w.is_fresh and w.name not in exclude and w.name not in blocked]
        if not cands:
            return None
        loads = await asyncio.gather(*(self.worker_load(w) for w in cands))
        return min(zip(loads, cands), key=lambda p: p[0])[1]

    async def _blocked_server_workers(self) -> set[str]:
        """Worker names hosted on non-routable servers (draining/offline/...)."""
        try:
            from shared.models import _session_factory, ServerNode, WorkerNode
            async with _session_factory() as db:
                rows = (await db.execute(
                    select(WorkerNode.name).join(
                        ServerNode, ServerNode.id == WorkerNode.server_id
                    ).where(ServerNode.status.in_(
                        ("draining", "offline", "decommissioned", "degraded")
                    ))
                )).all()
                return {r[0] for r in rows}
        except Exception:
            return set()

    # ------------------------------------------------------------- dispatch
    async def dispatch(self, function: str, args: tuple, plan: str = "free",
                       priority: int = 0) -> dict:
        """Serialize + route one job. Returns {"job_id","worker","queue","fallback"}."""
        r = await self._r()
        w = await self.pick_worker()

        if w is None or await self.worker_load(w) >= MAX_QUEUE_DEPTH_PER_WORKER:
            queue, worker, fallback = FALLBACK_QUEUE, None, True
        else:
            queue, worker, fallback = f"arq:q:{w.name}", w.name, False

        job_id = uuid.uuid4().hex
        enqueue_ms = int(time.time() * 1000)
        score = enqueue_ms - PLAN_PRIORITY_MS.get(plan, 0) - priority * 10_000
        data = pickle.dumps({"t": None, "f": function, "a": args, "k": {}, "et": enqueue_ms})

        pipe = r.pipeline(transaction=True)
        pipe.psetex(JOB_KEY_PREFIX + job_id, JOB_EXPIRE_MS, data)
        pipe.zadd(queue, {job_id: score})
        pipe.hset(INFLIGHT_PREFIX + (worker or "fallback"), job_id, enqueue_ms)
        pipe.hincrby(STATS_KEY, "routed" if not fallback else "fallback_routed", 1)
        await pipe.execute()

        return {"job_id": job_id, "worker": worker, "queue": queue, "fallback": fallback}

    # ------------------------------------------------------------- reaper
    async def _reaper_loop(self) -> None:
        import logging
        log = logging.getLogger("dispatcher.reaper")
        while not self._stopping.is_set():
            try:
                await self._reap_tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("reap tick failed: %s", e)
            await asyncio.sleep(2)

    INFLIGHT_TTL_MS = 20 * 60 * 1000  # 20 min — hard cap on any single job

    async def _reap_tick(self) -> None:
        """Rescue dead-worker queues + drain fallback + expire stale inflight.

        Fleet (T021): a whole dead SERVER is detected via worker_nodes →
        server_nodes (status offline/decommissioned or heartbeat stale), and
        ALL its workers' queues are moved to fallback in one pass. Per-worker
        rescue below is unchanged.
        """
        r = await self._r()
        live = {w.name for w in await self.get_workers() if w.is_ready and w.is_fresh}
        await self._reap_dead_servers(r, live)

        # 0) Expire inflight members older than TTL (lost completion / zombie entries)
        now_ms = int(time.time() * 1000)
        for ihk_b in await r.keys(INFLIGHT_PREFIX + "*"):
            ihk = ihk_b.decode() if isinstance(ihk_b, bytes) else ihk_b
            for jid_b, ts_b in (await r.hgetall(ihk)).items():
                jid = jid_b.decode() if isinstance(jid_b, bytes) else jid_b
                ts_s = ts_b.decode() if isinstance(ts_b, bytes) else ts_b
                try:
                    ts_val = int(ts_s)
                except (TypeError, ValueError):
                    ts_val = 0
                if now_ms - ts_val > self.INFLIGHT_TTL_MS:
                    await r.hdel(ihk, jid)

        # 1) Any queue whose worker is no longer ready → move all jobs to fallback
        # (skip non-zset keys like arq:q:<name>:health-check via type check)
        for qk_b in await r.keys("arq:q:*"):
            qk = qk_b.decode() if isinstance(qk_b, bytes) else qk_b
            name = qk.split("arq:q:", 1)[1]
            if name == "fallback" or name in live or ":" in name:
                continue  # fallback itself / live worker / internal sub-keys
            t = await r.type(qk)
            t = t.decode() if isinstance(t, bytes) else t
            if t != "zset":
                continue  # health-check strings etc.
            n = await r.zcard(qk)
            if n == 0:
                continue
            for job_id, score in await r.zrangebyscore(qk, min="-inf", max="+inf", withscores=True):
                pipe = r.pipeline(transaction=True)
                pipe.zrem(qk, job_id)
                pipe.zadd(FALLBACK_QUEUE, {job_id: score})
                await pipe.execute()
            await r.hincrby(STATS_KEY, "rescued_from_dead", n)

        # 2) Drain fallback queue into the healthiest worker (max 16/tick)
        if await r.zcard(FALLBACK_QUEUE) == 0:
            return
        w = await self.pick_worker()
        if w is None:
            return
        jobs = await r.zrangebyscore(FALLBACK_QUEUE, min="-inf", max="+inf", start=0, num=16, withscores=True)
        if not jobs:
            return
        moved_ids: list[bytes] = []
        pipe = r.pipeline(transaction=True)
        for job_id, score in jobs:
            pipe.zrem(FALLBACK_QUEUE, job_id)
            pipe.zadd(f"arq:q:{w.name}", {job_id: score})
            pipe.hdel(INFLIGHT_PREFIX + "fallback", job_id)
            pipe.hset(w.inflight_key, job_id, int(time.time() * 1000))
            moved_ids.append(job_id)
        await pipe.execute()
        await r.hincrby(STATS_KEY, "reaped", len(moved_ids))

    async def _reap_dead_servers(self, r, live: set[str]) -> None:
        """Move ALL queues of a dead server's workers to fallback at once.

        A server counts as dead when its DB status is offline/decommissioned
        (set by the health loop / admin) — its workers' heartbeats are gone
        with it, so per-worker rescue would trickle them one tick at a time.
        Group rescue keeps plan-priority scores intact (zadd preserves score).
        """
        try:
            from shared.models import _session_factory, ServerNode, WorkerNode
            async with _session_factory() as db:
                dead = (await db.execute(
                    select(ServerNode.id).where(
                        ServerNode.status.in_(("offline", "decommissioned"))
                    )
                )).all()
                dead_ids = {row[0] for row in dead}
                if not dead_ids:
                    return
                names = (await db.execute(
                    select(WorkerNode.name).where(WorkerNode.server_id.in_(dead_ids))
                )).all()
        except Exception:
            return
        moved = 0
        for (wname,) in names:
            if wname in live:
                continue
            qk = f"arq:q:{wname}"
            try:
                t = await r.type(qk)
                t = t.decode() if isinstance(t, bytes) else t
                if t != "zset":
                    continue
                jobs = await r.zrangebyscore(qk, min="-inf", max="+inf", withscores=True)
                for job_id, score in jobs:
                    pipe = r.pipeline(transaction=True)
                    pipe.zrem(qk, job_id)
                    pipe.zadd(FALLBACK_QUEUE, {job_id: score})
                    await pipe.execute()
                    moved += 1
            except Exception:
                continue
        if moved:
            await r.hincrby(STATS_KEY, "rescued_from_dead_server", moved)


# ---------------------------------------------------------------------------
# Singleton + FastAPI lifecycle
# ---------------------------------------------------------------------------

_dispatcher: Optional[Dispatcher] = None


def get_dispatcher() -> Dispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = Dispatcher()
    return _dispatcher


async def start_dispatcher() -> None:
    await get_dispatcher().start()


async def stop_dispatcher() -> None:
    global _dispatcher
    if _dispatcher:
        await _dispatcher.stop()
        _dispatcher = None
