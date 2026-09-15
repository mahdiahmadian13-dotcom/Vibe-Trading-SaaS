# Implementation Plan: ناوگان ورکر مقیاس‌پذیر چندسروره

**Branch**: `001-scalable-worker-fleet` | **Date**: 2026-09-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-scalable-worker-fleet/spec.md` (13 user stories, 27 requirements, 16 success criteria, 19 clarification decisions)

## Summary

تکامل زیرساخت نیمه‌آماده چندسروره به ناوگان تجاری کامل: هر سرور راه‌دور یک **گره کامل** (ورکرها + انجین محلی + ایجنت) که با فرم پنل (IP + رمز/کلید SSH) و **نصب خودکار صفرتا‌صد** عضو می‌شود؛ تنظیم‌کننده موجود به سطح سرور تعمیم می‌یابد (تشخیص مرگ کل سرور + نجات گروهی)؛ پنل به داشبورد گرافیکی زنده + مدیریت کامل چرخه‌عمر گره + نقش‌های مدیریتی ارتقا می‌یابد؛ سهمیه کوپن فری (روزانه/هفتگی، بدون انباشت) + اعتبار دائمی رفرال + برگشت کوپن خطای سمت ما، هزینه LLM را کنترل می‌کند. همه‌چیز پشت شبکه خصوصی (Tailscale/VPN).

## Technical Context

**Language/Version**: Python 3.11 (gateway/worker/agent) + TypeScript React 18 (webapp)

**Primary Dependencies**: FastAPI + uvicorn (4 workers), ARQ + Redis 7 (صف‌ها), SQLAlchemy async + Postgres 16, httpx (انجین‌کلاینت), aiogram (بات), Vite + Tailwind + framer-motion (مینی‌اپ), Docker Compose (گره‌ها)

**Storage**: Postgres (جداول جدید/توسعه‌یافته — پایین), Redis (صف‌ها، رجیستری، heartbeat، کش متریک زنده), دیسک مرکزی (خروجی بک‌تست‌ها — بدون تغییر)

**Testing**: pytest (gateway/worker) + tsc + vite build (webapp) + E2E روی سرور واقعی با browser_console (الزام قانون‌نامه، ماده I)

**Target Platform**: سرور لینوکس (مرکز + گره‌های راه‌دور Ubuntu + Docker)

**Project Type**: Web-service SaaS چندسرویسی (gateway / worker / agent / bot / updater / webapp)

**Performance Goals**: عضویت سرور جدید تا «آماده» < ۱۰ دقیقه (نصب خام < ۱۵ دقیقه)؛ داشبورد زنده هر ۵ ثانیه؛ اطلاع مرگ سرور به مدیر < ۵ دقیقه؛ ۱۰ تسک هم‌زمان بدون گم‌شدگی/تکرار

**Constraints**: گیت‌وی stateless بین ۴ ورکر uvicorn (state فقط Redis)؛ سکرت فقط env؛ فارسی RTL دارک‌مود؛ PDF تازه بدون کش؛ دانلود تلگرام‌نیتیو (توکن multi-use)؛ هیچ پورت داخلی روی اینترنت عمومی؛ تأیید دومرحله‌ای و audit log بیرون از دامنه

**Scale/Scope**: شروع ۱→۳ گره، هر گره ۱–۸ ورکر، هر ورکر concurrency ‏۴؛ تاریخچه متریک چند روزه با نمونه ۵ ثانیه‌ای

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| اصل | وضعیت | توضیح |
|---|---|---|
| I. E2E روی سرور واقعی | ✅ PASS | quickstart.md سناریوهای E2E (عضویت، kill سرور، کوپن، داشبورد) با browser_console/curl تعریف می‌کند |
| II. Worker + stateless gateway | ✅ PASS | کار طولانی فقط در ورکر/نصب‌رانر پس‌زمینه؛ state در Redis/Postgres؛ نصب SSH رانر جدا از ریکوئست |
| III. PDF تازه بدون کش | ✅ PASS | بدون تغییر — خروجی گره‌ها به مرکز برمی‌گردد و مسیر PDF دست نمی‌خورد |
| IV. دانلود تلگرام‌نیتیو | ✅ PASS | بدون تغییر در مسیر دانلود |
| V. فارسی RTL دارک‌مود | ✅ PASS | همه UI جدید فارسی/RTL/دارک؛ پیام‌های نصب و هشدار فارسی |
| VI. سکرت در env | ✅ PASS با توجیه | رمز SSH گره‌ها استثنای طراحی‌شده است: رمزنگاری متقارن با مستر در env + مشتق per-server + پاک‌سازی موقع حذف + هیچ نمایش متن ساده (research.md تصمیم نهایی) |
| VII. یک کلید AI در انجین | ✅ PASS | کلید مشترک روی انجین هر گره (env همان گره)؛ ورکرها هیچ کلید LLM ندارند |

بدون تخلف بدون توجیه → ورود به Phase 0 مجاز.

**Re-check پس از Phase 1 (design)**: هر ۷ گیت همچنان PASS — طراحی هیچ endpoint دانلود/PDF را تغییر نمی‌دهد (FR-022 فقط خواندن متریک است)؛ سکرت SSH دقیقاً با مدل R2 (Fernet + HKDF per-server + purge) مهار شده؛ نقش‌ها در API هم گارد دارند (FR-024) پس ماده VI از سمت UI دور زده نمی‌شود؛ تاریخچه متریک (R5) هیچ وابستگی به کش PDF ندارد.

## Project Structure

### Documentation (this feature)

```text
specs/001-scalable-worker-fleet/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (API endpoints)
│   ├── fleet-admin.md
│   ├── provision.md
│   ├── metrics.md
│   └── coupons.md
└── tasks.md             # Phase 2 output (/speckit.tasks — NOT here)
```

### Source Code (repository root)

```text
gateway/app/
├── main.py            # راوت‌های جدید: provision/metrics/roles/coupons (thin, منطق در سرویس‌ها)
├── dispatch.py        # تعمیم: آگاهی از سرور + نجات سطح سرور
├── fleet.py           # EnginePool per-node (انجین هر گره) + health
├── provision.py       # NEW: نصب خودکار SSH (ماشین‌حالت مرحله‌ای + retry)
├── metrics.py         # NEW: جمع‌آوری/تاریخچه متریک ناوگان
├── roles.py           # NEW: نقش‌های مدیریتی + گارد دسترسی
├── quota.py           # NEW: سهمیه کوپن (روزانه/هفتگی/رفرال/برگشت) — تکامل coupons.py
├── crypto.py          # NEW: رمزنگاری سکرت SSH (مستر env + مشتق per-server)
├── retention.py       # NEW: پاک‌سازی ۳۰ روزه فایل‌ها (متریک می‌ماند)
└── autoscale.py       # NEW: حلقه مقیاس خودکار (عمق صف → desired_workers)

shared/
├── models.py          # توسعه: ServerNode (SSH/نقشه سلامت/سقف‌ها) + جدول‌های جدید
└── queue.py           # بدون تغییر عمده

agent/app/main.py      # توسعه: تست توان + گزارش متریک + اعمال مقیاس + آپدیت مرحله‌ای
worker/app/main.py     # توسعه: ENGINE_URL=انجین همان گره + برگرداندن خروجی به مرکز
docker-compose.node.yml # توسعه: سرویس engine (گره کامل) + ایجنت

webapp/src/
├── pages/AdminPage.tsx     # تب‌های جدید: داشبورد گرافیکی + نصب سرور + نقش‌ها
├── components/fleet/       # NEW: ویجت‌های نمودار/نقشه سلامت/نوار پیشرفت نصب
└── api/admin.ts            # NEW: کلاینت endpointهای ناوگان

tests/
├── contract/   # قرارداد APIهای جدید (provision/metrics/roles/coupons)
├── integration/# سناریوهای چندسرویسی (kill سرور، نصب، آپدیت مرحله‌ای)
└── unit/       # کوپن/کریپتو/اتوسکیل/retention
```

**Structure Decision**: همان ساختار مونو‌ریپوی موجود (gateway/worker/agent/shared/webapp) — فیچر جدید در قالب ماژول‌های جدید داخل gateway + توسعه agent/worker/compose گره + کامپوننت‌های جدید وب‌اپ. هیچ سرویس/ریپوی جدیدی ساخته نمی‌شود (قانون سادگی قانون‌نامه).

## Complexity Tracking

> فقط چون یک مورد نیاز به توجیه دارد:

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| نگهداری رمز SSH در DB (ماده VI) | نصب صفرتا‌صد خودکار بدون SSH دستی، مستلزم اتصال پنل به سرور است | مدل «هر بار مدیر وارد کند» (in-memory) یعنی با هر ری‌استارت گیت‌وی و هر retry نصب، مدیر باید بیدار شود — خودکار بودن را می‌کشد؛ مدل «فقط دستور روی سرور» یعنی حذف کل US-09 که قلب خواسته کاربر است |
