# Data Model: ناوگان ورکر مقیاس‌پذیر چندسروره

**Date**: 2026-09-15 | **Spec**: [spec.md](spec.md) | **Research**: [research.md](research.md)

## موجود (بدون تغییر ساختار، فقط ستون جدید)

### users
بدون تغییر. نقش‌ها در جدول جدا (`admin_roles`) و اتصال کاربر→نقش نگه داشته می‌شود تا لاگین و JWT دست نخورد.

### tasks
بدون تغییر ساختار. `worker_name` از قبل دارد (قابل‌مپ به گره از طریق `worker_nodes`)؛ `params/result/progress` برای بازیابی و ادامه کافی‌اند.

### coupons
بدون تغییر ساختار (R7). سیاست جدید فقط در کد grant/claim:
- `source=daily` + `kind=backtest` → انقضا نیمه‌شب تهران، بدون انباشت
- `source=weekly` + `kind=swarm` → انقضا پایان هفته تهران
- `source ∈ {welcome, referral}` → `expires_at=NULL` (اعتبار دائمی)
- ترتیب مصرف اتمیک: daily → welcome/referral

## توسعه موجود

### server_nodes (+ ستون‌های جدید)
گره = یک سرور عضو. وضعیت‌های سلامت: `pending | online | degraded | offline | draining | decommissioned`.

| ستون جدید | نوع | توضیح |
|---|---|---|
| `ssh_host` | String(256) | IP/هاست SSH (فقط شبکه خصوصی) |
| `ssh_user` | String(64) | نام کاربری SSH |
| `ssh_secret` | Text | رمز/کلید خصوصی **رمزنگاری‌شده** (Fernet + مشتق per-server) — هرگز plaintext |
| `ssh_auth_type` | String(16) | `password \| key` |
| `engine_url_local` | String(256) | آدرس انجین همان گره (معمولاً localhost گره) |
| `engine_healthy` | Boolean | چراغ جدا از سلامت ورکرها (R6) |
| `min_workers` / `max_workers` | Integer | سقف مدیر برای مقیاس خودکار (پیش‌فرض 1 / 8) |
| `autoscale_enabled` | Boolean | پیش‌فرض True |
| `capability` | JSON | نتیجه تست توان {cpu, mem, disk, docker_ver, rtt_ms, verdict} |
| `capability_warning` | Boolean | True = «سرور ضعیف» |
| `provision_state` | String(32) | `none \| running \| failed \| ready` |
| `provision_step` | String(64) | مرحله جاری نصب |
| `provision_log` | JSON | لاگ مرحله‌ها [{step, ts, ok, msg_fa}] |
| `node_role` | String(32) | پیش‌فرض `full` (گره کامل) — رزرو آینده |
| `tailscale_ip` | String(64) | IP شبکه خصوصی |

رابطه: یک گره → چند `worker_nodes` → (هر ورکر صف خودش در Redis).

### worker_nodes (+ ستون)
| ستون جدید | نوع | توضیح |
|---|---|---|
| `server_id` | FK → server_nodes | گره میزبان (قبلاً فقط نام تخت بود) |

### engine_nodes (موجود در fleet.py)
هر گره کامل یک ردیف انجین می‌گیرد (`url` = انجین همان گره) تا EnginePool بدون تغییر منطق، بین انجین‌ها توزیع کند.

## جدید

### admin_roles — نقش مدیریتی (US-12)
| ستون | نوع | توضیح |
|---|---|---|
| `id` / `name` | PK / String یکتا | مثل «اپراتور»، «مشاهده‌گر» |
| `perms` | JSON | {dashboard, servers, users, secrets, updates} بولین |
| `created_at` | timestamptz | |

### admin_role_links — کاربر→نقش
| ستون | نوع | توضیح |
|---|---|---|
| `user_id` FK / `role_id` FK | — | یکتا (user, role) |

### fleet_metrics — تاریخچه متریک (R5)
| ستون | نوع | توضیح |
|---|---|---|
| `server_id` FK + `ts` | — | ایندکس مرکب (server, ts) |
| `metric` | String(32) | `load \| queue \| tasks_ok \| tasks_fail \| workers \| rtt` |
| `value` | Float | |
| نگهداری | — | خام ۵ ثانیه‌ای ۲۴ ساعت؛ rollup دقیقه‌ای چند روزه؛ purge روزانه |

### provision_jobs — اجرای نصب (US-09)
| ستون | نوع | توضیح |
|---|---|---|
| `server_id` FK | — | |
| `status` | String | `running \| failed \| ready \| cancelled` |
| `current_step` | String | مرحله جاری (docker → net → engine → workers → agent → bench) |
| `steps` | JSON | وضعیت هر مرحله + پیام فارسی |
| `triggered_by` | FK users | مدیر اجراکننده |
| `created/finished` | timestamptz | |

### update_rollouts — آپدیت مرحله‌ای (US-11, توسعه FleetUpdate)
از `fleet_updates` موجود استفاده + ستون `per_node` JSON (وضعیت هر گره: pending/draining/updating/ok/rolled_back) — جدول جدید لازم نیست.

## State transitions

```
گره:      pending → (نصب) → online ⇄ degraded ⇄ offline
                                  → draining → decommissioned
نصب:      running → ready | failed → (retry از همان مرحله) → running
آپدیت:    pending → draining → updating → ok | rolled_back
تسک نجات: running@dead → queued@fallback → running@healthy → completed (یک نتیجه)
```

## Validation rules (از FRها)

- هر تسک دقیقاً یک ورکر (claim اتمیک dispatcher؛ FR-003)
- claim کوپن اتمیک با ترتیب daily→دائمی؛ برگشت فقط خطای سمت ما (FR-014a/b)
- دسترسی API نصب/حذف/سکرت فقط نقش مجاز، 403 در غیر این صورت (FR-024)
- هیچ سکرت plaintext در هیچ ستونی به‌جز حافظه رانر (FR-021)
