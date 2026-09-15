"""Vibe-Trading SaaS — ARQ Worker (distributed task processor)"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

import pickle

import httpx
import redis.asyncio as aioredis
from arq.connections import RedisSettings

import os, sys, socket
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from shared.models import (
    init_db, Task, TaskStatus, _utcnow,
    create_async_engine, async_sessionmaker, AsyncSession
)

# ============================================================================
# Config
# ============================================================================

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
ENGINE_URL = os.getenv("VIBE_ENGINE_URL", "http://engine:8899")
ENGINE_API_KEY = os.getenv("VIBE_ENGINE_API_KEY", "")
# Unique per container (docker --scale replicas get distinct hostnames)
WORKER_NAME = os.getenv("WORKER_NAME") or f"{os.getenv('WORKER_NAME_PREFIX') or 'worker'}-{socket.gethostname().split('.')[0]}"
WORKER_CONCURRENCY = int(os.getenv("WORKER_CONCURRENCY", "4"))
DATABASE_URL = os.getenv("DATABASE_URL", "")
# Dedicated queue consumed ONLY by this worker (dispatcher writes here)
QUEUE_NAME = os.getenv("WORKER_QUEUE", f"arq:q:{WORKER_NAME}")
INFLIGHT_PREFIX = "vibe:dispatch:inflight:"

# ============================================================================
# Shared Redis connection (reuse across calls)
# ============================================================================

_redis = None

async def get_redis():
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


# ============================================================================
# DB Session (for updating Task rows)
# ============================================================================

_db_engine = None
_db_factory = None

async def get_db_session() -> AsyncSession:
    global _db_engine, _db_factory
    if _db_factory is None:
        _db_engine = create_async_engine(DATABASE_URL, pool_size=5, pool_pre_ping=True)
        _db_factory = async_sessionmaker(_db_engine, class_=AsyncSession, expire_on_commit=False)
    return _db_factory()


async def update_task(task_id: str, **kwargs):
    """Update a Task row by task_id."""
    async with await get_db_session() as db:
        from sqlalchemy import select
        result = await db.execute(select(Task).where(Task.task_id == task_id))
        task = result.scalar_one_or_none()
        if task:
            for k, v in kwargs.items():
                setattr(task, k, v)
            await db.commit()


# ============================================================================
# Engine Client
# ============================================================================

async def engine_request(method: str, path: str, **kwargs) -> dict:
    url = f"{ENGINE_URL}{path}"
    headers = {"Authorization": f"Bearer {ENGINE_API_KEY}"} if ENGINE_API_KEY else {}
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.request(method, url, headers=headers, **kwargs)
        if resp.status_code >= 400:
            return {"error": resp.text, "status_code": resp.status_code}
        return resp.json()


async def _mirror_run_to_center(run_id: str) -> None:
    """Fleet T023: copy a completed run's artifacts to the CENTER gateway.

    Same-machine nodes (ENGINE_URL == central) are a no-op — the gateway
    already sees the files. Remote nodes POST the small artifacts
    (signal_engine.py, config.json, metrics) to the gateway mirror endpoint
    so PDFs/code keep serving from central disk. Best-effort by design.
    """
    center = os.getenv("GATEWAY_URL", "") or os.getenv("VT_CONTROL_URL", "")
    if not center or ENGINE_URL.startswith("http://vibe-trading-vibe-trading-1"):
        return  # same host or no center configured — nothing to mirror
    try:
        detail = await engine_request("GET", f"/runs/{run_id}")
        if not isinstance(detail, dict) or "error" in detail:
            return
        payload = {
            "run_id": run_id,
            "node": os.getenv("VT_SERVER_NAME", ""),
            "metrics": {k: detail.get(k) for k in ("total_return", "sharpe", "status")},
            "artifacts": {k: detail.get(k) for k in ("signal_code", "config", "summary") if detail.get(k)},
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            await client.post(f"{center.rstrip('/')}/api/v1/fleet/mirror", json=payload)
    except Exception:
        pass  # best-effort — caller logs


# ============================================================================
# Redis Pub/Sub (progress notifications)
# ============================================================================

async def publish_progress(user_id: int, task_id: str, data: dict):
    r = await get_redis()
    channel = f"user:{user_id}:progress"
    payload = json.dumps({"task_id": task_id, **data, "ts": time.time()})
    await r.publish(channel, payload)


# ============================================================================
# Tasks
# ============================================================================

async def task_chat(ctx: dict, task_id: str, user_id: int, params: dict) -> dict:
    """Handle a chat message — forward to engine and poll for response."""
    session_id = params.get("session_id", "")
    message = params.get("message", "")

    await update_task(task_id, status=TaskStatus.RUNNING, started_at=_utcnow(), worker_name=WORKER_NAME)
    await publish_progress(user_id, task_id, {"status": "running", "phase": "sending"})

    try:
        result = await engine_request("POST", f"/sessions/{session_id}/messages", json={"content": message})
        if "error" in result:
            await update_task(task_id, status=TaskStatus.FAILED, error_message=result["error"], completed_at=_utcnow())
            await publish_progress(user_id, task_id, {"status": "failed", "error": result["error"]})
            return {"status": "failed", "error": result["error"]}

        await publish_progress(user_id, task_id, {"status": "running", "phase": "waiting_response"})

        # Poll for response
        for i in range(90):
            await asyncio.sleep(1)
            if i % 5 == 0:
                await publish_progress(user_id, task_id, {"status": "running", "phase": "polling", "elapsed": i})

            messages = await engine_request("GET", f"/sessions/{session_id}/messages")
            if isinstance(messages, list) and len(messages) > 0:
                last = messages[-1]
                if last.get("role") == "assistant" and last.get("content"):
                    answer = last["content"]
                    await update_task(task_id, status=TaskStatus.COMPLETED, result={"answer": answer}, completed_at=_utcnow())
                    await publish_progress(user_id, task_id, {"status": "completed", "answer": answer[:500]})
                    return {"status": "completed", "answer": answer}

        await update_task(task_id, status=TaskStatus.FAILED, error_message="Timeout", completed_at=_utcnow())
        await publish_progress(user_id, task_id, {"status": "failed", "error": "Timeout"})
        return {"status": "failed", "error": "Response timeout"}

    except Exception as e:
        await update_task(task_id, status=TaskStatus.FAILED, error_message=str(e), completed_at=_utcnow())
        await publish_progress(user_id, task_id, {"status": "failed", "error": str(e)})
        return {"status": "failed", "error": str(e)}


async def task_backtest(ctx: dict, task_id: str, user_id: int, params: dict) -> dict:
    """Run a backtest via the engine."""
    await update_task(task_id, status=TaskStatus.RUNNING, started_at=_utcnow(), worker_name=WORKER_NAME)
    await publish_progress(user_id, task_id, {"status": "running", "phase": "starting_backtest"})

    try:
        session = await engine_request("POST", "/sessions", json={"name": f"bt_{user_id}_{task_id[:8]}"})
        session_id = session.get("session_id") or session.get("id")

        prompt = params.get("prompt", f"Run a backtest with these parameters: {json.dumps(params)}")
        await engine_request("POST", f"/sessions/{session_id}/messages", json={"content": prompt})

        for i in range(900):  # LLM-driven backtests take several minutes
            await asyncio.sleep(1)
            if i % 15 == 0:
                progress = {"status": "running", "phase": "backtest", "elapsed": i}
                await update_task(task_id, progress=progress)
                await publish_progress(user_id, task_id, progress)

            messages = await engine_request("GET", f"/sessions/{session_id}/messages")
            if isinstance(messages, list) and len(messages) > 0:
                last = messages[-1]
                if last.get("role") == "assistant" and last.get("content"):
                    runs = await engine_request("GET", "/runs", params={"session_id": session_id})
                    if isinstance(runs, list):
                        for run in runs:
                            if run.get("status") == "success" and run.get("total_return") is not None:
                                result_data = {
                                    "run_id": run.get("id"),
                                    "metrics": {"total_return": run.get("total_return"), "sharpe": run.get("sharpe")},
                                    # fleet T023: which node computed this (UI + routing diagnostics)
                                    "node": os.getenv("VT_SERVER_NAME", ""),
                                    "engine": ENGINE_URL,
                                }
                                # fleet T023: mirror run artifacts to the CENTER so the
                                # gateway serves PDFs/code from central disk like today.
                                # Best-effort: a failed mirror never fails the task.
                                try:
                                    await _mirror_run_to_center(run.get("id"))
                                except Exception as me:
                                    print(f"[{WORKER_NAME}] center-mirror failed for {run.get('id')}: {me}")
                                await update_task(task_id, status=TaskStatus.COMPLETED, result=result_data, completed_at=_utcnow())
                                await publish_progress(user_id, task_id, {"status": "completed", **result_data})
                                return result_data

        await update_task(task_id, status=TaskStatus.FAILED, error_message="Backtest timeout", completed_at=_utcnow())
        await publish_progress(user_id, task_id, {"status": "failed", "error": "Backtest timeout"})
        return {"status": "failed", "error": "Backtest timeout"}

    except Exception as e:
        await update_task(task_id, status=TaskStatus.FAILED, error_message=str(e), completed_at=_utcnow())
        await publish_progress(user_id, task_id, {"status": "failed", "error": str(e)})
        return {"status": "failed", "error": str(e)}


async def task_swarm(ctx: dict, task_id: str, user_id: int, params: dict) -> dict:
    """Run a swarm (multi-agent) analysis."""
    preset_name = params.get("preset_name", "")
    user_vars = params.get("user_vars", {})
    user_vars["output_language"] = "Persian (Farsi) — write the ENTIRE final report in fluent Persian"

    await update_task(task_id, status=TaskStatus.RUNNING, started_at=_utcnow(), worker_name=WORKER_NAME)
    await publish_progress(user_id, task_id, {"status": "running", "phase": "starting_swarm"})

    try:
        result = await engine_request("POST", "/swarm/runs", json={"preset_name": preset_name, "user_vars": user_vars})
        run_id = result.get("id")
        if not run_id:
            await update_task(task_id, status=TaskStatus.FAILED, error_message="Failed to create swarm run", completed_at=_utcnow())
            return {"status": "failed", "error": "Failed to create swarm run"}

        for i in range(1200):
            await asyncio.sleep(1)
            if i % 30 == 0:
                status = await engine_request("GET", f"/swarm/runs/{run_id}")
                current_status = status.get("status", "running")
                progress = {
                    "status": "running", "phase": "swarm",
                    "tasks_done": status.get("completed_count", 0),
                    "tasks_total": status.get("task_count", 0),
                    "elapsed": i,
                }
                await update_task(task_id, progress=progress)
                await publish_progress(user_id, task_id, progress)

                if current_status in ("completed", "failed"):
                    if current_status == "completed":
                        report = status.get("final_report", "")
                        result_data = {"run_id": run_id, "report": report}
                        await update_task(task_id, status=TaskStatus.COMPLETED, result=result_data, completed_at=_utcnow())
                        await publish_progress(user_id, task_id, {"status": "completed", "report": report[:1000], "run_id": run_id})
                        return result_data
                    else:
                        await update_task(task_id, status=TaskStatus.FAILED, error_message="Swarm run failed", completed_at=_utcnow())
                        return {"status": "failed", "error": "Swarm run failed"}

        await update_task(task_id, status=TaskStatus.FAILED, error_message="Swarm timeout", completed_at=_utcnow())
        return {"status": "failed", "error": "Swarm timeout"}

    except Exception as e:
        await update_task(task_id, status=TaskStatus.FAILED, error_message=str(e), completed_at=_utcnow())
        await publish_progress(user_id, task_id, {"status": "failed", "error": str(e)})
        return {"status": "failed", "error": str(e)}


# ============================================================================
# ARQ Worker Settings
# ============================================================================

_heartbeat_task: asyncio.Task | None = None
INFLIGHT_KEY = INFLIGHT_PREFIX + WORKER_NAME


async def _job_start(ctx: dict):
    """Mark job inflight on this worker for the dispatcher's load metric."""
    try:
        r = await get_redis()
        await r.hset(INFLIGHT_KEY, ctx.get("job_id", ""), int(time.time() * 1000))
    except Exception:
        pass


async def _job_end(ctx: dict):
    """Clear inflight marker so the worker becomes eligible for new jobs."""
    try:
        r = await get_redis()
        await r.hdel(INFLIGHT_KEY, ctx.get("job_id", ""))
    except Exception:
        pass


async def _health_report(ctx: dict):
    """Periodically check for and clean any stale state from dead runs."""
    pass


def _load_recycle(ctx: dict):
    return time.time()


async def _heartbeat():
    """Refresh workers:registry every 30s so the gateway knows we are alive."""
    while True:
        try:
            r = await get_redis()
            await r.hset("workers:registry", WORKER_NAME, json.dumps({
                "name": WORKER_NAME,
                "heartbeat_at": datetime.now(timezone.utc).isoformat(),
                "concurrency": WORKER_CONCURRENCY,
                "status": "ready",
                "engine": ENGINE_URL,
                "server": os.getenv("VT_SERVER_NAME", ""),  # fleet T020: host node link
            }))
        except Exception as e:
            print(f"[{WORKER_NAME}] heartbeat error: {e}")
        await asyncio.sleep(30)


async def startup(ctx):
    """Worker startup — register with broker and init DB."""
    r = await get_redis()
    await r.hset("workers:registry", WORKER_NAME, json.dumps({
        "name": WORKER_NAME,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "concurrency": WORKER_CONCURRENCY,
        "status": "ready",
        "engine": ENGINE_URL,
    }))
    # Init DB connection for task updates
    if DATABASE_URL:
        await get_db_session()
    global _heartbeat_task
    _heartbeat_task = asyncio.create_task(_heartbeat())
    print(f"[{WORKER_NAME}] Worker started, registered with broker (engine={ENGINE_URL})")


async def shutdown(ctx):
    """Worker shutdown — deregister and cleanup."""
    global _heartbeat_task
    if _heartbeat_task:
        _heartbeat_task.cancel()
    r = await get_redis()
    await r.hdel("workers:registry", WORKER_NAME)
    global _redis, _db_engine
    if _redis:
        await _redis.aclose()
        _redis = None
    if _db_engine:
        await _db_engine.dispose()
    print(f"[{WORKER_NAME}] Worker stopped")


class WorkerSettings:
    """ARQ worker configuration."""

    functions = [task_chat, task_backtest, task_swarm]
    on_startup = startup
    on_shutdown = shutdown
    on_job_start = _job_start
    on_job_end = _job_end

    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    queue_name = QUEUE_NAME
    max_jobs = WORKER_CONCURRENCY
    job_timeout = 1200
    retry_delay = 10
    max_tries = 2
    poll_delay = 0.25

    # Dedicated per-worker queue (arq:q:<NAME>) fed by the gateway dispatcher;
    # the shared default queue is no longer used for new jobs.

