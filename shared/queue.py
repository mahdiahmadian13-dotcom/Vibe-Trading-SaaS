"""Shared ARQ queue helpers — enqueue tasks from gateway, run in worker."""

from __future__ import annotations

import os
from typing import Optional

from arq import create_pool
from arq.connections import RedisSettings
from arq.jobs import Job

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(REDIS_URL))
    return _pool


async def enqueue(task_name: str, *args, **kwargs) -> Optional[Job]:
    pool = await get_pool()
    return await pool.enqueue_job(task_name, *args, **kwargs)


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
