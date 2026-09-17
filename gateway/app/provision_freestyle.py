"""Freestyle VM transport (gateway/app/provision_freestyle.py).

Freestyle's free VMs expose no plain SSH endpoint — the only supported
channel is the `freestyle` CLI (npx freestyle vm exec <vmId> -- -- <cmd>)
authenticated with FREESTYLE_API_KEY / FREESTYLE_TEAM env vars.

Transport interface used by provision.py is (exit_code, output) from
_run(conn, cmd, timeout). For freestyle we fake the "conn" with a small
executor object so every existing step works unchanged.
"""

from __future__ import annotations

import asyncio
import shutil

FREESTYLE_TIMEOUT_S = 900


class FreestyleError(RuntimeError):
    pass


def _cli() -> str:
    """Locate the freestyle CLI inside the gateway container."""
    for cand in (
        shutil.which("freestyle"),
        shutil.which("npx"),
    ):
        if cand:
            return cand
    raise FreestyleError("freestyle CLI یافت نشد (npx در ایمیج گیت‌وی نصب نیست)")


async def freestyle_exec(vm_id: str, cmd: str, timeout_s: int = FREESTYLE_TIMEOUT_S, team: str | None = None) -> tuple[int, str]:
    """Run `cmd` inside the VM via the freestyle CLI. Returns (rc, output)."""
    exe = _cli()
    team_flag: list[str] = []
    # Per-VM team wins (parsed from the pasted ssh line); else env FREESTYLE_TEAM.
    import os as _os
    eff_team = (team or "").strip() or (_os.getenv("FREESTYLE_TEAM") or "").strip()
    if eff_team:
        team_flag = ["--team", eff_team]
    if exe.endswith("npx"):
        argv = [exe, "-y", "freestyle@latest", "vm", "exec", vm_id, *team_flag, "--", "sh", "-lc", cmd]
    else:
        argv = [exe, "vm", "exec", vm_id, *team_flag, "--", "sh", "-lc", cmd]

    import os

    env = dict(os.environ)
    env.setdefault("FREESTYLE_OUTPUT", "pretty")
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "زمان اجرای freestyle exec تمام شد"
    text = (out or b"").decode("utf-8", "replace")
    return proc.returncode or 0, text[-4000:]


class FreestyleConn:
    """Duck-typed stand-in for the asyncssh connection object.

    provision._run(conn, cmd) calls conn.run(cmd) → needs .exit_status,
    .stdout, .stderr. We implement run() and pre-combined output.
    """

    def __init__(self, vm_id: str, team: str | None = None) -> None:
        self.vm_id = vm_id
        self.team = (team or "").strip() or None
        self.kind = "freestyle"

    async def run(self, cmd: str, timeout: int = FREESTYLE_TIMEOUT_S):
        rc, out = await freestyle_exec(self.vm_id, cmd, timeout_s=timeout, team=self.team)

        class _Res:
            def __init__(self, rc: int, out: str) -> None:
                self.exit_status = rc
                self.stdout = out
                self.stderr = ""

        return _Res(rc, out)


def parse_vm_id(ssh_host: str) -> str:
    """Extract the VM id from whatever the user pastes.

    Accepts: bare id, the full `npx freestyle vm ssh vm-XXX --team ...`
    command line, or vm-ids with extra tokens. Takes the LAST token that
    looks like a vm id.
    """
    import re

    text = (ssh_host or "").strip()
    if not text:
        raise FreestyleError("شناسه VM خالی است")
    # strip a wrapper command if pasted whole
    tokens = text.replace("\\n", " ").split()
    for tok in reversed(tokens):
        if tok.startswith("vm-") or re.fullmatch(r"[0-9a-f]{16,}", tok):
            return tok
    return tokens[0]


def parse_team(ssh_host: str) -> str | None:
    """Extract `--team <id>` from the pasted freestyle ssh line (if present).

    Each VM line can carry its own team — per-VM team wins over the
    FREESTYLE_TEAM env default in freestyle_exec().
    """
    import re

    m = re.search(r"--team[=\s]+([^\s]+)", ssh_host or "")
    return m.group(1).strip() if m else None
