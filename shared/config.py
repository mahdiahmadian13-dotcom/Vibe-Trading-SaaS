"""Vibe-Trading SaaS — Central Configuration"""

from __future__ import annotations

import os
from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """All settings from environment variables."""

    # --- Role ---
    ROLE: str = "central"  # central | worker | engine | bot

    # --- Gateway ---
    GATEWAY_HOST: str = "0.0.0.0"
    GATEWAY_PORT: int = 9000
    JWT_SECRET: str = Field(..., description="Secret for JWT signing")
    JWT_EXPIRY_MINUTES: int = 60 * 24 * 7  # 7 days
    API_KEY: str = Field(..., description="Shared API key for engine auth")

    # --- Redis ---
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_URL: str = "redis://redis:6379/0"

    # --- PostgreSQL ---
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "vibetrader"
    POSTGRES_USER: str = "vt"
    POSTGRES_PASSWORD: str = Field(..., description="PostgreSQL password")

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def DATABASE_URL_SYNC(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # --- Vibe-Trading Engine ---
    VIBE_ENGINE_URL: str = "http://engine:8899"
    VIBE_ENGINE_API_KEY: str = ""

    # --- Telegram ---
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_ALLOWED_USERS: str = ""  # comma-separated user IDs
    TELEGRAM_BOT_USERNAME: str = ""  # without @ — for referral deep-links
    TELEGRAM_CHANNEL_URL: str = ""   # e.g. https://t.me/yourchannel
    FLEET_ALERT_TG_IDS: str = ""     # US13 T040: comma-separated Telegram user IDs for server down/up alerts

    # --- Fleet (multi-server) ---
    FLEET_HEALTH_INTERVAL: int = 30
    FLEET_HEALTH_FAILS: int = 2
    # Fleet updater (one-click core updates)
    VT_UPDATER_TOKEN: str = ""
    # Public endpoints for node agents joining from other servers
    REDIS_URL_PUBLIC: str | None = None      # e.g. redis://206.245.166.14:6379/0
    DATABASE_URL_PUBLIC: str | None = None   # postgres URL reachable from node servers
    VIBE_ENGINE_URL_PUBLIC: str | None = None
    VIBE_NODE_SHARE_ENGINE_KEY: bool = False # share engine key with joined nodes
    VIBE_NODE_LOCAL_ENGINE: bool = True      # 002: workers use the node-local engine (http://engine:8899)
    SESSION_MIRROR_DIR: str = "/app/session_mirrors"  # T008: center-side session file mirror

    # --- Distributed engine fleet (002): node engines build from git, LLM key ships to nodes ---
    LLM_PROVIDER: str = ""       # e.g. openai (mirrors engine agent/.env LANGCHAIN_PROVIDER)
    LLM_MODEL: str = ""          # e.g. mimo-v2.5-pro (mirrors LANGCHAIN_MODEL_NAME)
    LLM_BASE_URL: str = ""       # e.g. https://opencode.ai/zen/go/v1 (mirrors OPENAI_BASE_URL)
    LLM_API_KEY: str = ""        # shared LLM key — sent to node engines ONLY via node state, never logged
    ENGINE_REPO: str = ""        # engine git repo for node builds (e.g. fork URL)
    ENGINE_COMMIT: str = ""      # pinned engine commit for node builds
    ENGINE_ENV_FILE: str = "/engine-env/agent.env"  # ro mount of /opt/Vibe-Trading/agent/.env into gateway (002 preflight live-read)

    # --- Worker ---
    WORKER_CONCURRENCY: int = 4
    WORKER_MAX_TASKS: int = 100
    WORKER_NAME: str = "worker-1"

    # --- Rate Limits ---
    RATE_LIMIT_MESSAGES_PER_MINUTE: int = 20
    RATE_LIMIT_SESSIONS_PER_DAY: int = 50
    MESSAGE_MIN_INTERVAL_SECONDS: float = 2.0

    # --- Anti-Abuse ---
    MAX_ACCOUNTS_PER_DEVICE: int = 1

    # --- Payment ---
    IDPAY_API_KEY: str = ""
    IDPAY_MERCHANT_ID: str = ""
    PUBLIC_BASE_URL: str = ""          # e.g. https://api.example.com (payment callback)
    PAYMENT_CALLBACK_URL: str = ""     # full override of callback URL (optional)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
