# Vibe-Trading SaaS — Distributed Architecture

## Overview

 horizontally-scalable trading SaaS with:
- **Central Broker** (Redis) — coordinates all workers
- **Gateway** (FastAPI) — single entry point for all clients
- **Workers** (ARQ) — horizontally scalable, add new servers with one command
- **Engine** (Vibe-Trading Docker) — the AI brain (can be remote or local)
- **Telegram Bot** — client interface

## Architecture Diagram

```
                         ┌─────────────┐
                         │   Telegram   │
                         │     Bot      │
                         └──────┬───────┘
                                │
                    ┌───────────▼───────────┐
                    │     API GATEWAY       │
                    │    FastAPI :9000       │
                    │  ┌─────────────────┐  │
                    │  │ JWT Auth        │  │
                    │  │ Subscriptions   │  │
                    │  │ Rate Limiting   │  │
                    │  │ Payment         │  │
                    │  └─────────────────┘  │
                    └───────────┬───────────┘
                                │
                    ┌───────────▼───────────┐
                    │     REDIS BROKER      │
                    │   (Central Queue)     │
                    │  ┌─────────────────┐  │
                    │  │ Task Queue      │  │
                    │  │ Session State   │  │
                    │  │ Pub/Sub Events  │  │
                    │  │ Rate Limits     │  │
                    │  └─────────────────┘  │
                    └───┬───────┬───────┬───┘
                        │       │       │
               ┌────────▼──┐ ┌──▼─────┐ ┌▼────────┐
               │ Worker #1 │ │Worker#2│ │Worker #N│
               │ (Server1) │ │(Srvr2) │ │(ServerN)│
               │           │ │        │ │         │
               │ backtest  │ │ swarm  │ │  chat   │
               │ analysis  │ │ multi  │ │  query  │
               └─────┬─────┘ └───┬────┘ └────┬────┘
                     │           │            │
                     └───────────┼────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   VIBE ENGINE :8899     │
                    │  (Docker, can be remote)│
                    │  66 MCP tools           │
                    │  30 swarm presets        │
                    │  90 skills              │
                    └─────────────────────────┘
```

## Server Roles

### 1. Central Server (Broker + Gateway)
Runs:
- Redis (broker)
- PostgreSQL (database)
- FastAPI Gateway
- Telegram Bot

### 2. Worker Servers (horizontal scaling)
Each runs:
- ARQ Worker (connects to central Redis)
- Optionally: Vibe-Trading Engine (local Docker)

### 3. Engine Server (can be same as Central)
Runs:
- Vibe-Trading Docker
- Can be remote (workers connect via HTTP)

## Adding a New Server

```bash
# On any new Ubuntu/Debian server:
curl -fsSL https://raw.githubusercontent.com/mahdiahmadian13-dotcom/Vibe-Trading-SaaS/main/setup.sh | bash

# Or with specific role:
curl -fsSL https://raw.githubusercontent.com/mahdiahmadian13-dotcom/Vibe-Trading-SaaS/main/setup.sh | bash -s -- --role worker --broker redis://CENTRAL_IP:6379
```

## File Structure

```
vibe-trading-saas/
│
├── setup.sh                    # One-line server setup
├── docker-compose.yml          # Central server compose
├── docker-compose.worker.yml   # Worker server compose
│
├── gateway/                    # API Gateway
│   ├── app/
│   │   ├── main.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── database.py
│   │   │   └── security.py
│   │   ├── api/
│   │   │   ├── auth.py
│   │   │   ├── subscriptions.py
│   │   │   ├── proxy.py
│   │   │   └── admin.py
│   │   ├── models/
│   │   │   └── models.py
│   │   └── services/
│   │       ├── payment.py
│   │       └── usage.py
│   ├── Dockerfile
│   └── requirements.txt
│
├── worker/                     # Task Worker
│   ├── app/
│   │   ├── main.py
│   │   ├── tasks/
│   │   │   ├── backtest.py
│   │   │   ├── swarm.py
│   │   │   └── chat.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   └── engine_client.py
│   │   └── workers/
│   │       └── arq_worker.py
│   ├── Dockerfile
│   └── requirements.txt
│
├── bot/                        # Telegram Bot
│   ├── app/
│   │   ├── main.py
│   │   ├── handlers/
│   │   │   ├── start.py
│   │   │   ├── chat.py
│   │   │   ├── backtest.py
│   │   │   └── swarm.py
│   │   ├── middlewares/
│   │   │   ├── auth.py
│   │   │   └── rate_limit.py
│   │   ├── keyboards/
│   │   │   └── inline.py
│   │   └── core/
│   │       ├── config.py
│   │       └── gateway_client.py
│   ├── Dockerfile
│   └── requirements.txt
│
└── shared/                     # Shared code
    ├── models/
    │   └── schemas.py
    └── utils/
        └── redis.py
```

## Task Flow Example

1. User sends "/backtest AAPL" to Telegram bot
2. Bot calls Gateway: `POST /api/v1/tasks` with `{type: "backtest", params: {...}}`
3. Gateway checks auth + subscription limits
4. Gateway pushes task to Redis queue: `RPUSH tasks:backtest {...}`
5. Worker picks up task: `BRPOP tasks:backtest`
6. Worker calls Engine: `POST http://ENGINE:8899/sessions/{sid}/messages`
7. Worker streams SSE events back
8. Worker publishes progress to Redis Pub/Sub: `PUBLISH user:{user_id}:progress {...}`
9. Bot subscribes to user's channel: `SUBSCRIBE user:{user_id}:progress`
10. Bot sends progress updates to Telegram user
11. Worker completes → stores result → sends final answer
