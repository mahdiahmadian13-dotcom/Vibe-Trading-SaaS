"""Vibe-Trading SaaS — auto-provision engine (US-09, FR-020/021).

Installs a FULL node (docker → private net → engine → workers → agent →
capability bench) over SSH with asyncssh, as a background asyncio job —
never inside the request cycle. Stepwise state machine persisted on
server_nodes (provision_state/step/log) + provision_jobs row.

Steps: connect → docker → net → engine → workers → agent → bench → done
Retry resumes from the first failed step (successful steps are skipped).
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone

from sqlalchemy import select

from shared.models import ProvisionJob, ServerNode, _utcnow

from app.crypto import decrypt_secret

STEPS: list[tuple[str, str]] = [
    ("preflight", "پیش‌نیازهای مرکز"),
    ("connect", "برقراری اتصال SSH"),
    ("docker", "نصب و تأیید داکر"),
    ("net", "اتصال شبکه خصوصی"),
    ("engine", "نصب انجین محلی"),
    ("workers", "راه‌اندازی ورکرها"),
    ("agent", "راه‌اندازی ایجنت"),
    ("bench", "تست توان سرور"),
    ("done", "اتمام"),
]

STEP_TIMEOUT = 600  # seconds per step
_SEMAPHORE = asyncio.Semaphore(10)  # max concurrent installs (R1)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fa_ok(step: str) -> str:
    return {s: f"{label} انجام شد" for s, label in STEPS}.get(step, step)


class ProvisionError(Exception):
    """A step failed with a Persian message safe for the panel (no secrets)."""


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

async def _connect(server: ServerNode):
    """Open a transport. For freestyle VMs this is a CLI-based fake conn;
    otherwise an asyncssh connection. Plaintext lives only in this scope."""
    if (server.ssh_auth_type or "") == "freestyle":
        from app.provision_freestyle import FreestyleConn, parse_team, parse_vm_id
        return FreestyleConn(parse_vm_id(server.ssh_host or ""), team=parse_team(server.ssh_host or ""))

    import asyncssh

    secret = decrypt_secret(server.ssh_secret, server.id)
    kwargs: dict = {
        "host": server.ssh_host,
        "username": server.ssh_user,
        "known_hosts": None,  # Tailscale/private-net: fingerprint pinned below
        "connect_timeout": 15,
        "login_timeout": 15,
    }
    try:
        if server.ssh_auth_type == "key":
            kwargs["client_keys"] = [asyncssh.import_private_key(secret)]
        else:
            kwargs["password"] = secret
        # hard outer timeout: unroutable IPs must fail fast, never hang the job
        async with asyncio.timeout(30):
            conn = await asyncssh.connect(**kwargs)
    finally:
        secret = ""  # drop plaintext ASAP
    return conn


async def _run(conn, cmd: str, timeout: int = STEP_TIMEOUT) -> tuple[int, str]:
    """Run a remote command; returns (exit_code, output tail)."""
    try:
        result = await asyncio.wait_for(conn.run(cmd), timeout=timeout)
        out = (result.stdout or "") + (result.stderr or "")
        return result.exit_status or 0, out[-2000:]
    except asyncio.TimeoutError:
        return 124, "زمان اجرای دستور تمام شد"


# ---------------------------------------------------------------------------
# Step implementations (each idempotent — safe to re-run on retry)
# ---------------------------------------------------------------------------

async def _step_preflight(server: ServerNode) -> None:
    """002: center-side prerequisite gate — runs BEFORE connect (no node contact).

    Auto-resolves what is safe (live-read LLM/repo from the center engine env
    file via app.node_env), reports the rest in Persian with exact fixes.
    Idempotent (pure checks, no side effects on the node).
    """
    from shared.config import get_settings
    from app.node_env import llm_for_nodes
    from app.preflight import preflight_failures, run_preflight

    settings = get_settings()
    node_env = llm_for_nodes(settings)
    report = run_preflight(settings, node_env, auth_type=(server.ssh_auth_type or ""))
    bad = preflight_failures(report)
    if bad:
        details = "؛ ".join(r["msg_fa"] for r in bad)
        raise ProvisionError(f"پیش‌نیازهای مرکز کامل نیست: {details}")


async def _step_connect(server: ServerNode, conn_holder: dict) -> None:
    conn_holder["conn"] = await _connect(server)


async def _step_docker(server: ServerNode, conn) -> None:
    rc, out = await _run(conn, "docker --version || (curl -fsSL https://get.docker.com | sh)")
    if rc != 0:
        raise ProvisionError(f"نصب داکر ناموفق بود: {out[-300:]}")
    rc, out = await _run(conn, "docker compose version || docker-compose version")
    if rc != 0:
        raise ProvisionError("افزونه docker compose یافت نشد")


def _public_probe_cmds(settings) -> list[str]:
    """Commands the NODE runs to prove passworded public access to the center.

    Secrets are interpolated from the center .env into the remote command and
    are never logged (provision step logs store only the Persian verdict).
    """
    from urllib.parse import unquote, urlparse
    base = (getattr(settings, "VIBE_ENGINE_URL_PUBLIC", "") or
            getattr(settings, "PUBLIC_BASE_URL", "") or "").rstrip("/")
    cmds = [f"curl -fsS --max-time 10 {base}/live || echo NO_ROUTE"]
    try:
        line = ""
        env_path = os.environ.get("APP_ENV_FILE") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
        for raw in open(env_path, encoding="utf-8"):
            if raw.startswith("REDIS_URL_PUBLIC="):
                line = raw.strip()
                break
        if line:
            u = urlparse(line.split("=", 1)[1])
            pw = unquote(u.password or "")
            if pw:
                import shlex as _shlex
                pwq = _shlex.quote(pw)
                # raw TCP redis: AUTH then PING in one write; expect -WRONGPASS OR +OK...PONG
                # (grep matches the good path; WRONGPASS/NOAUTH echo REDIS_FAIL)
                cmds.append(f"bash -c 'exec 3<>/dev/tcp/{u.hostname}/{u.port}; "
                            f"printf \"AUTH {pwq}\\r\\nPING\\r\\n\" >&3; "
                            f"timeout 6 dd bs=1 count=30 <&3 2>/dev/null | tr -d \"\\\\r\\\\n\" | grep -q PONG' && echo REDIS_OK || echo REDIS_FAIL")
    except Exception:
        pass
    return cmds


async def _step_net(server: ServerNode, conn) -> None:
    from shared.config import get_settings as _get_settings
    _settings_snapshot = lambda: _get_settings()
    # private-net check: the control plane must be reachable on its Tailscale/VPN IP
    rc, out = await _run(conn, "which tailscale && tailscale status | head -3 || echo NO_TAILSCALE")
    if "NO_TAILSCALE" in out:
        # No tailnet: prove passworded public access instead (all nodes on
        # public net — Redis is requirepass'ed, engine key-gated).
        failures: list[str] = []
        for cmd in _public_probe_cmds(_settings_snapshot()):
            rc2, out2 = await _run(conn, cmd, timeout=30)
            if rc2 != 0 or "NO_ROUTE" in out2 or "FAIL" in out2:
                failures.append(out2.strip()[-120:] or f"rc={rc2}")
        if failures:
            raise ProvisionError(
                "شبکه خصوصی (Tailscale) نیست و دسترسی رمزدار عمومی به مرکز هم برقرار نشد — "
                "ابتدا دسترسی سرور به مرکز را بررسی کنید")
        return  # public routing OK — Tailscale not required
    if server.tailscale_ip:
        rc, out = await _run(conn, f"ping -c2 -W3 {server.tailscale_ip}")
        if rc != 0:
            raise ProvisionError("سرور به IP خصوصی مرکز دسترسی ندارد")


def _engine_env_lines(node_env: dict) -> list[str]:
    """Engine container env file content, base64-wrapped so the secret never
    lands in provision logs or the remote command line as plaintext."""
    import base64
    inner = (
        f"LANGCHAIN_PROVIDER={node_env.get('llm_provider', '')}\n"
        f"LANGCHAIN_MODEL_NAME={node_env.get('llm_model', '')}\n"
        f"OPENAI_BASE_URL={node_env.get('llm_base_url', '')}\n"
        f"OPENAI_API_KEY={node_env.get('llm_api_key', '')}\n"
        f"API_AUTH_KEY={node_env.get('engine_api_key', '')}\n"
    )
    return [f"B64={base64.b64encode(inner.encode()).decode()}"]


def _engine_git_cmds(node_env: dict, freestyle: bool) -> list[str]:
    """Clone/fetch the pinned engine commit on the node + build the engine image.

    Idempotent: a checkout at the right commit short-circuits the fetch.
    Secrets are written separately via _engine_env_lines (base64), never here.
    """
    repo = (node_env.get('engine_repo') or '').strip()
    commit = (node_env.get('engine_commit') or '').strip()
    if not repo or not commit:
        raise ProvisionError("ENGINE_REPO/ENGINE_COMMIT کامل نیست — بیلد انجین ممکن نیست")
    path = '/opt/vibe-engine'
    prefix = f"sudo mkdir -p {path} && sudo chown -R $(whoami) {path} && " if freestyle else ""
    cmds = [
        f"{prefix}mkdir -p {path} && cd {path} && "
        f"(git rev-parse --quiet --verify {commit} >/dev/null 2>&1 || "
        f"(git rev-parse --is-inside-work-tree >/dev/null 2>&1 || git init -q) && "
        f"(git remote get-url origin >/dev/null 2>&1 || git remote add origin {repo}) && "
        f"git fetch --depth=1 origin {commit} && git checkout --detach FETCH_HEAD)",
        f"cd {path} && git rev-parse HEAD | grep -q {commit[:12]}",
        f"cd {path} && docker build -t vibe-node-engine:local . && "
        f"docker compose -f /opt/vibe-node/docker-compose.node.yml up -d --build engine",
    ]
    return cmds


async def _step_engine(server: ServerNode, conn, bundle_url: str) -> None:
    """Full node: bundle (compose + agent + worker) THEN engine git-build."""
    is_freestyle = getattr(conn, "kind", "") == "freestyle"
    # Freestyle VMs run as the non-root 'ubuntu' user (docker group, sudo NOPASSWD):
    # /opt is root-owned → create via sudo then hand ownership to the user.
    prefix = "sudo mkdir -p /opt/vibe-node && sudo chown -R $(whoami) /opt/vibe-node && " if is_freestyle else ""
    rc, out = await _run(
        conn,
        f"{prefix}mkdir -p /opt/vibe-node && cd /opt/vibe-node && "
        f"curl -fsSL '{bundle_url}' -o node.tar.gz && tar xzf node.tar.gz",
        timeout=300,
    )
    if rc != 0:
        raise ProvisionError(f"دریافت باندل گره ناموفق بود: {out[-300:]}")

    # git must exist for the pinned engine build (fresh VMs have none)
    rc, out = await _run(conn, "command -v git || (apt-get update -qq && apt-get install -y -qq git)", timeout=300)
    if rc != 0:
        raise ProvisionError(f"نصب git روی گره ناموفق بود: {out[-200:]}")

    # --- 002: engine git-build at the pinned commit (panel-driven, no manual SSH) ---
    from shared.config import get_settings
    from app.node_env import llm_for_nodes
    node_env = llm_for_nodes(get_settings())
    import base64 as _b64

    # write the engine env file (base64-wrapped; decoded on the node)
    b64 = _engine_env_lines(node_env)[0].split('B64=', 1)[1]
    rc, out = await _run(
        conn,
        f"echo {b64} | base64 -d > /opt/vibe-node/engine.env && chmod 600 /opt/vibe-node/engine.env",
        timeout=30,
    )
    if rc != 0:
        raise ProvisionError(f"نوشتن env انجین گره ناموفق بود: {out[-200:]}")

    for cmd in _engine_git_cmds(node_env, freestyle=is_freestyle):
        rc, out = await _run(conn, cmd, timeout=900)
        if rc != 0:
            raise ProvisionError(f"دریافت/پین سورس انجین ناموفق بود: {out[-300:]}")

    rc, out = await _run(
        conn,
        "cd /opt/vibe-engine && "
        "docker build -t vibe-node-engine:local . ",
        timeout=1800,
    )
    if rc != 0:
        raise ProvisionError(f"بیلد انجین روی گره ناموفق بود: {out[-300:]}")


async def _step_workers(server: ServerNode, conn) -> None:
    rc, out = await _run(
        conn,
        "cd /opt/vibe-node && docker compose -f docker-compose.node.yml up -d --build worker",
        timeout=600,
    )
    if rc != 0:
        raise ProvisionError(f"بالا آوردن ورکرها ناموفق بود: {out[-300:]}")


async def _step_agent(server: ServerNode, conn) -> None:
    rc, out = await _run(
        conn,
        "cd /opt/vibe-node && docker compose -f docker-compose.node.yml up -d agent",
        timeout=300,
    )
    if rc != 0:
        raise ProvisionError(f"بالا آوردن ایجنت ناموفق بود: {out[-300:]}")


async def _step_bench(server: ServerNode, conn) -> dict:
    rc, out = await _run(
        conn,
        "nproc; free -g | awk '/Mem:/{print $2}'; df -BG / | awk 'NR==2{print $4}'; docker --version",
        timeout=60,
    )
    if rc != 0:
        raise ProvisionError("تست توان اجرا نشد")
    lines = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    try:
        bench = {
            "cpu": int(lines[-4]),
            "mem_gb": int(lines[-3]),
            "disk_free": lines[-2],
            "docker": lines[-1][:60],
            "verdict": "ok" if int(lines[-4]) >= 2 else "weak",
        }
    except (ValueError, IndexError):
        bench = {"raw": out[-500:], "verdict": "unknown"}
    return bench


_STEP_FUNCS = {
    "connect": _step_connect,
    "docker": _step_docker,
    "net": _step_net,
    "engine": _step_engine,
    "workers": _step_workers,
    "agent": _step_agent,
    "bench": _step_bench,
    "preflight": None,  # handled inline in runner (center-side, no conn)
}


# ---------------------------------------------------------------------------
# Job runner
# ---------------------------------------------------------------------------

async def run_provision_job(job_id: int, bundle_url_fn=None) -> None:
    """Background task: run/continue a provision job to done/failed."""
    import logging

    from shared.models import _session_factory

    log = logging.getLogger("provision")

    async with _SEMAPHORE:
        async with _session_factory() as db:
            job = (await db.execute(select(ProvisionJob).where(ProvisionJob.id == job_id))).scalar_one_or_none()
            if not job or job.status != "running":
                return
            server = (await db.execute(select(ServerNode).where(ServerNode.id == job.server_id))).scalar_one_or_none()
            if not server:
                job.status = "failed"
                await db.commit()
                return

            steps: list[dict] = list(job.steps or [])
            done_steps = {s["step"] for s in steps if s.get("ok") is True}
            conn_holder: dict = {}
            conn = None
            bench: dict = {}
            try:
                for step, _label in STEPS:
                    if step == "done":
                        continue
                    if step in done_steps:
                        continue
                    job.current_step = step
                    server.provision_state = "running"
                    server.provision_step = step
                    await db.commit()
                    try:
                        if step == "preflight":
                            # center-side gate — no node connection needed
                            await _step_preflight(server)
                        elif step == "connect":
                            await _step_connect(server, conn_holder)
                            conn = conn_holder.get("conn")
                        elif step == "engine":
                            url = bundle_url_fn(server) if bundle_url_fn else ""
                            if conn is None:  # retried job skipped the saved connect step
                                await _step_connect(server, conn_holder)
                                conn = conn_holder.get("conn")
                            await _step_engine(server, conn, url)
                        elif step == "bench":
                            if conn is None:  # same reconnect guard as engine
                                await _step_connect(server, conn_holder)
                                conn = conn_holder.get("conn")
                            bench = await _step_bench(server, conn)
                        else:
                            if conn is None:  # same reconnect guard as engine
                                await _step_connect(server, conn_holder)
                                conn = conn_holder.get("conn")
                            await _STEP_FUNCS[step](server, conn)
                        steps.append({"step": step, "ts": _now_iso(), "ok": True, "msg_fa": _fa_ok(step)})
                    except ProvisionError as exc:
                        steps.append({"step": step, "ts": _now_iso(), "ok": False, "msg_fa": str(exc)})
                        job.steps = list(steps)
                        job.status = "failed"
                        job.finished_at = _utcnow()
                        server.provision_state = "failed"
                        server.provision_step = step
                        server.provision_log = list(steps)
                        server.status = "offline"  # half-installed nodes get no traffic
                        await db.commit()
                        return
                    except Exception as exc:
                        # transport-level failure (timeout/unreachable/auth — including
                        # builtin TimeoutError from asyncio.timeout which is NOT an
                        # alias of asyncio.TimeoutError on all builds): Persian
                        # message in panel, technical detail in server log, no secrets
                        if step == "preflight":
                            msg = f"خطای داخلی پیش‌نیازها: {type(exc).__name__}"
                        else:
                            msg = "اتصال SSH برقرار نشد (تایم‌اوت/در دسترس نبودن/احراز ناموفق)"
                        log.warning("provision job=%s server=%s step=%s failed: %s: %s",
                                    job_id, server.name, step, type(exc).__name__, str(exc)[:200])
                        steps.append({"step": step, "ts": _now_iso(), "ok": False, "msg_fa": msg})
                        job.steps = list(steps)
                        job.status = "failed"
                        job.finished_at = _utcnow()
                        server.provision_state = "failed"
                        server.provision_step = step
                        server.provision_log = list(steps)
                        server.status = "offline"
                        await db.commit()
                        return
                    job.steps = list(steps)
                    await db.commit()

                # all steps ok
                if bench:
                    server.capability = bench
                    server.capability_warning = bench.get("verdict") != "ok"
                job.steps = list(steps)
                job.current_step = "done"
                job.status = "ready"
                job.finished_at = _utcnow()
                server.provision_state = "ready"
                server.provision_step = "done"
                server.provision_log = list(steps)
                await db.commit()
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                # belt & braces: drop any lingering plaintext refs
                conn_holder.clear()


def start_provision_job(server_id: int, triggered_by: int | None, db) -> ProvisionJob:
    """Create a running job row (called from the request cycle; runner is spawned by main)."""
    job = ProvisionJob(server_id=server_id, status="running", current_step="connect",
                       steps=[], triggered_by=triggered_by)
    return job


async def retry_provision_job(job: ProvisionJob, db) -> None:
    """Re-arm a failed job: keep successful steps, rerun from the failed one."""
    job.status = "running"
    job.finished_at = None
    # drop the failed tail — done steps stay, runner skips them
    steps = [s for s in (job.steps or []) if s.get("ok") is True]
    job.steps = list(steps)
    await db.commit()
