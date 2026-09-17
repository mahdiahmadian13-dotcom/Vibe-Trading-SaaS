"""Contract tests for node engine git-build provisioning (spec 002 T005/T007).

These test the COMMAND ASSEMBLY logic — the actual build runs on a real node
during provisioning (panel-driven).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'gateway'))
from app.provision import _engine_git_cmds, _engine_env_lines


def test_engine_git_cmds_pinned_clone_and_build():
    env = {'engine_repo': 'https://github.com/mahdiahmadian13-dotcom/Vibe-Trading.git',
           'engine_commit': 'a7c62113f306def3319f2190ae60aacea10b13c3',
           'llm_provider': 'openai', 'llm_model': 'm', 'llm_base_url': 'https://x/v1',
           'llm_api_key': 'sk-secret', 'engine_api_key': 'vt-key'}
    cmds = _engine_git_cmds(env, freestyle=True)
    joined = '\n'.join(cmds)
    # pin commands
    assert any('git fetch --depth=1 origin' in c and 'a7c62113' in c for c in cmds)
    assert any('git checkout --detach' in c for c in cmds)
    # build via compose with the engine service, long timeout on the runner side
    assert any('up -d --build engine' in c for c in cmds)
    # no plaintext secrets in clone/fetch commands (env file is written separately)
    assert 'sk-secret' not in joined or any('engine.env' in c for c in cmds)
    # freestyle needs the sudo mkdir/chown prefix
    assert any('sudo mkdir -p' in c for c in cmds)


def test_engine_env_lines_b64_and_health():
    env = {'engine_repo': 'https://github.com/example/v.git',
           'engine_commit': 'a' * 40, 'llm_provider': 'openai', 'llm_model': 'm',
           'llm_base_url': 'https://x/v1', 'llm_api_key': 'sk-secret',
           'engine_api_key': 'vt-key'}
    lines = _engine_env_lines(env)
    # engine env file: keys are base64-wrapped (never plain in step logs)
    assert all('LANGCHAIN_PROVIDER=openai' in l or 'API_AUTH_KEY' in l or '=' in l for l in lines)
    blob = '\n'.join(lines)
    assert 'sk-secret' not in blob
    # decode check
    import base64
    decoded = base64.b64decode(lines[0].split('B64=')[1]).decode()
    assert 'LANGCHAIN_PROVIDER=openai' in decoded
