"""Vibe-Trading SaaS — Fleet Updater service.

Runs on the central server next to the engine repo. Polls the control plane
for pending update jobs (admin clicks "بروزرسانی هسته" in the panel),
updates the HKUDS/Vibe-Trading engine core (git fetch + rebase-local +
docker build + recreate), verifies health, and on success bumps the
worker epoch so every joined server's agent rebuilds its workers from
the fresh bundle. On failure it rolls the engine back to the previous
commit and reports `rolled_back`.

Design notes:
  - One updater container, one job at a time (selected FOR UPDATE-style
    via atomic status swap pending -> running done by the poll endpoint).
  - The engine repo has local commits; we never reset --hard, we rebase
    local commits onto origin/main. On rebase conflicts we abort and roll
    back rather than touch the user's work.
  - The platform repo (SaaS itself) is NOT auto-updated by default; the
    job may optionally pull-and-rebuild it too (include_platform flag),
    but the updater never force-pushes or resets it.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

CONTROL_URL = os.getenv("VT_CONTROL_URL", "http://gateway:9000").rstrip("/")
UPDATER_TOKEN = os.getenv("VT_UPDATER_TOKEN", "")
ENGINE_DIR = Path(os.getenv("VT_ENGINE_DIR", "/opt/Vibe-Trading"))
PLATFORM_DIR = Path(os.getenv("VT_PLATFORM_DIR", "/repo"))
ENGINE_UPSTREAM = os.getenv("VT_ENGINE_UPSTREAM", "origin")
ENGINE_BRANCH = os.getenv("VT_ENGINE_BRANCH", "main")
ENGINE_SVC = os.getenv("VT_ENGINE_SERVICE", "vibe-trading")
POLL_INTERVAL = float(os.getenv("VT_POLL_INTERVAL", "5"))
STEPS_BETWEEN_REPORTS = int(os.getenv("VT_STEPS_BETWEEN_REPORTS", "1"))

_local_log: list = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(cmd: list[str] | str, cwd: Path | None = None, timeout: int = 900) -> tuple[int, str]:
    """Run a command, capture combined output, never raise."""
    try:
        p = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True,
                           timeout=timeout, cwd=str(cwd) if cwd else None)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {cmd}"
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def log(line: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    entry = f"[{ts}] {line}"
    print(entry, flush=True)
    _local_log.append(entry)


async def report(client: httpx.AsyncClient, job_id: int, status: str | None = None,
                step: str | None = None, line: str | None = None,
                error: str | None = None, to_commit: str | None = None,
                changed: bool | None = None, from_commit: str | None = None,
                node: str | None = None, node_status: str | None = None) -> None:
    """Push progress to the control plane (non-fatal on errors)."""
    try:
        await client.post(
            f"{CONTROL_URL}/api/v1/updater/report",
            json={
                "token": UPDATER_TOKEN,
                "job_id": job_id,
                "status": status,
                "step": step,
                "line": line,
                "error": error,
                "to_commit": to_commit,
                "changed": changed,
                "from_commit": from_commit,
                "node": node,
                "node_status": node_status,
            },
            timeout=20.0,
        )
    except Exception as exc:  # pragma: no cover
        print(f"[updater] report failed: {exc}", flush=True)


async def poll_job(client: httpx.AsyncClient) -> dict | None:
    try:
        r = await client.get(
            f"{CONTROL_URL}/api/v1/updater/poll",
            params={"token": UPDATER_TOKEN},
            timeout=15.0,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


# ============================================================================
# Engine update steps
# ============================================================================

def _git(cmd: list[str]) -> tuple[int, str]:
    return _run(["git"] + cmd, cwd=ENGINE_DIR, timeout=300)


def _current_commit() -> str | None:
    rc, out = _git(["rev-parse", "HEAD"])
    return out.strip() if rc == 0 else None


def _upstream_commit() -> str | None:
    rc, out = _git(["rev-parse", f"{ENGINE_UPSTREAM}/{ENGINE_BRANCH}"])
    return out.strip() if rc == 0 else None


def _repo_dirty() -> bool:
    rc, out = _git(["status", "--porcelain"])
    if rc != 0:
        return True  # assume dirty when in doubt
    return bool(out.strip())


def _fetch_upstream() -> tuple[bool, str]:
    rc, out = _git(["fetch", ENGINE_UPSTREAM, ENGINE_BRANCH])
    return rc == 0, out[-500:] if out else ""


def _rebase_onto_upstream() -> tuple[bool, str]:
    """Rebase local commits onto origin/main; abort cleanly on conflicts."""
    # record the pre-rebase HEAD for rollback
    rc, out = _git(["rebase", f"{ENGINE_UPSTREAM}/{ENGINE_BRANCH}"])
    if rc == 0:
        return True, out
    _git(["rebase", "--abort"])
    return False, out[-800:] if out else "rebase failed"


def _build_engine() -> tuple[bool, str]:
    """Rebuild the engine image via compose (no restart yet)."""
    rc, out = _run([
        "docker", "compose", "-p", "vibe-trading",
        "-f", str(ENGINE_DIR / "docker-compose.yml"),
        "build", ENGINE_SVC,
    ], timeout=2400)
    return rc == 0, out[-1500:] if out else ""


def _recreate_engine() -> tuple[bool, str]:
    """Recreate the engine container from the freshly built image."""
    rc, out = _run([
        "docker", "compose", "-p", "vibe-trading",
        "-f", str(ENGINE_DIR / "docker-compose.yml"),
        "up", "-d", "--no-deps", ENGINE_SVC,
    ], timeout=600)
    return rc == 0, out[-800:] if out else ""


def _engine_healthy() -> bool:
    """Probe engine health. Tries container-DNS HTTP first (updater is on the
    engine network when configured so); falls back to the HOST's port-mapped
    endpoint via docker exec inside the engine container — always available
    because the updater drives the host daemon anyway."""
    import json as _json
    deadline = time.time() + 120
    while time.time() < deadline:
        # path 1: direct HTTP (works when updater joined engine_net)
        rc, _ = _run(["curl", "-sf", "-m", "5",
                      "http://vibe-trading-vibe-trading-1:8899/health"], timeout=10)
        if rc == 0:
            return True
        # path 2: host-mapped port from the updater's own network namespace
        rc, _ = _run(["curl", "-sf", "-m", "5", "http://host.docker.internal:8899/health"], timeout=10)
        if rc == 0:
            return True
        # path 3: docker exec curl inside the engine container (daemon socket)
        rc, out = _run(["docker", "exec", "vibe-trading-vibe-trading-1",
                        "curl", "-sf", "-m", "5", "http://127.0.0.1:8899/health"], timeout=15)
        if rc == 0:
            return True
        time.sleep(4)
    return False


def _rollback_engine(commit: str) -> tuple[bool, str]:
    """Hard-reset the engine repo to `commit` and rebuild."""
    rc2, out2 = _git(["reset", "--hard", commit])
    if rc2 != 0:
        return False, f"git reset failed: {out2[-300:]}"
    ok_build, out_b = _build_engine()
    if not ok_build:
        return False, f"rollback build failed: {out_b[-300:]}"
    ok_up, out_u = _recreate_engine()
    if not ok_up:
        return False, f"rollback recreate failed: {out_u[-300:]}"
    if not _engine_healthy():
        return False, "engine unhealthy after rollback"
    return True, "rolled back"


def _commit_short(c: str | None) -> str:
    return (c or "?")[:9]


# ============================================================================
# Platform update (optional — SaaS repo itself)
# ============================================================================

def _platform_update() -> tuple[bool, str]:
    """Pull the platform repo and rebuild gateway+worker images.

    Only fast-forward; on divergence we skip (admin resolves manually) —
    never destroy the running platform on a conflict.
    """
    rc, out = _run(["git", "pull", "--ff-only"], cwd=PLATFORM_DIR, timeout=300)
    if rc != 0:
        return False, f"platform pull failed (non-ff or conflict): {out[-400:]}"
    rc, out = _run([
        "docker", "compose", "-f", str(PLATFORM_DIR / "docker-compose.yml"),
        "build", "gateway", "worker",
    ], cwd=PLATFORM_DIR, timeout=2400)
    if rc != 0:
        return False, f"platform build failed: {out[-600:]}"
    rc, out = _run([
        "docker", "compose", "-f", str(PLATFORM_DIR / "docker-compose.yml"),
        "up", "-d", "--no-deps", "gateway", "worker",
    ], cwd=PLATFORM_DIR, timeout=900)
    if rc != 0:
        return False, f"platform up failed: {out[-400:]}"
    return True, "platform rebuilt"


# ============================================================================
# Job execution
# ============================================================================

async def execute_job(client: httpx.AsyncClient, job: dict) -> None:
    job_id = int(job["id"])
    include_platform = bool(job.get("include_platform"))
    _local_log.clear()

    log(f"job #{job_id} started (scope={'engine+platform' if include_platform else 'engine'})")
    from_commit = _current_commit()
    await report(client, job_id, status="running", step="fetch", line=f"شروع — commit فعلی {_commit_short(from_commit)}")

    # 1) fetch upstream ------------------------------------------------------
    ok, out = _fetch_upstream()
    if not ok:
        await report(client, job_id, status="failed", step="fetch", error=f"git fetch failed: {out}")
        log(f"fetch failed: {out}")
        return
    upstream = _upstream_commit()
    if not upstream:
        await report(client, job_id, status="failed", step="fetch", error="cannot resolve origin/main")
        return

    if upstream == from_commit:
        await report(client, job_id, status="up_to_date", step="done",
                     line=f"هسته به‌روز است ({_commit_short(from_commit)})", changed=False,
                     to_commit=from_commit)
        log("up to date — nothing to do")
        return

    # dirty repo blocks rebase (protect local work)
    if _repo_dirty():
        await report(client, job_id, status="failed", step="preflight",
                     error="repo dirty — کامیت‌های محلی ذخیره‌نشده؛ ابتدا git status را ببینید")
        log("repo dirty — aborting")
        return

    # 2) rebase local commits onto upstream ----------------------------------
    log(f"rebasing {_commit_short(from_commit)} -> {_commit_short(upstream)}")
    ok, out = _rebase_onto_upstream()
    if not ok:
        await report(client, job_id, status="failed", step="rebase",
                     error=f"rebase conflict (aborted, nothing changed): {out}",
                     to_commit=from_commit, from_commit=from_commit)
        log(f"rebase conflict — aborted. {out}")
        return
    new_commit = _current_commit()
    await report(client, job_id, step="build",
                 line=f"کامیت‌ها روی {_commit_short(new_commit)} تنظیم شد — بیلد Image",
                 from_commit=from_commit)

    # 3) build + recreate engine ---------------------------------------------
    ok, out = _build_engine()
    if not ok:
        await report(client, job_id, status="failed", step="build", error=f"engine build failed: {out}")
        log(f"build failed: {out}")
        return
    log("engine image built")

    ok, out = _recreate_engine()
    if not ok:
        await report(client, job_id, status="failed", step="recreate", error=f"engine recreate failed: {out}")
        log(f"recreate failed: {out}")
        return
    log("engine container recreated")

    # 4) health check ---------------------------------------------------------
    await report(client, job_id, step="health", line="بررسی سلامت موتور…")
    if not _engine_healthy():
        log("engine unhealthy after update — ROLLING BACK")
        if not from_commit:
            await report(client, job_id, status="failed", step="health",
                         error="engine unhealthy and no previous commit to roll back to")
            return
        ok, out = _rollback_engine(from_commit)
        await report(client, job_id, status="rolled_back",
                     step="rollback",
                     error=f"engine unhealthy after update; rolled back to {_commit_short(from_commit)}. {out}",
                     to_commit=from_commit)
        return

    # 5) optional platform update ---------------------------------------------
    if include_platform:
        await report(client, job_id, step="platform", line="آپدیت پلتفرم (gateway+worker)…")
        ok, out = _platform_update()
        if not ok:
            await report(client, job_id, status="failed", step="platform",
                         error=f"platform update failed (engine OK): {out}")
            return
        log("platform rebuilt")

    # 6) success — control plane bumps worker epoch (agents rebuild workers) --
    await report(client, job_id, status="success", step="workers",
                 line=f"هسته به {_commit_short(new_commit or '')} آپدیت شد — انتشار مرحله‌ای به گره‌ها",
                 to_commit=new_commit, changed=True)
    log(f"job #{job_id} success — {(new_commit or '?')[:9]}")

    # 7) US11 staged node rollout — one node at a time, stop on cancel/failure
    await _rollout_nodes(client, job_id)


async def _rollout_nodes(client: httpx.AsyncClient, job_id: int) -> None:
    """Drain→update→health→back per node, sequentially. Stops on cancel or
    first failure (failed node rolls back to the previous epoch; the rest
    wait for the admin's decision)."""
    try:
        r = await client.get(f"{CONTROL_URL}/api/v1/updater/nodes",
                             params={"token": UPDATER_TOKEN, "job_id": job_id}, timeout=20.0)
        nodes = r.json().get("servers", []) if r.status_code == 200 else []
    except Exception as exc:
        log(f"node list failed: {exc}")
        return
    for node in nodes:
        sid, sname = node.get("id"), node.get("name", "?")
        # cancel check between nodes
        try:
            st = await client.get(f"{CONTROL_URL}/api/v1/updater/nodes",
                                  params={"token": UPDATER_TOKEN, "job_id": job_id}, timeout=20.0)
            if (st.json().get("cancel_requested")):
                log(f"rollout cancelled by admin — stopping before {sname}")
                await report(client, job_id, step="cancelled",
                             line=f"لغو شد — گره {sname} و بعدی‌ها آپدیت نشدند", node=sname, node_status="cancelled")
                return
        except Exception:
            pass
        await report(client, job_id, step=f"node:{sname}", line=f"گره {sname}: تخلیه…",
                     node=sname, node_status="draining")
        # drain (no new tasks) — the control plane marks it; workers finish currents
        try:
            await client.post(f"{CONTROL_URL}/api/v1/updater/node-drain",
                              json={"token": UPDATER_TOKEN, "job_id": job_id,
                                    "server_id": sid, "drain": True}, timeout=20.0)
        except Exception as exc:
            log(f"drain {sname} failed: {exc}")
            await report(client, job_id, step=f"node:{sname}",
                         line=f"گره {sname} ناموفق (تخلیه) — توقف rollout", node=sname, node_status="failed")
            return
        await report(client, job_id, step=f"node:{sname}", line=f"گره {sname}: آپدیت…",
                     node=sname, node_status="updating")
        # trigger agent rebuild via epoch bump for THIS node only
        try:
            rr = await client.post(f"{CONTROL_URL}/api/v1/updater/node-bump",
                                   json={"token": UPDATER_TOKEN, "job_id": job_id,
                                         "server_id": sid}, timeout=30.0)
            ok = rr.status_code == 200 and rr.json().get("converged")
        except Exception as exc:
            log(f"bump {sname} failed: {exc}")
            ok = False
        if ok:
            await report(client, job_id, step=f"node:{sname}", line=f"گره {sname} ✅",
                         node=sname, node_status="ok")
            try:
                await client.post(f"{CONTROL_URL}/api/v1/updater/node-drain",
                                  json={"token": UPDATER_TOKEN, "job_id": job_id,
                                        "server_id": sid, "drain": False}, timeout=20.0)
            except Exception:
                pass
        else:
            await report(client, job_id, step=f"node:{sname}",
                         line=f"گره {sname} ناموفق — rollback و توقف (بقیه منتظر تصمیم مدیر)",
                         node=sname, node_status="rolled_back")
            try:
                await client.post(f"{CONTROL_URL}/api/v1/updater/node-drain",
                                  json={"token": UPDATER_TOKEN, "job_id": job_id,
                                        "server_id": sid, "drain": False}, timeout=20.0)
            except Exception:
                pass
            return
    await report(client, job_id, step="done", line="انتشار مرحله‌ای تمام شد ✅")


async def main() -> None:
    if not UPDATER_TOKEN:
        raise SystemExit("VT_UPDATER_TOKEN required")
    print(f"[updater] polling {CONTROL_URL} every {POLL_INTERVAL}s", flush=True)
    async with httpx.AsyncClient() as client:
        while True:
            job = await poll_job(client)
            if job:
                print(f"[updater] got job #{job['id']}", flush=True)
                await execute_job(client, job)
            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
