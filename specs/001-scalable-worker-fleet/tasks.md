# Tasks: ناوگان ورکر مقیاس‌پذیر چندسروره

**Input**: Design documents from `/specs/001-scalable-worker-fleet/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: تسک‌های تست فقط جایی آمده که spec صراحتاً سناریوی قابل‌تست مستقل دارد (Independent Test) — همان‌ها به‌صورت pytest/contract درآمده‌اند. E2E نهایی هر استوری با quickstart.md انجام می‌شود (الزام ماده I قانون‌نامه).

**Organization**: گروه‌بندی بر اساس user story؛ هر استوری مستقل قابل‌پیاده‌سازی و قابل‌تست است.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: قابل اجرای موازی (فایل متفاوت، بدون وابستگی)
- **[Story]**: شماره استوری (US1..US13)
- مسیر فایل دقیق در هر تسک آمده است.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: وابستگی‌ها و بستر مشترک — بدون این‌ها هیچ‌کدام از فازها شروع نمی‌شود.

- [X] T001 نصب وابستگی‌های جدید گیت‌وی در `gateway/requirements.txt` (افزودن `asyncssh`, `cryptography`)
- [X] T002 [P] نصب کتابخانه نمودار وب‌اپ با `npm i recharts` در `webapp/package.json` (پین major نسخه)
- [X] T003 [P] افزودن `FLEET_MASTER_KEY` به `/.env.example` و نمونه‌مقدار در `/root/vibe-trading-saas/.env` (فقط کلید، بدون سکرت واقعی در گیت)
- [X] T004 [P] ساخت اسکلت دایرکتوری `webapp/src/components/fleet/` — بدون دست‌زدن به `webapp/src/api/admin.ts` موجود (۱۲۰ خط، APIهای fleet/monitor زنده و سالم؛ اسکلت جدا لازم نبود)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: زیرساخت هسته که همه استوری‌ها روی آن سوارند — مدل‌ها، کریپتو، نقش‌ها، سهمیه، متریک.

**⚠️ CRITICAL**: هیچ‌کدام از فازهای استوری قبل از اتمام این فاز شروع نمی‌شود.

- [X] T005 توسعه مدل `ServerNode` در `shared/models.py` (ستون‌ها: `ssh_host`, `ssh_user`, `ssh_secret`, `ssh_auth_type`, `engine_url_local`, `engine_healthy`, `min_workers`/`max_workers`, `autoscale_enabled`, `capability`, `capability_warning`, `provision_state`, `provision_step`, `provision_log`, `tailscale_ip` + وضعیت‌های `pending|online|degraded|offline|draining|decommissioned`)
- [X] T006 [P] مدل‌های جدید در `shared/models.py`: `AdminRole` (`name` یکتا, `perms` JSON)، `AdminRoleLink` (یکتا `user_id,role_id`)، `FleetMetric` (`server_id,ts` ایندکس مرکب, `metric`, `value`)، `ProvisionJob` (`server_id`, `status`, `current_step`, `steps` JSON, `triggered_by`)
- [X] T007 [P] ستون `server_id` (FK به `server_nodes`) در مدل `WorkerNode` در `shared/models.py`
- [X] T008 پیاده‌سازی `gateway/app/crypto.py` (Fernet + مشتق HKDF از `(FLEET_MASTER_KEY, server_id)`، توابع encrypt/decrypt، چرخش مستر با `key_id`، ممنوعیت لاگ plaintext)
- [X] T009 پیاده‌سازی گارد نقش در `gateway/app/roles.py` (وابستگی FastAPI `require_perm(perm)`، خواندن نقش کاربر از `AdminRoleLink`، پاسخ `403` فارسی)
- [X] T010 تکامل `gateway/app/coupons.py` به سیاست جدید در همان فایل (سقف فری: روزانه ۱ بک‌تست + هفتگی ۱ سوارم بدون انباشت؛ `welcome/referral` دائمی با `expires_at=NULL`؛ ترتیب مصرف اتمیک daily→دائمی در یک تراکنش؛ `refund` فقط خطای سمت ما)
- [X] T011 [P] ماژول جمع‌آوری متریک در `gateway/app/metrics.py` (نوشتن نمونه Redis زنده با TTL + درج `FleetMetric` + purge خام ۲۴ ساعته + rollup دقیقه‌ای)
- [X] T012 اعمال مایگریشن DB روی سرور واقعی (اجرای `create_all`/اسکریپت مایگریشن علیه Postgres مرکزی + تأیید جداول جدید)
- [X] T013 [P] تست واحد کریپتو و کوپن در `tests/unit/test_crypto_coupon.py` (رمز/گشایش، چرخش مستر، claim اتمیک، عدم انباشت روزانه، برگشت خطای سمت ما)

**Checkpoint**: مدل‌ها + کریپتو + نقش + سهمیه + متریک آماده — پیاده‌سازی استوری‌ها می‌تواند شروع شود.

---

## Phase 3: US9 — ثبت سرور با IP و رمز + نصب کامل خودکار (Priority: P1) 🎯 MVP

**Goal**: فرم «سرور جدید» در پنل → نصب صفرتاصد (داکر→شبکه→انجین→ورکر→ایجنت→تست توان) با نوار پیشرفت زنده و تلاش مجدد از همان مرحله.

**Independent Test**: VPS خام فقط با فرم پنل به ناوگان اضافه شود؛ بدون هیچ دستوری روی آن سرور، گره کامل «آماده» شود و تسک بگیرد (quickstart §۳).

- [X] T014 [P] [US9] تست قراردادی `POST /servers` و `GET provision` و `POST provision/retry` در `tests/contract/test_provision.py` (۲۰۱، ۴۰۳ بدون نقش، ۴۲۲ SSH نامعتبر)
- [X] T015 [P] [US9] موتور نصب مرحله‌ای در `gateway/app/provision.py` (ماشین‌حالت `connect→docker→net→engine→workers→agent→bench→done` با `asyncssh` + سمافور ~۱۰ + تایم‌اوت هر مرحله + لاگ فارسی خط‌به‌خط + پین host-key)
- [X] T016 [US9] راوت‌های نصب در `gateway/app/main.py` (`POST /api/v1/admin/fleet/servers` با رمزنگاری سکرت، `GET .../provision` برای poll، `POST .../provision/retry` از همان مرحله) با گارد نقش `servers` (وابسته به T009، T015)
- [X] T017 [US9] فرم «سرور جدید» + نوار پیشرفت زنده در `webapp/src/pages/AdminPage.tsx` (فیلدهای IP/کاربر + رمز/کلید، poll هر ۳ ثانیه حین نصب، دکمه تلاش مجدد، پیام خطای فارسی) (وابسته به T016)
- [X] T018 [US9] توسعه باندل گره برای نصب کامل در `gateway/app/main.py` تابع `node_bundle` (افزودن ایمیج/کامپوز انجین پین‌شده + تزریق `VIBE_ENGINE_URL=localhost` و کلید مشترک به env گره)
- [X] T019 [US9] E2E نصب روی VPS خام طبق `specs/001-scalable-worker-fleet/quickstart.md` §۳ (مدرک browser_console + `grep` عدم نشت سکرت در لاگ)

**Checkpoint**: US9 مستقل کار می‌کند — سرور خام فقط با پنل «آماده» می‌شود.

---

## Phase 4: US2 — توزیع خودکار تسک بین همه ورکرها (Priority: P1)

**Goal**: هر تسک دقیقاً به یک ورکر سالم (کم‌بارترین کل ناوگان، با اولویت پلن پولی) سپرده شود و نتیجه به همان کاربر برگردد.

**Independent Test**: ۱۰ تسک هم‌زمان → همه به نتیجه، بدون گم‌شدگی/تکرار، بار پخش‌شده بین گره‌ها (quickstart §۲).

- [X] T020 [P] [US2] آگاهی از سرور در `gateway/app/dispatch.py` (نگاشت `worker→server` از `worker_nodes`، محرومیت ورکرهای گره draining/offline از انتخاب)
- [X] T021 [US2] نجات سطح سرور در `gateway/app/dispatch.py` تابع `_reap_tick` (تشخیص مرگ کل سرور از heartbeat + انتقال گروهی صف‌هایش به fallback، حفظ اولویت پلن) (وابسته به T020)
- [X] T022 [US2] مسیریابی به انجین همان گره در `gateway/app/fleet.py` (`EnginePool`: هر گره کامل یک ردیف انجین، failover سالم‌اول) (وابسته به T021)
- [X] T023 [US2] برگرداندن خروجی به مرکز در `worker/app/main.py` (پس از اتمام تسک: آپلود فایل‌های `signal_engine.py`/`config.json`/متریک به گیت‌وی مرکزی؛ گره stateless می‌ماند)
- [X] T024 [US2] تست kill وسط اجرا طبق quickstart §۲ (مدرک: دقیقاً یک نتیجه برای کاربر + پیام تلگرام مدیر)

**Checkpoint**: US1+US2+US3+US9+US10 مسیر اصلی «ثبت→توزیع→نتیجه» را کامل می‌کنند.

---

## Phase 5: US3 — مدیریت ناوگان از پنل (Priority: P1)

**Goal**: دیدن هر سرور/ورکر/بار/سلامت + تغییر سقف ورکر + drain + حذف — همه بدون SSH.

**Independent Test**: فقط با پنل تعداد ورکر عوض و سرور drain شود؛ رفتار سیستم مطابق انتظار عوض شود.

- [X] T025 [P] [US3] راوت‌های مدیریت در `gateway/app/main.py` (`GET /api/v1/admin/fleet/servers` لیست+سلامت، `PATCH .../{id}` سقف/min-max/autoscale/drain، `DELETE .../{id}` حذف امن با ترتیب drain→توقف→پاک‌سازی سکرت→ابطال توکن) با گارد نقش
- [X] T026 [US3] تب مدیریت گره‌ها در `webapp/src/pages/AdminPage.tsx` (جدول سرورها + تغییر سقف + دکمه drain/حذف + پیام فارسی) (وابسته به T025)
- [X] T027 [US3] E2E مدیریتی: تغییر سقف → ورکرها اعمال شوند؛ drain → ورودی جدید صفر ولی جاری‌ها تمام شوند؛ حذف → خارج از چرخه + `moved_tasks` گزارش شود

**Checkpoint**: چرخه‌عمر گره کاملاً از پنل کنترل می‌شود.

---

## Phase 6: US10 — داشبورد گرافیکی زنده ناوگان (Priority: P1)

**Goal**: نمودار خطی بار (۵ ثانیه + تاریخچه) + میله‌ای تسک + دایره‌ای توزیع + نقشه سلامت + کارت‌های عددی + لیست زنده — فارسی RTL دارک‌مود موبایل‌دوست.

**Independent Test**: با ۱۰ تسک فعال نمودارها زنده حرکت کنند، اعداد با واقعیت بخوانند، روی موبایل به‌هم‌ریخته نباشد (quickstart §۴).

- [X] T028 [P] [US10] endpointهای متریک در `gateway/app/main.py` (`GET /api/v1/admin/fleet/metrics/live` نمونه زنده، `GET /api/v1/admin/fleet/metrics/history` با downsample ≤۵۰۰ نقطه، `GET /api/v1/admin/fleet/tasks-live`) با نقش `dashboard`
- [X] T029 [P] [US10] ویجت‌های داشبورد در `webapp/src/components/fleet/` (کارت‌های عددی فارسی `FleetCards.tsx` + نمودار خطی `LoadChart.tsx` با `isAnimationActive={false}` و رینگ‌بافر + میله‌ای `TasksBar.tsx` + دایره‌ای `DistDonut.tsx` + نقشه سلامت SVG دستی `HealthMap.tsx` + لیست زنده `LiveTasks.tsx`)
- [X] T030 [US10] صفحه داشبورد در `webapp/src/pages/AdminPage.tsx` (poll هر ۵ ثانیه + تب تاریخچه چندروزه + `Intl.NumberFormat('fa-IR')` + سازگاری موبایل) (وابسته به T028، T029)
- [X] T031 [US10] E2E داشبورد طبق quickstart §۴ (مدرک browser_console: حرکت زنده + قرمز شدن گره مرده + اسکرین‌شات موبایل)

**Checkpoint**: ویترین تجاری محصول — مدیری که این را ببیند اعتماد می‌کند.

---

## Phase 7: US1 — افزودن سرور جدید در چند دقیقه (Priority: P1)

**Goal**: دستور عضویت یک‌خطی از پنل → اجرای روی سرور → «آماده» شدن بدون تغییر دستی در مرکز (مسیر جایگزین US9 برای مدیرانی که SSH دستی ترجیح می‌دهند).

**Independent Test**: سرور جدید فقط با دستور عضویت در پنل «آماده» شود و تسک بگیرد.

- [X] T032 [US1] polish مسیر عضویت موجود در `gateway/app/main.py` (`node_bundle`/`node_state`/`node_heartbeat` + دکمه «کپی دستور عضویت» در `webapp/src/pages/AdminPage.tsx`) و نمایش توکن یکتا با امکان ابطال/بازتولید (FR-011)
- [X] T033 [US1] E2E عضویت دستوری: اجرا روی سرور تستی → «آماده» در پنل + گرفتن تسک آزمایشی

---

## Phase 8: US6 — سهمیه کوپن پلن فری (Priority: P2)

**Goal**: روزی ۱ بک‌تست + هفته‌ای ۱ سوارم بدون انباشت + اعتبار دائمی رفرال + برگشت کوپن خطای سمت ما + پیام فارسی «کوپن تمام شد».

**Independent Test**: ۲ بک‌تست در یک روز (کوپن+جایزه) هر دو اجرا، سومی 402؛ تسک ناموفق سمت ما کوپن را برمی‌گرداند (quickstart §۱).

- [X] T034 [P] [US6] اعمال گیت کوپن در مسیر ثبت تسک در `gateway/app/main.py` (claim اتمیک قبل از dispatch؛ `402` فارسی بدون کوپن؛ عدم مصرف در رد اعتبارسنجی) (وابسته به T010)
- [X] T035 [P] [US6] endpoint سهمیه من `GET /api/v1/me/quota` در `gateway/app/main.py` + نمایش فارسی در مینی‌اپ (`webapp/src/pages/HomePage.tsx` یا `ChatPage.tsx`: «کوپن امروز: ۱»)
- [X] T036 [US6] تست مسابقه و برگشت در `tests/unit/test_crypto_coupon.py` (۲ ثبت هم‌زمان با ۱ کوپن → دقیقاً یکی 402؛ refund خطای موتور؛ ریست نیمه‌شب تهران) + E2E quickstart §۱

---

## Phase 9: US4 — زنده ماندن تسک با مرگ سرور (Priority: P2)

**Goal**: مرگ وسط‌راه سرور → تسک روی گره سالم تمام شود، کاربر دقیقاً یک نتیجه بگیرد.

**Independent Test**: kill سرور وسط اجرا → اتمام روی گره سالم + دقیقاً یک نتیجه (quickstart §۲، توسعه Phase 4).

- [X] T037 [US4] تضمین idempotency نتیجه در `gateway/app/main.py` (کلید یکتای تحویل به‌ازای `task_id`؛ نتیجه دوم همان تسک دور ریخته می‌شود، نه دوباره ارسال)
- [X] T038 [US4] E2E kill با لاگ `[dl-token]`-مانند تشخیصی برای dispatcher (مدرک: `rescued_from_dead` افزایش + یک push به کاربر)

---

## Phase 10: US8 + US13 — شبکه خصوصی تنها + بازیابی خودکار قطعی (Priority: P2)

**Goal**: هیچ پورت داخلی روی اینترنت عمومی باز نباشد؛ قطعی Tailscale/VPN → drain خودکار + انتقال تسک + پیام تلگرام + تلاش اتصال مجدد + بازگشت خودکار.

**Independent Test**: اسکن بیرونی بسته + کارکرد از IP خصوصی (quickstart §۸)؛ قطع/وصل اینترفیس → انتقال + پیام + بازگشت (quickstart §۷).

- [X] T039 [P] [US8] بستن پورت‌ها روی compose مرکزی در `docker-compose.yml` (حذف publish عمومی `6379/5432/8899`؛ فقط `127.0.0.1` یا شبکه خصوصی) + مستندسازی Tailscale در `README.md`
- [ ] T040 [US13] تشخیص قطعی شبکه در `gateway/app/fleet.py` حلقه سلامت (افت heartbeat → `degraded` → drain خودکار + انتقال تسک + پیام تلگرام به مدیر + تلاش دوره‌ای؛ وصل شدن → تست سلامت → `online`)
- [X] T041 [US8+US13] E2E امنیتی-شبکه طبق quickstart §۷ و §۸ (`nmap` بیرونی + `redis-cli` از IP خصوصی + قطع/وصل Tailscale)

---

## Phase 11: US11 — به‌روزرسانی مرحله‌ای گره‌ها (Priority: P2)

**Goal**: آپدیت گره‌به‌گره (حداکثر یک گره هم‌زمان) با اطلاع قبلی + لغو + rollback خودکار گره ناموفق.

**Independent Test**: آپدیت ۲ گره با تسک در حال اجرا → هیچ گم‌شدگی، هر دو روی نسخه جدید (quickstart §۵).

- [ ] T042 [US11] توسعه `FleetUpdate` با وضعیت per-node در `shared/models.py` + منطق rollout در `updater/` (drain→update→health→back، توقف با شکست + rollback به نسخه قبلی)
- [ ] T043 [US11] UI آپدیت در `webapp/src/pages/AdminPage.tsx` (دکمه «به‌روزرسانی ناوگان» + پیش‌نمایش تغییرات + لغو + پیشرفت per-node) + راوت‌ها در `gateway/app/main.py` (`POST /api/v1/admin/fleet/updates`, `DELETE .../{id}`)
- [ ] T044 [US11] E2E آپدیت طبق quickstart §۵

---

## Phase 12: US12 — نقش‌های مدیریتی قابل تعریف (Priority: P2)

**Goal**: ساخت نقش با تیک دسترسی + اعمال در UI و API (403 واقعی، نه فقط مخفی‌کردن دکمه).

**Independent Test**: نقش «فقط‌مشاهده» → هیچ دکمه عملیاتی + `403` روی فراخوانی مستقیم نصب (quickstart §۶).

- [ ] T045 [P] [US12] CRUD نقش‌ها در `gateway/app/main.py` (`GET/POST/PATCH/DELETE /api/v1/admin/roles` + اتصال کاربر→نقش) با نقش لازم `users`
- [ ] T046 [US12] UI نقش‌ها در `webapp/src/pages/AdminPage.tsx` (لیست نقش‌ها + چک‌باکس دسترسی‌ها + مخفی‌سازی دکمه‌ها بر اساس نقش جاری) (وابسته به T045)
- [ ] T047 [US12] E2E نقش طبق quickstart §۶ (مدرک 403 + اسکرین‌شات UI بدون دکمه)

---

## Phase 13: US5 — دید زنده بار و صف (Priority: P3) + US7 — تست توان (Priority: P3)

**Goal (US5)**: بار/صف هر سرور/ورکر به‌روز در پنل. **Goal (US7)**: تست خودکار توان موقع عضویت + هشدار «سرور ضعیف» با جزئیات (بدون بلاک).

**Independent Test**: اعداد پنل با واقعیت بخوانند؛ سرور ضعیف هشدار بگیرد.

- [ ] T048 [P] [US7] تست توان در `agent/app/main.py` (جمع‌آوری CPU/RAM/دیسک/docker/latency در اولین heartbeat + ارسال در `host_info`) + نمایش verdict در `gateway/app/main.py` (`GET /servers/{id}` شامل `capability` + `capability_warning`)
- [ ] T049 [P] [US5] کارت‌های بار/صف به‌تفکیک ورکر در تب نودها (`webapp/src/pages/AdminPage.tsx`، تغذیه از `metrics/live`) — مکمل داشبورد US10
- [ ] T050 [P] [US5+US7] مقیاس خودکار در `gateway/app/autoscale.py` (حلقه ۶۰ ثانیه‌ای: `want=ceil(queue/TARGET)` داخل `[min,max]` + hysteresis + cooldown؛ override دستی همیشه مقدم) + سیم‌کشی به `desired_workers`

---

## Phase 14: Polish & Cross-Cutting Concerns

**Purpose**: نگه‌داری، امنیت نهایی، مستندات، اعتبارسنجی سرتاسری.

- [ ] T051 [P] پاک‌سازی ۳۰ روزه در `gateway/app/retention.py` (job روزانه: حذف فایل‌های >۳۰ روز + اطلاع کاربر + حفظ متریک خلاصه) + E2E quickstart §۹
- [ ] T052 [P] فارسی‌سازی و RTL نهایی همه متن‌های جدید وب‌اپ + تست موبایل WebView (تلگرام) برای تب‌های نودها/داشبورد
- [ ] T053 [P] به‌روزرسانی `README.md` + `ARCHITECTURE.md` (نقشه ناوگان، Tailscale، سقف‌ها، کوپن) — بدون سکرت
- [ ] T054 اجرای کامل `specs/001-scalable-worker-fleet/quickstart.md` (§۱ تا §۹) روی سرور واقعی + ثبت مدارک + کامیت/push نهایی
- [ ] T055 بازبینی امنیتی نهایی: `grep` عدم plaintext سکرت (`ssh_password`, `PRIVATE KEY`) در لاگ/پاسخ‌ها + تأیید 403 نقش‌ها + تأیید بسته بودن پورت‌ها

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: بدون وابستگی — شروع فوری.
- **Foundational (Phase 2)**: وابسته به Setup — **مسدودکننده همه استوری‌ها**.
- **User Stories (Phase 3+)**: همه وابسته به Foundational؛ سپس به ترتیب اولویت P1 → P2 → P3 (یا موازی با ظرفیت کافی).
- **Polish (Phase 14)**: وابسته به اتمام استوری‌های موردنظر.

### User Story Dependencies

- **US9 (P1)**: پس از Foundational؛ مستقل — موتور نصب روی crypto+roles+models سوار است.
- **US2 (P1)**: پس از Foundational؛ خروجی‌به‌مرکز (T023) پیش‌نیاز تست kill است.
- **US3 (P1)**: پس از Foundational؛ از API نصب (T016) برای شناسه گره استفاده می‌کند ولی مستقل قابل‌تست است.
- **US10 (P1)**: پس از Foundational + `metrics.py`؛ از داده US2/US3 تغذیه می‌شود ولی UI مستقل است.
- **US1 (P1)**: پس از Foundational؛ مسیر جایگزین US9.
- **US6 (P2)**: پس از Foundational (quota)؛ گیت ثبت تسک روی dispatch سوار است ولی مستقل تست می‌شود.
- **US4 (P2)**: پس از US2 (نجات سطح سرور) + idempotency خودش.
- **US8+US13 (P2)**: پس از Foundational؛ حلقه سلامت fleet.py توسعه می‌یابد.
- **US11 (P2)**: پس از US9 (مکانیزم نصب/اجرا روی گره) + US3 (drain).
- **US12 (P2)**: پس از Foundational (roles)؛ CRUD مستقل.
- **US5+US7 (P3)**: پس از Foundational؛ autoscale روی dispatcher سوار است.

### Within Each User Story

- قرارداد/تست اول (قرمز) → مدل → سرویس → endpoint → UI → E2E.
- ایمپلیمنت قبل از انتگراسیون؛ استوری کامل بعد سراغ اولویت بعدی.

### Parallel Opportunities

- [P]های Setup (T002–T004) موازی.
- [P]های Foundational (T006، T007، T011، T013) موازی.
- پس از Foundational: US9/US2/US10 می‌توانند موازی جلو بروند (فایل‌های جدا: `provision.py` / `dispatch.py`+`fleet.py` / `components/fleet/`).
- US12 (roles CRUD) و US6 (quota UI) موازی‌پذیرند.
- T048/T049/T050 (Phase 13) هر سه [P] و موازی.

---

## Parallel Example: US9 (نصب خودکار)

```bash
# قرارداد + موتور نصب موازی (فایل‌های جدا):
Task: "T014 contract test provision در tests/contract/test_provision.py"
Task: "T015 موتور نصب در gateway/app/provision.py"
# بعد به ترتیب:
Task: "T016 راوت‌ها در gateway/app/main.py (وابسته به T015)"
Task: "T017 فرم + نوار پیشرفت در webapp/src/pages/AdminPage.tsx (وابسته به T016)"
```

---

## Implementation Strategy

### MVP First (US9 + US2 + US3)

1. Phase 1: Setup + Phase 2: Foundational.
2. US9 (نصب خودکار) → US2 (توزیع) → US3 (مدیریت) — هر کدام **STOP و VALIDATE** مستقل.
3. MVP = «سرور خام با پنل می‌آید بالا + تسک می‌گیرد + از پنل مدیریت می‌شود».

### Incremental Delivery

1. Setup + Foundational → بستر آماده.
2. P1ها (US9→US2→US3→US10→US1) یکی‌یکی + E2E هر کدام.
3. P2ها (US6→US4→US8/US13→US11→US12) + E2E.
4. P3ها (US5/US7) + Polish + quickstart کامل §۱–§۹.

### Notes

- [P] = فایل متفاوت، بدون وابستگی.
- [Story] = ردیابی استوری برای هر تسک فاز استوری.
- بعد از هر تسک/گروه منطقی کامیت با پیام `type(scope): subject` + push به main پس از E2E سبز.
- توقف در هر Checkpoint برای اعتبارسنجی مستقل مجاز است.
- `.specify/feature.json` در `.gitignore` است و کامیت نمی‌شود (state محلی) — مسیر فیچر همیشه `specs/001-scalable-worker-fleet`.
