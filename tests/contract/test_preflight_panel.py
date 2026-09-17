"""Preflight panel API contract. No real node or database mutations."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'gateway'))
from fastapi.testclient import TestClient
from app.main import app, _can_servers, get_settings


def test_admin_preflight_reports_missing_commit_without_secrets(monkeypatch):
    from app import node_env
    settings = SimpleNamespace(VIBE_NODE_SHARE_ENGINE_KEY=True,
        REDIS_URL_PUBLIC='redis://:test-secret@127.0.0.1:6379/0',
        DATABASE_URL_PUBLIC='postgresql://u:test-secret@127.0.0.1:5432/test',
        VIBE_ENGINE_URL_PUBLIC='http://127.0.0.1:8899')
    monkeypatch.setattr(node_env, 'llm_for_nodes', lambda s: {
        'llm_provider':'openai', 'llm_model':'test', 'llm_base_url':'https://example.com/v1',
        'llm_api_key':'test-secret', 'engine_repo':'https://example.com/repo.git',
        'engine_commit':'', 'from_file':True})
    app.dependency_overrides[_can_servers] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get('/api/v1/admin/fleet/preflight?auth_type=password')
        assert response.status_code == 200
        body = response.json()
        assert body['ready'] is False
        row = next(r for r in body['checks'] if r['check'] == 'engine_pin')
        assert row['ok'] is False
        assert 'کامیت' in row['msg_fa']
        assert 'test-secret' not in response.text
    finally:
        app.dependency_overrides.clear()
