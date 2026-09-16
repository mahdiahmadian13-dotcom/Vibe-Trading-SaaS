"""Vibe-Trading SaaS — 30-day retention janitor (T051, FR-019).

Daily job: null out heavy payloads (params/result) on completed
tasks older than 30 days, keep the lightweight summary row and
Postgres history. Safe to run in a loop: never touches running/
pending tasks, never deletes rows younger than the horizon, and
logs a count so ops can audit.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import get_settings
from shared.models import Task, get_session_factory

logger = logging.getLogger("retention")


async def _purge_completed(db: AsyncSession, horizon: datetime) -> int:
    """Null out heavy payloads on completed tasks past the horizon."""
    rows = (
        await db.execute(
            text(
                "UPDATE tasks SET params = NULL, result = NULL "
                "WHERE status = 'completed' "
                "AND completed_at < :horizon "
                "AND (params IS NOT NULL OR result IS NOT NULL)"
            ),
            {"horizon": horizon},
        )
    ).rowcount

    if rows:
        logger.info("retention: cleared payloads on %d completed tasks older than %s", rows, horizon.date())
    return rows


async def run_retention() -> dict:
    """Run one retention cycle. Returns counts for logging."""
    settings = get_settings()
    horizon = datetime.now(timezone.utc) - settings.retention_days
    async with get_session_factory()(settings.database_url) as db:
        purged = await _purge_completed(db, horizon)
        await db.commit()
    return {"payloads_cleared": purged}


if __name__ == "__main__":
    asyncio.run(run_retention())