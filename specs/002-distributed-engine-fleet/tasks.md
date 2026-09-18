# Tasks: ناوگان انجین توزیع‌شده (002-distributed-engine-fleet)

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
**Constitution**: اصلاح اصل VII (۱ → ۷ کانتینر انجین) با تأیید مالک، در T001.

---

## Phase 0 — زیرساخت مرکزی (بدون تاچ گره)

- [x] **T000a API گزارش پیش‌نیازها**: `GET /api/v1/admin/fleet/preflight?auth_type=freestyle` با مجوز servers؛ گزارش فارسی بدون مقدار سکرت و بدون اتصال به گره. تست قرارداد کمبود commit با TestClient: ابتدا 405، پس از پیاده‌سازی 200 و ready=false. تست HTTP روی گیت‌وی مستقر: ناشناس 401، ادمین 200، هشت چک موفق، عدم افشای کلید تأیید شد. این فقط بررسی تنظیمات مرکز است، نه تست دسترسی شبکه یا نصب کامل؛ رابط پنل و اصلاح خودکار عمومی هنوز باقی است.

- [ ] **T001 config + constitution**: `shared/config.py` شش کلید `LLM_PROVIDER/LLM_MODEL/LLM_BASE_URL/LLM_API_KEY/ENGINE_REPO/ENGINE_COMMIT` + اصلاح اصل VII در `.specify/memory/constitution.md`. سپس دستی در `.env` مرکز (کامیت‌نشدنی): کپی ۴ مقدار LLM از `/opt/Vibe-Trading/agent/.env` + `VIBE_NODE_SHARE_ENGINE_KEY=True` + `ENGINE_REPO=<fork>` + `ENGINE_COMMIT=76247a9a`. چک: `get_settings().LLM_API_KEY` غیرخالی داخل کانتینر گیت‌وی.
- [ ] **T002 مدل**: `ServerNode` سه ستون (`engine_checked_at`, `last_session_sync_at`؛ `engine_healthy` موجود است) + `VibeSession` دو ستون (`home_node`, `last_synced_at`) — همه nullable تا migrate با `create_all` بی‌دردسر. چک: `py_compile` + ستون‌ها در PG.
- [ ] **T003 state/heartbeat**: `node_state` شش کلید LLM/repo/commit را بدهد (کلید فقط اگر SHARE روشن) + `node_heartbeat` دو کلید `engine_healthy/engine_detail` را ذخیره کند + هشدار تلگرام/پنل پس از ۲ heartbeat ناسالم (مثل مرگ سرور). چک: state از بیرون با توکن واقعی.
- [ ] **T004 push/pull سشن**: `POST /api/v1/fleet/sessions/push` (auth `X-Node-Token`، نوشتن `/app/session_mirrors/{id}/`) + `GET /api/v1/fleet/sessions/pull/{id}` (+ volume `./session_mirrors:/app/session_mirrors` در compose مرکز). چک: push/pull با curl از بیرون.

## Phase 1 — باندل و ایجنت گره

- [ ] **T005 compose گره + engine**: سرویس `engine` در `docker-compose.node.yml` (`build: ./engine-src`، env از `.env` گره، پورت 8899 داخلی بدون publish، volume `vibe-sessions`) + worker با `ENGINE_URL=http://engine:8899` ثابت و مانت `/node-sessions`. **نکته بیلد**: Dockerfile انجین `frontend/dist` را می‌خواهد → روی گره `npm` نیست؛ پس compose گره `build.args.SKIP_FRONTEND=1` یا `target` بدون فرانت می‌خواهد (در T005 با تست واقعی روی گره مشخص می‌شود؛ fallback: کپی `frontend/dist` آماده مرکز در tar جدا). چک: `docker compose config` سبز.
- [ ] **T006 ایجنت**: `_apply_env_files` (نوشتن `LLM_*/ENGINE_REPO/ENGINE_COMMIT` + `ENGINE_URL=http://engine:8899` ثابت در `.env` گره) + چک سلامت انجین در heartbeat (`GET http://engine:8899/live` داخل شبکه compose) + recreate انجین هنگام تغییر LLM/epoch. چک: `.env` گره بعد از reconcile.
- [ ] **T007 provision قدم engine**: `_step_engine` جدید — `git clone` فورک (یا fetch+checkout اگر هست) + `docker compose up -d --build engine` با تایم‌اوت ۱۸۰۰s + پیام فارسی. چک: `py_compile`.

## Phase 2 — ورکر (local-only + write-through + pull-on-miss)

- [x] **T008 ورکر**: pull-on-miss قبل از POST (۴۰۴ لوکال → pull مرکز → نوشتن `/node-sessions/{id}/` → retry) + write-through بعد از تسک موفق (push به مرکز، best-effort — خطای sync تسک را fail نکند) + ثبت `home_node/last_synced_at`. چک: unit با انجین mock. ✅ 2026-09-18 — `worker/app/main.py` (`_pull_session_on_miss`, `_push_session_to_center`, `_is_session_missing`) + مرکز `POST/GET /api/v1/fleet/sessions/push|pull/{id}` (X-Node-Token = join_token، فایل‌ها زیر `SESSION_MIRROR_DIR/{id}/`) + compose گره volume مشترک `vibe-sessions:/node-sessions`. تست‌های قراردادی: `tests/contract/test_worker_session_sync.py` + `test_session_mirror_endpoints.py` — ۵ پاس.
- [ ] **T009 dispatcher/fallback**: گره با `engine_healthy=False` ترافیک جدید نگیرد (فیلتر مثل stale-worker) + fallback آخر `engine-primary` با هشدار پنل. چک: unit + رفتار با pool موجود.

## Phase 3 — پنل

- [ ] **T010 پنل**: بج سلامت انجین + «آخرین سینک» در NodesTab/ServerDetailModal (آستانه زرد: سینک > ۵ دقیقه) + دکمه «نصب/ارتقای انجین» هر ۶ گره (provision موجود با قدم engine جدید). Deploy با hygiene کامل (`npm build` → `static/` → recreate → curl رشته جدید). چک: browser E2E.

## Phase 4 — رول‌اوت و E2E (روی سرور واقعی)

- [ ] **T011 رول‌اوت هر ۶ گره** (Q3-ب): provision از پنل؛ بیلد اول هر گره ۱۰–۲۰ دقیقه؛ retry از قدم خراب. سبز = `docker ps` هر گره `engine (healthy)` + ۶ بج سبز پنل.
- [ ] **T012 فلاد ۶۰ تسک چت** (recipe تست قبلی، `fallback=0`): ‎≥۹۵٪ completed + ثبت per-engine (کدام انجین جواب داد) تا «انجین یا رله» جدا شود.
- [ ] **T013 kill-میدانی**: کشتن engine گره A وسط چت → پیام بعدی همان سشن از گره B موفق؛ صفر تسک گم‌شده.
- [ ] **T014 converge**: کامیت + push (PAT موقت) + ثبت skill + این tasks.md با تیک نهایی.

## ریسک‌های باز (صادقانه)

1. **T005 ناشناخته اصلی**: آیا Dockerfile انجین بدون `frontend/dist` بیلد می‌شود؟ روی گره `npm` نیست. اگر نه → tar جدا برای `frontend/dist` آماده (حجم؟ باید اندازه گرفت).
2. **سؤال انجین-vs-رله** در T012 جواب داده می‌شود؛ اگر رله مقصر بود قدم بعدی کلید دوم است نه انجین بیشتر.
3. دیسک مرکز ۸۵٪ پر — قبل از T011 باید `docker image prune` و چک فضا.
