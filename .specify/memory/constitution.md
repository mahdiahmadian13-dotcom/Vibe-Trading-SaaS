# Vibe-Trading SaaS Constitution

<!-- Spec-Kit constitution — principles every spec/plan/tasks/implement cycle must obey.
     Established 2026-09-15. Amend only via /speckit.constitution with user approval. -->

## Core Principles

### I. E2E on the Real Server (NON-NEGOTIABLE)
Every change that touches user-facing behavior MUST be verified end-to-end
against the live stack at `http://127.0.0.1:9001/app/index.html`
(login: `mahdi` / password in server `.env`).
Desktop browser checks are necessary but NOT sufficient — Telegram WebView
behaves differently (downloadFile semantics, attachment-menu limits).
A task is not "done" without `browser_console` evidence or a curl/API proof
recorded in the session.

### II. Worker Architecture — Gateway Never Blocks
Long work (chat, backtest, swarm) runs on ARQ workers via Redis queues,
never inside the gateway request cycle. The gateway (4× uvicorn workers)
shares NO in-memory state — tokens, locks, registries live in Redis only.
New endpoints must be stateless across gateway replicas.

### III. Fresh PDF, No Cache (NON-NEGOTIABLE)
Report PDFs are generated fresh per request (3–5s) with a cache-buster
(`?_=${Date.now()}`). Never serve a cached/stale PDF as "the report".
Download links use multi-use tokens (fixed TTL window, capped fetches —
Telegram's downloader re-fetches the same URL; single-use tokens kill the
save silently).

### IV. Telegram-Native Downloads Only
Files reach the user via `Telegram.WebApp.downloadFile({url, file_name})`
(object signature — never two strings), with `openLink` fallback. Never
blob-only downloads (mobile WebView silently drops them), never
send-to-chat paths. No success notices inside the mini-app; no "send to
Telegram chat" buttons anywhere in the UI.

### V. Persian RTL, Dark Mode
All user-facing text is fluent Persian, RTL layout, dark theme. Error
messages in Persian. Numbers/metrics keep Latin digits where the existing
UI does.

### VI. Secrets in env, Never in Code or Chat
`TELEGRAM_BOT_TOKEN`, JWT secrets, `VIBE_ENGINE_API_KEY`, `vt_token`,
git PAT — only from environment / server `.env`. Never commit `.env`,
`tsconfig.tsbuildinfo`, or credentials. Redact secrets in logs and
user-facing messages.

### VII. One AI Key at the Engine
LLM credentials live ONLY in the Vibe-Trading engine container
(`LANGCHAIN_PROVIDER` / model / key). Workers and gateway hold no LLM
keys — they authenticate to the engine with the shared
`VIBE_ENGINE_API_KEY`. Scaling = more workers / more engines, never
per-worker LLM keys.

## Constraints & Standards

- Stack: FastAPI gateway, ARQ + Redis workers, Postgres, React mini-app,
  aiogram bot, Vibe-Trading engine (HKUDS, `/opt/Vibe-Trading`).
- Referral rule: 2 bonus backtests per referred signup — covered by tests
  whenever referral code paths change.
- Commit messages: `type(scope): subject` + symptom/root-cause/verification
  in the body. Push to `main` after green E2E.
- Spec workflow: `/speckit.specify` → `/speckit.plan` → `/speckit.tasks` →
  `/speckit.implement` → `/speckit.converge` (repeat 4–5 until Converged).

## Governance
Constitution amendments require explicit user approval. When a principle
conflicts with a user request, surface the conflict and let the user decide —
then record the decision here.
