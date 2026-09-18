"""Contract tests for T008: worker session pull-on-miss + write-through.

The worker:
- PULL: before POSTing a message, if the local engine 404s the session AND a
  center gateway mirror exists, restore the session files in front of the
  local engine (shared volume path), then retry once.
- PUSH: after a successful task, push that session's files to the center
  mirror (best-effort — failures never fail the task).
Center endpoints: POST /api/v1/fleet/sessions/push (X-Node-Token auth),
GET /api/v1/fleet/sessions/pull/{id}.
"""
import base64, json, sys, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'worker'))

import importlib
worker = importlib.import_module('app.main')


def _run(coro): return asyncio.new_event_loop().run_until_complete(coro)

def _json_dumps(obj): return json.dumps(obj)


def test_pull_on_miss_builds_correct_requests_and_writes_files(tmp_path, monkeypatch):
    calls = []
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, headers=None, json=None, timeout=None):
            calls.append((method, url, json))
            if url.endswith('/messages') and method == 'POST':
                if calls.count(('POST', url, json)) == 1:
                    return _resp(404, '{"detail":"not found"}')
                return _resp(200, '{"ok":true}')
            if '/fleet/sessions/pull/' in url:
                return _resp(200, _json_dumps({'files': {
                    'session.json': base64.b64encode(b'{"session_id":"s1"}').decode(),
                    'messages.jsonl': base64.b64encode(b'{}\n').decode()}}))
            return _resp(200, '{}')
        async def get(self, url, headers=None):
            return await self.request('GET', url, headers=headers)
    monkeypatch.setattr(worker.httpx, 'AsyncClient', FakeClient)
    monkeypatch.setattr(worker, 'ENGINE_URL', 'http://engine:8899')
    monkeypatch.setattr(worker, 'ENGINE_API_KEY', '')
    node_dir = tmp_path / 'sessions'
    monkeypatch.setattr(worker, '_node_sessions_dir', lambda: node_dir)
    monkeypatch.setattr(worker, '_node_token', lambda: 'node-token-abc')
    monkeypatch.setattr(worker, '_center_base', lambda: 'http://center')
    restored = _run(worker._pull_session_on_miss('s1'))
    assert restored is True
    assert (node_dir / 's1' / 'session.json').read_bytes() == b'{"session_id":"s1"}'
    assert (node_dir / 's1' / 'messages.jsonl').read_bytes() == b'{}\n'
    # pull hit the center mirror endpoint with the node token
    pulls = [c for c in calls if c[0] == 'GET' and '/fleet/sessions/pull/' in c[1]]
    assert len(pulls) == 1


def test_push_after_success_is_best_effort(tmp_path, monkeypatch):
    class BoomClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, *a, **k):
            raise OSError('network down')
        async def post(self, *a, **k):
            raise OSError('network down')
    monkeypatch.setattr(worker.httpx, 'AsyncClient', BoomClient)
    src = tmp_path / 's9'; src.mkdir(); (src / 'session.json').write_text('{}')
    monkeypatch.setattr(worker, '_node_sessions_dir', lambda: tmp_path)
    monkeypatch.setattr(worker, '_node_token', lambda: 'node-token')
    monkeypatch.setattr(worker, '_center_base', lambda: 'http://center')
    # must not raise
    _run(worker._push_session_to_center('s9'))


def _resp(status, text):
    r = type('R', (), {})()
    r.status_code = status
    r.text = text
    r.json = lambda: json.loads(text)
    return r
