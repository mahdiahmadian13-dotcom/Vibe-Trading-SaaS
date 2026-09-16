# Vibe-Trading SaaS

Multi-server fleet platform: gateway + ARQ workers + local Vibe-Trading engine per node + Telegram bot + updater.

## Quick start

```bash
cp .env.example .env   # fill FLEET_MASTER_KEY, DB, Redis, OpenAI relay
docker compose up -d --build
```

## Fleet architecture

- **Gateway** (`gateway/`): FastAPI, dispatcher, admin API, health loops
- **Workers** (`worker/`): ARQ workers, per-server queues
- **Engine** (`/opt/Vibe-Trading`): Vibe-Trading agent, one per full node
- **Bot** (`bot/`): Telegram listener
- **Updater** (`updater/`): staged rollout

## Deploy webapp after source changes

```bash
./deploy-webapp.sh   # builds webapp, copies dist → gateway/app/static, rebuilds gateway
```

Do **not** skip this step — `webapp/dist` is gitignored; the gateway serves from `static/`.