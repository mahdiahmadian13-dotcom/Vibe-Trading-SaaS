# Contracts: ناوگان ورکر مقیاس‌پذیر چندسروره

**Date**: 2026-09-15 | جزئیات کامل در [fleet-admin.md](fleet-admin.md)، [provision.md](provision.md)، [metrics.md](metrics.md)، [coupons.md](coupons.md).

قراردادها REST روی گیت‌وی (`/api/v1/...`)، احراز هویت JWT مدیر، فارسی در خطاها. امنیت نقش (FR-024) در همه endpointهای حساس سمت سرور اعمال می‌شود.

## خلاصه endpointها

| متد | مسیر | نقش لازم | کار |
|---|---|---|---|
| GET | `/api/v1/admin/fleet/overview` | dashboard | کارت‌های عددی + بار کل ناوگان (زنده) |
| GET | `/api/v1/admin/fleet/servers` | dashboard | لیست گره‌ها + سلامت + ورکرها + سقف‌ها |
| POST | `/api/v1/admin/fleet/servers` | servers | ثبت سرور جدید (IP + SSH) → شروع نصب |
| GET | `/api/v1/admin/fleet/servers/{id}` | dashboard | جزئیات گره + تست توان + لاگ نصب |
| PATCH | `/api/v1/admin/fleet/servers/{id}` | servers | سقف min/max، autoscale، drain، لغو |
| DELETE | `/api/v1/admin/fleet/servers/{id}` | servers | حذف امن (drain → پاک‌سازی سکرت → ابطال توکن) |
| GET | `/api/v1/admin/fleet/servers/{id}/provision` | servers | وضعیت نصب زنده (مرحله + نوار پیشرفت) |
| POST | `/api/v1/admin/fleet/servers/{id}/provision/retry` | servers | تلاش مجدد از همان مرحله ناموفق |
| GET | `/api/v1/admin/fleet/metrics/live` | dashboard | نمونه زنده همه گره‌ها (poll هر ۵ ثانیه) |
| GET | `/api/v1/admin/fleet/metrics/history?from&to&step` | dashboard | تاریخچه downsample (حداکثر ~۵۰۰ نقطه) |
| GET | `/api/v1/admin/fleet/tasks-live` | dashboard | تسک‌های در حال اجرا/صف به‌تفکیک گره |
| POST | `/api/v1/admin/fleet/updates` | updates | شروع آپدیت مرحله‌ای ناوگان |
| DELETE | `/api/v1/admin/fleet/updates/{id}` | updates | لغو آپدیت (قبل/حین با توقف امن) |
| GET/POST | `/api/v1/admin/roles...` | users | CRUD نقش‌ها + اتصال کاربر→نقش |
| GET | `/api/v1/me/quota` | — | سهمیه من (کوپن روزانه/هفتگی/اعتبار دائمی) |

کوپن (کاربر عادی): ثبت تسک بدون کوپن → `402` + پیام فارسی «کوپن امروز تمام شد»؛ هیچ endpoint جدا برای مصرف نیست (مصرف اتمیک داخل ثبت تسک).
