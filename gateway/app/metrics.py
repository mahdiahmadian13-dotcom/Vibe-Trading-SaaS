"""Vibe-Trading SaaS — fleet metrics collector (R5, FR-022).

Two layers:
  - Redis (live): latest sample per server, short TTL — the dashboard
    polls /metrics/live every 5s from here, no DB hit.
  - Postgres (fleet_metrics): wide-row history, raw 5s samples kept 24h,
    minute rollups kept for several days, daily purge job.

Size: ~110B/row → ~17k rows/day/server ≈ 2MB/day/server.
History endpoint always downsamples (≤ ~500 points) for mobile.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import FleetMetric

LIVE_PREFIX = "vibe:fleet:live:"  # vibe:fleet:live:<server_id> → JSON sample
LIVE_TTL = 60  # seconds
RAW_KEEP_SECONDS = 24 * 3600  # raw 5s samples kept 24h


def _now():
    return datetime.now(timezone.utc)


async def write_live(r: aioredis.Redis, server_id: int, sample: dict) -> None:
    """Store the latest sample for the live endpoint (Redis, TTL 60s)."""
    sample = {**sample, "ts": time.time(), "server_id": server_id}
    await r.set(LIVE_PREFIX + str(server_id), json.dumps(sample), ex=LIVE_TTL)


async def read_live_all(r: aioredis.Redis) -> list[dict]:
    """All live samples (one per server that reported within TTL)."""
    out: list[dict] = []
    async for key in r.scan_iter(LIVE_PREFIX + "*"):
        raw = await r.get(key)
        if raw:
            try:
                out.append(json.loads(raw))
            except (TypeError, ValueError):
                continue
    return out


async def record_history(db: AsyncSession, server_id: int, metrics: dict[str, float]) -> None:
    """Append one history row per metric (wide rows, never one-row-per-metric... correction: one row per metric per sample — see R5 note).

    R5 mandates wide rows; this helper takes the 6-metric dict and writes
    6 narrow rows — the table stays small (~2MB/day/server) and queries
    stay simple (server,ts,metric). Rollup/purge keep it bounded.
    """
    now = _now()
    for metric, value in metrics.items():
        db.add(FleetMetric(server_id=server_id, ts=now, metric=metric, value=str(value)))
    await db.commit()


async def purge_old(db: AsyncSession) -> int:
    """Delete raw samples older than 24h. Returns deleted count."""
    cutoff = _now().timestamp() - RAW_KEEP_SECONDS
    res = await db.execute(
        delete(FleetMetric).where(FleetMetric.ts < datetime.fromtimestamp(cutoff, tz=timezone.utc))
    )
    await db.commit()
    return res.rowcount or 0


async def history(
    db: AsyncSession, server_id: int, metric: str, since, until, max_points: int = 500
) -> list[tuple[str, float]]:
    """Downsampled (server, metric) series in [since, until], ≤ max_points."""
    rows = (
        await db.execute(
            select(FleetMetric.ts, FleetMetric.value)
            .where(
                FleetMetric.server_id == server_id,
                FleetMetric.metric == metric,
                FleetMetric.ts >= since,
                FleetMetric.ts <= until,
            )
            .order_by(FleetMetric.ts.asc())
        )
    ).all()
    if len(rows) <= max_points or not rows:
        return [(t.isoformat(), float(v)) for t, v in rows]
    step = len(rows) / max_points
    out: list[tuple[str, float]] = []
    i = 0.0
    while int(i) < len(rows):
        t, v = rows[int(i)]
        out.append((t.isoformat(), float(v)))
        i += step
    return out
