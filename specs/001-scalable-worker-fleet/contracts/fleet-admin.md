# Contract: مدیریت ناوگان (fleet-admin)

**Base**: `/api/v1/admin/fleet` — JWT مدیر + نقش لازم (FR-024). همه خطاها فارسی.

## POST /servers — ثبت سرور + شروع نصب (US-09, FR-020)

Request:
```json
{
  "name": "eu-node-2",
  "ssh_host": "100.64.0.12",
  "ssh_user": "root",
  "auth_type": "password",
  "ssh_password": "****",
  "ssh_key": null,
  "min_workers": 1,
  "max_workers": 6
}
```

- `auth_type=password` → `ssh_password` الزامی؛ `auth_type=key` → `ssh_key` (متن کلید خصوصی) الزامی.
- پاسخ `201`: `{ "id": 7, "provision_job_id": 21, "status": "running", "current_step": "connect" }`
- سکرت فقط ciphertext ذخیره می‌شود (FR-021)؛ پاسخ هیچ‌وقت سکرت برنمی‌گرداند.
- خطاها: `400` ورودی نامعتبر · `403` نقش ناکافی · `422` اتصال SSH ناموفق (پیام فارسی + دلیل، بدون نشت سکرت).

## PATCH /servers/{id} — سقف‌ها، مقیاس، drain (FR-007/017، US-03)

```json
{ "min_workers": 2, "max_workers": 8, "autoscale_enabled": true, "drain": true }
```

- `drain=true` → ورودی جدید قطع، جاری‌ها تمام می‌شوند؛ وضعیت `draining`.
- پاسخ: ردیف به‌روز گره.

## DELETE /servers/{id} — حذف امن (FR-008)

ترتیب سرور: drain → انتظار اتمام جاری‌ها (سقف زمانی) → توقف کانتینرهای گره → پاک‌سازی سکرت → ابطال join_token → وضعیت `decommissioned`.
پاسخ `200`: `{ "ok": true, "moved_tasks": 3 }`. گره حذف‌شده دیگر در چرخه توزیع نیست.

## GET /servers — لیست + سلامت (US-05/10)

```json
{ "servers": [{
  "id": 7, "name": "eu-node-2", "status": "online",
  "workers": {"desired": 3, "observed": 3},
  "load": 5, "queue": 2,
  "engine_healthy": true, "capability_warning": false,
  "last_heartbeat": "2026-09-15T10:00:00Z"
}]}
```
