# Contract: متریک و داشبورد (metrics)

## GET /metrics/live — نمونه زنده (poll هر ۵ ثانیه، نقش dashboard)

```json
{
  "ts": "2026-09-15T10:00:05Z",
  "fleet": {"load": 14, "queue": 3, "workers": 9, "tasks_running": 6},
  "servers": [
    {"id": 7, "name": "eu-node-2", "status": "online",
     "load": 5, "queue": 2, "workers": 3,
     "tasks_ok_1h": 11, "tasks_fail_1h": 1, "rtt_ms": 42}
  ],
  "tasks_live": [
    {"task_id": "a1…", "type": "backtest", "user": "mahdi",
     "server": "eu-node-2", "worker": "w-3", "elapsed_s": 95}
  ]
}
```

## GET /metrics/history — تاریخچه (نقش dashboard)

پارامترها: `from, to (ISO), step (auto|1m|15m|1h)، metric`. پاسخ همیشه downsample (≤ ~۵۰۰ نقطه) برای موبایل (R5):

```json
{ "metric": "load", "step": "1m",
  "points": [["2026-09-14T10:00:00Z", 12], ["2026-09-14T10:01:00Z", 9]] }
```

## نمودارهای داشبورد (US-10، FR-022)

- خطی بار ناوگان ← `history(metric=load)` + نقطه زنده از `live`
- میله‌ای تسک‌ها ← تجمیع ساعتی `tasks_ok/tasks_fail` + صف جاری
- دایره‌ای توزیع ← `live.servers[].load`
- نقشه سلامت ← `status/engine_healthy/capability_warning` هر گره (SVG دستی)
- اعداد فارسی با `Intl.NumberFormat('fa-IR')`؛ همه RTL و دارک‌مود (ماده V).
