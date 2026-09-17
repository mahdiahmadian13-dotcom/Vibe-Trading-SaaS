"""Vibe-Trading SaaS — live read of the CENTER engine's env for node provisioning (002).

The gateway container gets a read-only mount:
  /opt/Vibe-Trading/agent/.env  →  /engine-env/agent.env  (see docker-compose.yml)

node_state / preflight read LLM + repo values LIVE from that file, with the
saas .env Settings (LLM_PROVIDER / LLM_MODEL / LLM_BASE_URL / LLM_API_KEY /
ENGINE_REPO / ENGINE_COMMIT) as overrides. Result: adding a server from the
panel never needs a manual key-copy step — rotation of the center engine key
propagates to the next provision automatically.

Secrets from here must NEVER be logged or returned to non-node callers.
"""

from __future__ import annotations

import os

ENGINE_FORK_DEFAULT = "https://github.com/mahdiahmadian13-dotcom/Vibe-Trading.git"


def read_center_engine_env(path: str | None = None) -> dict:
    """Parse KEY=VALUE lines. Missing file → {} (caller decides)."""
    p = (path or os.getenv("ENGINE_ENV_FILE", "/engine-env/agent.env")).strip()
    out: dict = {}
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return {}
    return out


def llm_for_nodes(settings) -> dict:
    """Effective node-bound engine config: Settings override, engine file fallback."""
    file_env = read_center_engine_env(getattr(settings, "ENGINE_ENV_FILE", ""))

    def pick(settings_key: str, *file_keys: str) -> str:
        v = (getattr(settings, settings_key, "") or "").strip()
        if v:
            return v
        for fk in file_keys:
            if file_env.get(fk):
                return str(file_env[fk]).strip()
        return ""

    return {
        "llm_provider": pick("LLM_PROVIDER", "LANGCHAIN_PROVIDER"),
        "llm_model": pick("LLM_MODEL", "LANGCHAIN_MODEL_NAME"),
        "llm_base_url": pick("LLM_BASE_URL", "OPENAI_BASE_URL"),
        "llm_api_key": pick("LLM_API_KEY", "OPENAI_API_KEY"),
        "engine_api_key": (settings.VIBE_ENGINE_API_KEY or "").strip()
        or str(file_env.get("API_AUTH_KEY", "")).strip(),
        "engine_repo": (settings.ENGINE_REPO or "").strip() or ENGINE_FORK_DEFAULT,
        "engine_commit": (settings.ENGINE_COMMIT or "").strip(),
        "from_file": bool(file_env),
    }
