# Implementation Plan: ناوگان انجین توزیع‌شده (full-node واقعی)

**Branch**: `002-distributed-engine-fleet` | **Date**: 2026-09-16 | **Spec**: [spec.md](spec.md)

**Input**: `specs/002-distributed-engine-fleet/spec.md` (۴ استوری، ۱۰ FR، تصمیم‌های Q1–Q6 + انتخاب «بیلد انجین روی گره از git»)

## Summary

هر ۶ گره freestyle از thin-worker به **full-node واقعی** تبدیل می‌شوند: `engine` لوکال (بیلد از فورک روی خود گره) + `worker`ها (فقط لوکال) + `agent` (reconcile + heartbeat سلامت انجین). سشن‌ها با **write-through توسط ورکر** به مرکز sync می‌شوند و با **pull-on-miss** روی گره دیگر ادامه پیدا می‌کنند. انجین مرکزی می‌ماند (legacy + fallback). کلید AI فقط در env کانتینر انجین (۷ کانتینر به‌جای ۱).

## تصمیم‌های قفل‌شده (ورودی plan)

| # | موضوع | انتخاب |
|---|-------|--------|
| Q1 | کلید AI روی گره‌ها | الف — ارسال به هر گره |
| Q2 | سشن | ب — شناور (نه چسبیده) |
| Q3 | رول‌اوت | ب — هر ۶ با هم |
| Q4 | سینک سشن | الف — write-through فایل به مرکز |
| Q5 | انجین مرکزی | الف — می‌ماند (legacy + fallback) |
| Q6 | ورکر→انجین | الف — فقط لوکال خود + reaper |
| Q7 | انتقال انجین به گره | الف — **بیلد روی گره از git** (نه رجیستری) |

## یافته‌های تحقیق (اندازه‌گیری واقعی، نه حدس)

1. **گره‌ها جا دارند**: ۴ vCPU، ۸GB RAM، ۲۴GB دیسک خالی (capability جدول server_nodes + تست freestyle exec روی serv1).
2. **اینترنت گره باز است**: github/dockerhub/ghcr هر سه ۰.۲s. پس `git clone` + `docker build` روی گره ممکن است.
3. **ایمیج آماده نداریم**: `vibe-trading-vibe-trading:latest` فقط روی مرکز است؛ هیچ رجیستری عمومی/خصوصی نداریم و کانال `freestyle exec` فقط متن است (انتقال ۱.۸GB ناممکن). → بیلد روی گره تنها راه بدون زیرساخت جدید.
4. **سورس انجین ۴۷۱MB** (`/opt/Vibe-Trading` با frontend؛ بدون frontend کمتر). بیلد اول روی VM چهار هسته‌ای ۱۰–۲۰ دقیقه تخمین زده می‌شود.
5. **سشن‌ها فایل‌اند**: مسیر زنده داخل کانتینر `/home/vibe/.vibe-trading/sessions` (231 سشن = ۱۵MB، هر سشن ~۳۲KB: `session.json` + `messages.jsonl` + `attempts/` + `run_manifest.json`).
6. **`SessionStore.get_session` کش ندارد** — هر بار از دیسک می‌خواند. پس گذاشتن فایل جلوی انجین لوکال بدون ری‌استارت جواب می‌دهد.
7. **کلید LLM فقط در `/opt/Vibe-Trading/agent/.env`** است (LANGCHAIN_PROVIDER/MODEL_NAME + OPENAI_API_KEY/BASE_URL + API_AUTH_KEY). در `.env` ساس (`/root/vibe-trading-saas/.env`) هیچ متغیر LLM نیست — باید اضافه شود تا گیت‌وی بتواند به state گره بدهد.
8. **سؤال سوم (مقصر ۱۹ Timeout: انجین یا رله؟) عمداً به فاز تست موکول شد** — با ۶ انجین و کلید مشترک، اگر رله rate-limit کند تست فلاد نشان می‌دهد. طراحی طوری است که این را آشکار می‌کند (متریک per-engine).

## Technical Context

**Language/Version**: Python 3.11 (gateway/worker/agent) + TypeScript React (webapp، فقط بج سلامت/سینک)

**Primary Dependencies**: FastAPI، ARQ + Redis (رمزدار)، Postgres + asyncpg، httpx، Docker Compose روی گره (engine + worker + agent)

**Storage**: Postgres (چند ستون جدید روی `server_nodes`/`vibe_sessions`)؛ Redis (بدون تغییر)؛ دیسک مرکز (`/app/session_mirrors/{id}/` جدید)؛ دیسک گره (volume مشترک `vibe-sessions` بین engine و worker)

**Testing**: pytest + E2E روی سرور واقعی (فلاد ۶۰ تسک چت، kill-میدانی، پنل) — اصل I قانون اساسی

**Target Platform**: سرور مرکزی (۵۰G، ۸۵٪ پر) + ۶ VM freestyle (ubuntu، غیرروت، docker 29.1.3، compose 2.40.3)

**Performance Goals**: فلاد ۶۰ تسک در ۲s → ‎≥۹۵٪ completed؛ بیلد انجین هر گره < ۳۰ دقیقه؛ تأخیر سینک سشن ≤ ۶۰s؛ RTO ادامه سشن روی گره دیگر ≤ ۳ دقیقه

**Constraints**: گیت‌وی stateless بین ۴ ورکر uvicorn (state فقط Redis/PG)؛ سکرت فقط env؛ فارسی RTL؛ freestyle exec تنها کانال (تایم‌اوت جدا برای بیلد)؛ `.env` هرگز کامیت نمی‌شود

**Scale/Scope**: ۶ گره، هر گره ۱ انجین + ۱..۴ ورکر؛ ۷ انجین با یک کلید مشترک

## Constitution Check

| اصل | وضعیت | توضیح |
|---|---|---|
| I. E2E واقعی | ✅ PASS | quickstart این فیچر = فلاد + kill + پنل روی سرور واقعی |
| II. Worker-محوری + stateless gateway | ✅ PASS | سینک را ورکر انجام می‌دهد (نه گیت‌وی)؛ state در PG/Redis/دیسک |
| III. PDF تازه | ✅ PASS | مسیر PDF دست نمی‌خورد؛ mirror سشن جدا از run-artifact است |
| IV. دانلود تلگرام‌نیتیو | ✅ PASS | بدون تغییر |
| V. فارسی RTL | ✅ PASS | پیام‌های provision/sync فارسی |
| VI. سکرت در env | ✅ PASS با توجیه | کلید LLM در `.env` گره + env کانتینر انجین؛ هرگز لاگ/heartbeat/API |
| VII. یک کلید AI در انجین | ⚠️ **نیازمند اصلاح متن با تأیید مالک** | «فقط در کانتینر انجین (هرگز ورکر/گیت‌وی)» می‌ماند ولی «۱ کانتینر» → «۷ کانتینر (۶ گره + مرکز) با کلید مشترک». در همین plan اصلاح و با تأیید صریح مالک کامیت می‌شود |

## Architecture

```
┌─ مرکز ──────────────────────────────┐   ┌─ گره freestyle (×۶) ───────────────┐
│ gateway (pool+fallback)             │   │ worker → http://engine:8899 (لوکال)│
│ engine-primary (legacy+fallback)    │   │ engine (بیلد git، کلید در env)     │
│ PG + Redis + /app/session_mirrors/  │◄──│ agent (reconcile + engine health)  │
└─────────────────────────────────────┘   │ volume مشترک vibe-sessions         │
        ▲ state (LLM+repo/commit)         └────────────────────────────────────┘
        │ heartbeat (engine_healthy+synced_at)
        │ push/pull سشن (write-through)
```

### جریان‌ها

1. **Provision/ارتقا (پنل → هر ۶ گره)**: `connect → docker → net → engine → workers → agent → bench → done`
   - `engine` (جدید): `git clone` فورک + checkout کامیت پین‌شده به `/opt/vibe-node/engine-src`، سپس `docker compose up -d --build engine` (تایم‌اوت ۱۸۰۰s، جدا از ۶۰۰s بقیه قدم‌ها). Idempotent: clone اگر هست → `fetch + checkout`؛ بیلد اگر ایمیج با همان تگ هست → skip مگر epoch جدید.
   - `workers`: مثل امروز ولی `ENGINE_URL` ورکر = لوکال ثابت (compose DNS)، نه URL مرکزی.
   - `agent`: مثل امروز + گزارش سلامت انجین در heartbeat.
2. **چت/بک‌تست (local-only)**: ورکر فقط `http://engine:8899` لوکال را می‌زند. خطای اتصال/۵xx انجین لوکال → تسک FAILED (نه fallback به گره دیگر)؛ reaper/dispatcher موجود آن را روی گره سالم دوباره صف می‌کند (همان‌جا با انجین لوکال آن گره اجرا می‌شود).
3. **Write-through (بعد از هر تسک موفق)**: ورکر پیام‌های سشن را از انجین لوکال می‌خواند (`GET /sessions/{id}/messages` که امروز هم می‌زند) + متادیتا، به `POST /api/v1/fleet/sessions/push` مرکز می‌فرستد (auth با `X-Node-Token`)؛ گیت‌وی روی `/app/session_mirrors/{id}/` می‌نویسد. Best-effort: خطای sync تسک موفق را fail نمی‌کند (لاگ + `last_sync_error`).
4. **Pull-on-miss (شروع تسک)**: ورکر قبل از POST پیام، `GET` سشن از انجین لوکال می‌زند؛ اگر ۴۰۴ بود و مرکز mirror داشت (`GET /api/v1/fleet/sessions/pull/{id}`)، فایل‌ها را در volume مشترک (`/node-sessions/{id}/`) می‌نویسد و دوباره تلاش می‌کند (SessionStore بدون کش از دیسک می‌خواند).
5. **Gateway pool**: `EnginePool` امروز `node-{server}` را از `engine_url_local` می‌سازد — همان می‌ماند. سشن‌های legacy (ساخته قبل از مهاجرت) روی `engine-primary` می‌مانند؛ fallback آخر هم `engine-primary` است با هشدار پنل «حالت fallback مرکزی».
6. **چرخش کلید**: مالک کلید را در `.env` مرکز عوض می‌کند → state جدید → agentها `.env` گره را بازنویسی + `engine` را recreate می‌کنند (مثل رفتار worker امروز با `--force-recreate`).

## Data Model (تغییرات Postgres)

- `server_nodes` (+۳ ستون، nullable/پیش‌فرض‌دار تا migrate بی‌دردسر):
  - `engine_healthy: Boolean default True` — آخرین وضعیت انجین لوکال از heartbeat
  - `engine_checked_at: DateTime(timezone=True) nullable` — زمان آخرین چک
  - `last_session_sync_at: DateTime(timezone=True) nullable` — آخرین push موفق از این گره
  - موجود و استفاده‌نشده امروز که فعال می‌شود: `engine_url_local` (مقدار: `http://engine:8899` — فقط برای pool مرکزی به‌عنوان آدرس منطقی؛ ترافیک واقعی ورکرها لوکال است)، `node_role` (`full`).
- `vibe_sessions` (+۲ ستون nullable):
  - `home_node: String(64) nullable` — گرهی که سشن الان روی آن زنده است (مشاوره‌ای، نه قفل)
  - `last_synced_at: DateTime(timezone=True) nullable` — آخرین write-through
- جدول جدید لازم نیست (mirrorها فایل روی دیسک‌اند، نه ردیف PG). ایندکس جدید لازم نیست (lookup با `vibe_session_id` یکتا موجود).

## Contracts (API)

1. `GET /api/v1/node/{token}/state` (+۶ کلید، همه از settings/سرور؛ کلید LLM فقط اگر `VIBE_NODE_SHARE_ENGINE_KEY=True`):
   - `llm_provider | llm_model | llm_base_url | llm_api_key` (خالی اگر share خاموش → engine گره بالا نمی‌آید و provision در قدم engine با پیام فارسی می‌ایستد)
   - `engine_repo` (پیش‌فرض فورک)، `engine_commit` (پین‌شده؛ از updater مرکزی)
2. `POST /api/v1/node/{token}/heartbeat` (ورودی +۲ کلید): `engine_healthy: bool`، `engine_detail: str ≤500` → ستون‌های `engine_healthy/engine_checked_at`. اگر ۲ heartbeat متوالی ناسالم → هشدار تلگرام + پنل (مثل مرگ سرور) و آن گره ترافیک جدید نمی‌گیرد (dispatcher/worker-sync موجود: گره ناسالم از pool کنار می‌رود).
3. `POST /api/v1/fleet/sessions/push` (auth: `X-Node-Token` = join_token گره؛ body: `{session_id, messages[], session_json?, updated_at}`) → نوشتن `/app/session_mirrors/{id}/messages.jsonl` + `session.json`؛ جواب `{ok, session_id}`. Best-effort؛ بدون توکن معتبر → ۴۰۴.
4. `GET /api/v1/fleet/sessions/pull/{session_id}` (auth مشابه) → `{found, messages[], session_json?}` یا `{found:false}`. ورکر روی miss صدا می‌زند.
5. `GET /api/v1/admin/servers/{id}` و لیست سرورها (+۳ فیلد نمایشی): `engine_healthy`، `last_session_sync_at`، بج سلامت در پنل.

## Source Changes (فهرست دقیق)

- `shared/config.py`: `+ LLM_PROVIDER | LLM_MODEL | LLM_BASE_URL | LLM_API_KEY | ENGINE_REPO | ENGINE_COMMIT` (از env؛ خالی مجاز).
- `.env` مرکز (دستی، کامیت‌نشدنی): کپی ۴ مقدار LLM از `/opt/Vibe-Trading/agent/.env` + `VIBE_NODE_SHARE_ENGINE_KEY=True` + `ENGINE_REPO/ENGINE_COMMIT`.
- `gateway/app/main.py`: state/heartbeat/mirror/push/pull + غنی‌سازی admin-servers.
- `gateway/app/provision.py`: `_step_engine` جدید (git clone/checkout/build engine، تایم‌اوت ۱۸۰۰)؛ `_step_workers` بدون تغییر منطقی (ENV لوکال از agent می‌آید).
- `docker-compose.node.yml` (+ engine service): `build: ./engine-src`، `env_file: .env` + `environment` (LLM_* → نام‌های انجینی + `API_AUTH_KEY=${ENGINE_API_KEY}`)، پورت داخلی 8899 (بدون publish عمومی)، volume مشترک `vibe-sessions:/home/vibe/.vibe-trading/sessions`؛ worker هم همان volume را در `/node-sessions:rw` مانت می‌کند + `ENGINE_URL=http://engine:8899` ثابت.
- `agent/app/main.py`: `_apply_env_files` (نوشتن `ENGINE_URL=http://engine:8899` ثابت + `LLM_*/ENGINE_REPO/ENGINE_COMMIT` به `.env` گره) + heartbeat (`engine_healthy` via `docker inspect`/GET لوکال `http://engine:8899/live`) + reconcile (recreate engine هنگام epoch/تغییر LLM).
- `worker/app/main.py`: `task_chat/task_backtest` (pull-on-miss قبل، write-through بعد؛ sync هرگز تسک موفق را fail نکند) + helperهای `_session_push/_session_pull` (httpx به `VT_CONTROL_URL` با `X-Node-Token`).
- `gateway/app/static` + `AdminPage.tsx`: بج سلامت انجین + «آخرین سینک» در NodesTab/ServerDetailModal (آستانه پنل: سینک > ۵ دقیقه = زرد).
- `.specify/memory/constitution.md`: اصلاح اصل VII (۱ → ۷ کانتینر انجین با کلید مشترک) — **با تأیید صریح مالک کامیت می‌شود**.

## E2E (معیار قبولی روی سرور واقعی)

1. **سبز شدن ۶ انجین**: `docker ps` هر گره `engine (healthy)` + پنل ۶ بج سبز.
2. **local-only**: قطع منطقی انجین مرکزی از گره (یا شمارش ترافیک) → چت همچنان موفق (فقط رله LLM خارجی).
3. **فلاد ۶۰ تسک چت چندکاربره** (همان recipe تست قبلی، `fallback=0`): ‎≥۹۵٪ completed + ثبت per-engine (کدام انجین جواب داد) تا سؤال «انجین یا رله» جواب داده شود.
4. **kill-میدانی US-2**: کشتن engine گره A وسط چت فعال → پیام بعدی همان سشن از گره B موفق (pull-on-miss)؛ صفر تسک گم‌شده.
5. **اسنپ‌شات پنل**: بج‌ها + آخرین سینک + هشدار fallback (اگر تست شد).

## Risks

| ریسک | اثر | مهار |
|---|---|---|
| بیلد ۱۰–۲۰ دقیقه‌ای × ۶ گره همزمان | freestyle exec تایم‌اوت/قطعی | تایم‌اوت ۱۸۰۰s قدم engine؛ رول‌اوت هر ۶ با هم ولی retry از قدم خراب (انتخاب مالک Q3-ب حفظ می‌شود) |
| دیسک مرکز ۸۵٪ پر | mirror جا نشود | mirror فقط همین سشن‌های فعال (KB)؛ قبل از rollout `docker image prune`؛ مانیتور دیسک در پنل |
| رله LLM زیر بار مشترک rate-limit کند | فلاد دوباره Timeout بدهد | متریک per-engine در تست تا مقصر (انجین/رله) جدا شود؛ در این صورت قدم بعدی: کلید دوم/سهمیه‌بندی، نه انجین بیشتر |
| کامیت پین‌شده انجین قدیمی شود | گره‌ها از مرکز عقب بیفتند | `ENGINE_COMMIT` از updater مرکزی می‌آید؛ FleetUpdate موجود engine گره‌ها را هم bump می‌کند |

## Complexity Tracking

| نکته | چرا ساده‌سازی نشد |
|---|---|
| سینک توسط ورکر (نه ایجنت/کرون جدا) | ورکر تنها جزء است که هم انجین لوکال و هم گیت‌وی را می‌بیند؛ بدون سرویس جدید |
| mirror فایل روی دیسک (نه جدول PG) | انجین فایل‌محور است؛ بدون fork و بدون migrate سنگین |
| بیلد git روی گره (نه رجیستری) | انتخاب مالک (Q7-الف)؛ بدون سرویس/پورت/احراز جدید |
