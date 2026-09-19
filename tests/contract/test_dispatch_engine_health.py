"""Contract: T009 dispatcher engine-health filter + central fallback.

Rule: workers whose host server has engine_healthy=False never get NEW jobs
(a node whose local engine is down cannot execute a chat task — the job would
spin until timeout). The dispatcher route must behave exactly like the
existing stale-worker filter: candidate pool shrinks, job spills to the
fallback queue (reaper rescue path) which lands on a healthy worker.
"""
import sys
import asyncio
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'gateway'))
from app import dispatch as dp


def _worker(name: str, fresh: bool = True, ready: bool = True) -> dp.WorkerInfo:
    from datetime import datetime, timezone
    hb = datetime.now(timezone.utc).isoformat() if fresh else "2020-01-01T00:00:00+00:00"
    return dp.WorkerInfo(name=name, status="ready" if ready else "busy",
                         heartbeat_at=hb)


class _FakeDB:
    def __init__(self, rows): self._rows = rows
    async def execute(self, q): 
        class R:
            def all(self): return self._rows
        R._rows = self._rows
        return R()
    async def commit(self): pass


def test_unhealthy_engine_server_workers_excluded(monkeypatch):
    # worker "bad" lives on a server with engine_healthy=False → excluded;
    # healthy worker still routable.
    rows = [("bad",)]
    async def fake_blocked(self):
        return {"bad"}
    monkeypatch.setattr(dp.Dispatcher, "_blocked_server_workers", fake_blocked)
    async def fake_workers(self):
        return [_worker("bad"), _worker("good")]
    monkeypatch.setattr(dp.Dispatcher, "get_workers", fake_workers)
    async def fake_load(self, w): return 0
    monkeypatch.setattr(dp.Dispatcher, "worker_load", fake_load)

    got = asyncio.new_event_loop().run_until_complete(dp.Dispatcher().pick_worker())
    assert got is not None and got.name == "good"


def test_all_unhealthy_engine_falls_to_fallback(monkeypatch):
    async def fake_blocked(self):
        return {"bad"}
    monkeypatch.setattr(dp.Dispatcher, "_blocked_server_workers", fake_blocked)
    async def fake_workers(self):
        return [_worker("bad")]
    monkeypatch.setattr(dp.Dispatcher, "get_workers", fake_workers)
    got = asyncio.new_event_loop().run_until_complete(dp.Dispatcher().pick_worker())
    assert got is None  # → dispatch() routes to fallback queue


def test_blocked_set_queries_engine_healthy_column(monkeypatch):
    """The blocked-workers query must reference ServerNode.engine_healthy."""
    import inspect
    src = inspect.getsource(dp.Dispatcher._blocked_server_workers)
    assert "engine_healthy" in src
