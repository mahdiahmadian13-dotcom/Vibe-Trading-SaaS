"""Vibe-Trading SaaS — Node Agent.

Runs on each joined server (via docker-compose.node.yml). Responsibilities:
  1. Pull desired state from the control plane every RECONCILE_INTERVAL (10s).
  2. Reconcile worker replica count: `docker compose --scale worker=N`.
  3. Heartbeat observed state (worker count, host info, docker ok) every 15s.
  4. On desired_workers=0: scale down to zero (server stays joined).

The agent itself runs in a tiny container with the docker socket mounted
read-only, so it can drive the host's docker daemon.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import subprocess
import time
from datetime import datetime, timezone

import httpx

JOIN_TOKEN = os.getenv("VT_JOIN_TOKEN", "")
CONTROL_URL = os.getenv("VT_CONTROL_URL", "").rstrip("/")
SERVER_NAME = os.getenv("VT_SERVER_NAME", "")
NODE_DIR = os.getenv("NODE_PROJECT_DIR", "/agent-project")
COMPOSE_FILE = os.getenv("NODE_COMPOSE_FILE", "docker-compose.node.yml")
PROJECT_NAME = os.getenv("NODE_PROJECT_NAME", "vibe-node")
RECONCILE_INTERVAL = int(os.getenv("RECONCILE_INTERVAL", "10"))
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "15"))

_last_state: dict = {}
_last_error: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_state(client: httpx.AsyncClient) -> dict | None:
    r = await client.get(f"{CONTROL_URL}/api/v1/node/{JOIN_TOKEN}/state", timeout=15.0)
    r.raise_for_status()
    return r.json()


async def _heartbeat(client: httpx.AsyncClient, observed: dict) -> bool:
    try:
        r = await client.post(
            f"{CONTROL_URL}/api/v1/node/{JOIN_TOKEN}/heartbeat",
            json=observed,
            timeout=15.0,
        )
        return r.status_code == 200
    except Exception:
        return False


def _run(cmd: list[str], timeout: int = 180) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=NODE_DIR)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as exc:  # pragma: no cover
        return 1, str(exc)


def _ps_running_workers() -> int:
    """Count running worker CONTAINERS of this project (not deduped service names)."""
    rc, out = _run([
        "docker", "ps", "--filter", f"label=com.docker.compose.project={PROJECT_NAME}",
        "--filter", "label=com.docker.compose.service=worker",
        "--format", "{{.ID}}",
    ])
    if rc != 0:
        return -1
    return len([ln for ln in out.splitlines() if ln.strip()])


def outlines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.strip()]


def _scale_workers(n: int) -> tuple[int, str]:
    """Scale worker service to n replicas. n=0 removes them all."""
    if n <= 0:
        rc, out = _run([
            "docker", "compose", "-p", PROJECT_NAME, "-f", COMPOSE_FILE,
            "stop", "worker",
        ], timeout=300)
        if rc == 0:
            rc, out = _run([
                "docker", "compose", "-p", PROJECT_NAME, "-f", COMPOSE_FILE,
                "rm", "-f", "worker",
            ], timeout=300)
        return rc, out
    return _run([
        "docker", "compose", "-p", PROJECT_NAME, "-f", COMPOSE_FILE,
        "up", "-d", "--build", "--no-deps", "--scale", f"worker={n}", "worker",
    ], timeout=900)


def _apply_env_files(state: dict) -> None:
    """Write .env consumed by docker-compose.node.yml from control-plane state.

    Compose re-reads .env on each up/scale invocation, so worker replicas
    always start with the current broker/engine/database URLs.
    """
    redis_url = state.get("redis_url", "")
    database_url = state.get("database_url", "")
    engine_url = state.get("engine_url", "")
    engine_key = state.get("engine_api_key", "")
    conc = int(state.get("worker_concurrency", 4))
    cpu = state.get("cpu_limit", "2.0")
    mem = state.get("mem_limit", "2G")
    name = state.get("server") or SERVER_NAME
    content = (
        f"VT_JOIN_TOKEN={JOIN_TOKEN}\n"
        f"VT_CONTROL_URL={CONTROL_URL}\n"
        f"VT_SERVER_NAME={name}\n"
        f"BROKER_URL={redis_url}\n"
        f"DATABASE_URL={database_url}\n"
        f"ENGINE_URL={engine_url}\n"
        f"ENGINE_API_KEY={engine_key}\n"
        f"WORKER_CONCURRENCY={conc}\n"
        f"CPU_LIMIT={cpu}\n"
        f"MEM_LIMIT={mem}\n"
    )
    # write .env (idempotent)
    with open(f"{NODE_DIR}/.env", "w") as f:
        f.write(content)


def _host_info() -> dict:
    total, used, _ = shutil.disk_usage("/")
    return {
        "hostname": platform.node(),
        "os": platform.platform(),
        "cpu_count": os.cpu_count(),
        "mem_total_gb": round(_mem_total_gb(), 1),
        "disk_total_gb": round(total / 1e9, 1),
        "disk_used_pct": round(used / total * 100, 1) if total else None,
        "agent_version": "1.0.0",
    }


def _mem_total_gb() -> float:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1e6  # kB -> GB
    except Exception:
        pass
    return 0.0


def _docker_ok() -> bool:
    rc, _ = _run(["docker", "version", "--format", "{{.Server.Version}}"])
    return rc == 0


async def reconcile_loop():
    global _last_state, _last_error
    async with httpx.AsyncClient() as client:
        while True:
            try:
                state = await _get_state(client)
                if state:
                    _apply_env_files(state)
                    _last_state = state
                desired = int(state.get("desired_workers", 0))
                running = _ps_running_workers()
                if running >= 0 and running != desired:
                    print(f"[agent] scaling workers {running} -> {desired}", flush=True)
                    rc, out = _scale_workers(desired)
                    if rc != 0:
                        _last_error = out[-300:]
                        print(f"[agent] scale error: {out[-300:]}", flush=True)
                    else:
                        _last_error = ""
            except Exception as exc:
                _last_error = str(exc)[:300]
                print(f"[agent] state fetch error: {exc}", flush=True)
            await asyncio.sleep(RECONCILE_INTERVAL)


async def heartbeat_loop():
    async with httpx.AsyncClient() as client:
        while True:
            observed = {
                "observed_workers": max(_ps_running_workers(), 0),
                "docker_ok": _docker_ok(),
                "host_info": _host_info(),
                "last_error": _last_error,
                "ts": _now(),
            }
            ok = await _heartbeat(client, observed)
            if not ok:
                print("[agent] heartbeat failed", flush=True)
            await asyncio.sleep(HEARTBEAT_INTERVAL)


async def main():
    if not JOIN_TOKEN or not CONTROL_URL:
        raise SystemExit("VT_JOIN_TOKEN / VT_CONTROL_URL required")
    print(f"[agent] starting — server={SERVER_NAME or '(from state)'} control={CONTROL_URL}", flush=True)
    await asyncio.gather(reconcile_loop(), heartbeat_loop())


if __name__ == "__main__":
    asyncio.run(main())
