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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
