"""Vibe-Trading SaaS — preflight checks for node provisioning (002).

Runs BEFORE `connect` on the CENTER (no node contact needed). Two modes:

- auto-fix what is safe (LLM values default from the center engine env file,
  VIBE_NODE_SHARE_ENGINE_KEY flip is reported, never silently persisted to
  the saas .env by the gateway — the panel shows what to set),
- report the rest in Persian with exact fix instructions.

Check list (each returns ok:bool + msg_fa + auto-fixed:bool):
  P1 center engine env file readable (LLM provider/model/base/key present)
  P2 ENGINE_REPO / ENGINE_COMMIT resolvable (settings or updater default)
  P3 VIBE_NODE_SHARE_ENGINE_KEY enabled (else node engines get no key)
  P4 REDIS_URL_PUBLIC reachable-shape (host:port, passworded since requirepass)
  P5 DATABASE_URL_PUBLIC non-empty
  P6 VIBE_ENGINE_URL_PUBLIC non-empty (legacy/fallback path for workers)
  P7 center disk usage < 90% (df / — mirror dir lives here)
  P8 FREESTYLE_API_KEY present when auth_type == freestyle
"""

from __future__ import annotations

import os
import shutil
from urllib.parse import urlparse

REQUIRED_LLM = ("llm_provider", "llm_model", "llm_base_url", "llm_api_key")


def _disk_pct() -> int | None:
    try:
        total, used, _ = shutil.disk_usage("/")
        if total:
            return round(used / total * 100)
    except Exception:
        pass
    return None


def run_preflight(settings, node_env: dict, auth_type: str = "") -> list[dict]:
    """Return [{check, ok, msg_fa, autofixed}]. Never raises, never logs secrets."""
    out: list[dict] = []

    def rec(check: str, ok: bool, msg_fa: str, autofixed: bool = False):
        out.append({"check": check, "ok": ok, "msg_fa": msg_fa, "autofixed": autofixed})

    # P1 — LLM values (live-read with Settings override; auto-resolve, nothing to persist)
    missing = [k for k in REQUIRED_LLM if not (node_env.get(k) or "").strip()]
    if node_env.get("from_file"):
        src = "فایل env انجین مرکزی"
    else:
        src = "تنظیمات .env مرکز"
    if not missing:
        rec("llm", True, f"کلید/تنظیمات LLM آماده شد ({src})", autofixed=True)
    else:
        rec("llm", False,
            f"مقادیر LLM ناقص است ({', '.join(missing)}) — نه در {src} هست نه در .env مرکز. "
            "اول در پنل انجین مرکزی (یا /opt/Vibe-Trading/agent/.env) کلید را ست کن.")

    # P2 — repo / commit
    repo = (node_env.get("engine_repo") or "").strip()
    commit = (node_env.get("engine_commit") or "").strip()
    if repo and commit:
        rec("engine_pin", True, f"سورس انجین پین شد ({commit[:12]})", autofixed=True)
    elif repo:
        rec("engine_pin", False,
            "کامیت پین انجین خالی است (ENGINE_COMMIT) — گره نمی‌داند کدام نسخه را بیلد کند. "
            "در .env مرکز ENGINE_COMMIT را ست کن (آخرین کامیت updater).")
    else:
        rec("engine_pin", False, "مخزن انجین (ENGINE_REPO) مشخص نیست.")

    # P3 — share flag
    if bool(getattr(settings, "VIBE_NODE_SHARE_ENGINE_KEY", False)):
        rec("share_key", True, "اشتراک کلید انجین با گره‌ها فعال است")
    else:
        rec("share_key", False,
            "VIBE_NODE_SHARE_ENGINE_KEY در .env مرکز false است — انجین گره بدون کلید بالا نمی‌آید. "
            "در .env مرکز آن را true کن و گیت‌وی را recreate کن.")

    # P4 — redis public
    rpub = (getattr(settings, "REDIS_URL_PUBLIC", "") or "").strip()
    try:
        u = urlparse(rpub)
        redis_ok = u.scheme.startswith("redis") and bool(u.hostname) and bool(u.port)
    except Exception:
        redis_ok = False
    if redis_ok:
        rec("redis_public", True, "آدرس عمومی Redis معتبر است")
    else:
        rec("redis_public", False,
            "REDIS_URL_PUBLIC نامعتبر/خالی است — ورکر گره به بروکر نمی‌رسد. "
            "مثال: redis://:PASSWORD@IP:6379/0 (با رمز واقعی، چون requirepass فعال است).")

    # P5 — postgres public
    dpub = (getattr(settings, "DATABASE_URL_PUBLIC", "") or "").strip()
    if dpub and ("@" in dpub) and (":5432" in dpub or "5433" in dpub):
        rec("pg_public", True, "آدرس عمومی Postgres معتبر است")
    else:
        rec("pg_public", False,
            "DATABASE_URL_PUBLIC خالی/نامعتبر است — ورکر گره crash-loop می‌شود "
            "(درس قبلی: Could not parse SQLAlchemy URL).")

    # P6 — engine public (fallback/legacy path)
    epub = (getattr(settings, "VIBE_ENGINE_URL_PUBLIC", "") or "").strip()
    if epub.startswith("http"):
        rec("engine_public", True, "آدرس عمومی انجین مرکزی (fallback) معتبر است")
    else:
        rec("engine_public", False,
            "VIBE_ENGINE_URL_PUBLIC خالی است — مسیر fallback به مرکز کار نمی‌کند.")

    # P7 — disk
    pct = _disk_pct()
    if pct is None:
        rec("disk", True, "وضعیت دیسک خوانده نشد (رد شد)")
    elif pct < 90:
        rec("disk", True, f"دیسک مرکز {pct}٪ — فضا کافی است")
    else:
        rec("disk", False,
            f"دیسک مرکز {pct}٪ پر است — قبل از نصب `docker image prune -af` بزن "
            "(خطر: Postgres/Redis روی دیسک پر می‌خوابند).")

    # P8 — freestyle key
    if (auth_type or "") == "freestyle":
        fkey = (os.getenv("FREESTYLE_API_KEY", "") or "").strip()
        if fkey:
            rec("freestyle", True, "کلید freestyle در گیت‌وی موجود است")
        else:
            rec("freestyle", False,
                "FREESTYLE_API_KEY در env گیت‌وی نیست — در .env مرکز ست کن و گیت‌وی را recreate کن.")
    return out


def preflight_failures(report: list[dict]) -> list[dict]:
    return [r for r in report if not r.get("ok")]
