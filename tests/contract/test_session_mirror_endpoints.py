"""Contract: center session-mirror endpoints (T008).

POST /api/v1/fleet/sessions/push  (X-Node-Token auth) → write {SESSION_MIRROR_DIR}/{id}/
GET  /api/v1/fleet/sessions/pull/{id} (X-Node-Token) → base64 files

Tested via the route functions directly (no lifespan: init_db/dispatcher would
need live Redis/PG).
"""
import sys
import pytest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'gateway'))
from fastapi import HTTPException
from app import main as gw

TOKEN = 'node-token-abc'
_FAKE_SERVER = SimpleNamespace(id=25, name='server-ommk', status='online', last_session_sync_at=None)


class FakeDB:
    async def execute(self, q):
        class R:
            def scalar_one_or_none(self): return _FAKE_SERVER
        return R()
    async def commit(self): pass


@pytest.fixture
def settings(tmp_path, monkeypatch):
    s = SimpleNamespace(SESSION_MIRROR_DIR=str(tmp_path))
    monkeypatch.setattr(gw, 'get_settings', lambda: s)
    return s


@pytest.fixture
def req():
    return SimpleNamespace(headers={'x-node-token': TOKEN})


def test_push_writes_files_pull_reads_back(tmp_path, settings, req, monkeypatch):
    files = {'session.json': 'eyJhIjoxfQ==', 'messages.jsonl': 'e30K',
             '../evil': 'x', 'attempts/a1/attempt.json': 'e30K'}
    r = gw.fleet_sessions_push.__wrapped__ if hasattr(gw.fleet_sessions_push, '__wrapped__') else gw.fleet_sessions_push
    import asyncio
    out = asyncio.new_event_loop().run_until_complete(
        gw.fleet_sessions_push(req, gw.SessionPushIn(session_id='s7c33e8bf86f6', node='server-ommk', files=files), FakeDB()))
    assert out['ok'] is True and out['written'] == 3  # ../evil skipped
    assert (tmp_path / 's7c33e8bf86f6' / 'session.json').read_text() == '{"a":1}'
    assert not (tmp_path / 'evil').exists()
    out2 = asyncio.new_event_loop().run_until_complete(
        gw.fleet_sessions_pull(req, 's7c33e8bf86f6', FakeDB()))
    assert out2['files']['session.json'] == 'eyJhIjoxfQ=='
    assert 'attempts/a1/attempt.json' in out2['files']


def test_push_rejects_bad_token(tmp_path, settings, req, monkeypatch):
    async def boom(token, db):
        raise HTTPException(404, 'توکن گره نامعتبر است')
    monkeypatch.setattr(gw, '_server_by_join_token', boom)
    import asyncio
    with pytest.raises(HTTPException) as e:
        asyncio.new_event_loop().run_until_complete(
            gw.fleet_sessions_pull(req, 's77', FakeDB()))
    assert e.value.status_code == 404


def test_push_rejects_bad_session_id(tmp_path, settings, req, monkeypatch):
    import asyncio
    with pytest.raises(HTTPException) as e:
        asyncio.new_event_loop().run_until_complete(
            gw.fleet_sessions_push(req, gw.SessionPushIn(session_id='../x', files={}), FakeDB()))
    assert e.value.status_code == 400
