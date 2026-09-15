# Contract: نصب خودکار (provision)

**Base**: `/api/v1/admin/fleet/servers/{id}/provision` — نقش `servers`.

## مدل مرحله‌ای (FR-020)

مراحل به ترتیب: `connect → docker → net → engine → workers → agent → bench → done`

## GET provision — وضعیت زنده (poll پنل هر ۳ ثانیه حین نصب)

```json
{
  "status": "running",
  "current_step": "engine",
  "steps": [
    {"step": "connect", "ok": true, "msg_fa": "اتصال SSH برقرار شد"},
    {"step": "docker", "ok": true, "msg_fa": "داکر نصب/تأیید شد"},
    {"step": "net", "ok": true, "msg_fa": "شبکه خصوصی وصل شد"},
    {"step": "engine", "ok": null, "msg_fa": "در حال نصب انجین…"}
  ]
}
```

- `ok=null` = در حال اجرا؛ `ok=false` = ناموفق (همراه `msg_fa` فارسی و دلیل بدون سکرت).
- نصب نیمه‌کاره → گره `offline` می‌ماند و تسکی به آن نمی‌رود.

## POST provision/retry — تلاش مجدد از همان مرحله

- فقط مرحله ناموفق و بعدی‌ها دوباره اجرا می‌شوند (مراحل موفق تکرار نمی‌شوند).
- `409` اگر نصبی در حال اجراست.
