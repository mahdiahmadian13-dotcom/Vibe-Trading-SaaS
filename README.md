# 🚀 Vibe-Trading SaaS

Multi-tenant SaaS platform wrapping [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) as a sellable product with Telegram bot, parallel workers, and horizontal scaling.

## ✨ Features

> 🆕 **Server Fleet v1.9** — one-line server join (`curl | bash`) + panel-controlled
> per-server worker scaling (+/− in the Nodes tab). Each joined server runs a tiny
> agent that reconciles desired state from the control plane every 10s.

- 🤖 **Telegram Bot** — Professional Persian/English bot with AI chat, backtest, swarm analysis
- 👥 **Multi-Tenant** — User management, subscriptions, rate limiting, anti-abuse
- ⚡ **Parallel Workers** — Horizontal scaling, add servers with one command
- 🔐 **JWT Auth** — Secure authentication with device fingerprinting
- 💳 **Payment** — IDPay integration: create → bank gateway → callback verify → auto-activate subscription
- 🖥️ **Engine Fleet** — Multi-engine/multi-server: least-loaded routing, automatic health checks, failover
- 🛠️ **Admin API** — Manage engines, workers, users, plans via REST (`/api/v1/admin/*`)
- 📊 **Full API** — REST API for mobile apps, web, or third-party integrations

## 🏗️ Architecture

```
[Telegram Bot] ──→ [Gateway :9000] ──→ [Redis Broker]
                                            │
                        ┌───────────────────┼───────────────────┐
                        ▼                   ▼                   ▼
                  [Worker #1]         [Worker #2]         [Worker #N]
                        │                   │                   │
                        └───────────────────┼───────────────────┘
                                            ▼
                                   [Vibe-Trading :8899]
```

## ⚡ Quick Start

### One-Line Install (Central Server)

```bash
curl -fsSL https://raw.githubusercontent.com/mahdiahmadian13-dotcom/Vibe-Trading-SaaS/main/setup.sh | bash
```

### Add Worker Server (one-line join, panel-controlled scaling)

From the admin panel → **Nodes tab → Servers → «افزودن سرور»**: register a name and get a
one-line installer. Run it on the new server — the node agent joins automatically and
its workers appear in the panel within ~30s:

```bash
curl -fsSL http://CENTRAL_IP:9001/install/<JOIN_TOKEN> | bash
```

The installer (idempotent): installs Docker if missing, downloads the node bundle
(agent + worker + compose file), and starts the agent. The agent then:
- pulls desired state (worker count, per-worker concurrency, CPU/RAM limits) from the control plane every 10s,
- scales the `worker` service via `docker compose --scale`,
- heartbeats observed state, host info (CPU/RAM/disk), and Docker health every 15s.

Scale workers per-server from the panel with **+/−** (or the
`POST /api/v1/admin/servers/{id}/scale` API) — applied within ~10s on the remote server.
Requirements: the new server must reach the central Redis (6379), Postgres (5432), and
Engine (8899); both are exposed by default (restrict with a firewall for production).

### Add Engine Server

```bash
curl -fsSL https://raw.githubusercontent.com/mahdiahmadian13-dotcom/Vibe-Trading-SaaS/main/setup.sh | bash -s -- --role engine
```

## 📁 Project Structure

```
vibe-trading-saas/
├── gateway/              # API Gateway (FastAPI)
│   ├── app/main.py       # Routes, auth, proxy, rate limiting
│   ├── Dockerfile
│   └── requirements.txt
│
├── worker/               # Task Worker (ARQ)
│   ├── app/main.py       # Task definitions, engine client
│   ├── Dockerfile
│   └── requirements.txt
│
├── bot/                  # Telegram Bot (aiogram 3.x)
│   ├── app/main.py       # Handlers, keyboards, state management
│   ├── Dockerfile
│   └── requirements.txt
│
├── shared/               # Shared code
│   ├── config.py         # Pydantic Settings
│   ├── models.py         # SQLAlchemy ORM
│   └── security.py       # JWT + Password hashing
│
├── docker-compose.yml           # Central server
├── docker-compose.worker.yml    # Remote worker
├── setup.sh                     # One-line installer
├── .env.example
└── ARCHITECTURE.md
```

## 🔧 Manual Setup

### Prerequisites
- Docker + Docker Compose
- 4GB+ RAM (for engine)
- Vibe-Trading engine running on port 8899

### 1. Clone & Configure

```bash
git clone https://github.com/mahdiahmadian13-dotcom/Vibe-Trading-SaaS.git
cd Vibe-Trading-SaaS
cp .env.example .env
nano .env  # Fill in: JWT_SECRET, API_KEY, POSTGRES_PASSWORD, TELEGRAM_BOT_TOKEN
```

### 2. Start Services

```bash
docker compose up -d
```

### 3. Verify

```bash
# Health check
curl http://localhost:9000/health

# Swagger docs
open http://localhost:9000/docs
```

## 📡 API Endpoints

### Auth
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/auth/register` | Register new user |
| POST | `/api/v1/auth/login` | Login |

### Vibe-Trading Proxy
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/vibe/sessions` | Create chat session |
| POST | `/api/v1/vibe/sessions/{id}/messages` | Send message |
| GET | `/api/v1/vibe/sessions/{id}/messages` | Get messages |
| GET | `/api/v1/vibe/sessions/{id}/events` | SSE stream |
| GET | `/api/v1/vibe/runs` | List backtest runs |
| GET | `/api/v1/vibe/swarm/presets` | List swarm presets |
| POST | `/api/v1/vibe/swarm/runs` | Start swarm run |
| GET | `/api/v1/vibe/swarm/runs/{id}` | Get swarm status |

### Subscriptions
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/subscription/plans` | List available plans |
| GET | `/api/v1/subscription/current` | Current user plan |

### Admin
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/admin/users` | List all users |
| GET | `/api/v1/admin/tasks` | List all tasks |

## 🎯 Subscription Tiers

| Tier | Price/month | Sessions/day | Messages | Backtests | Swarm | Live |
|------|------------|-------------|----------|-----------|-------|------|
| Free | 0 | 3 | 10 | 1 | 0 | ❌ |
| Basic | 299,000 | 20 | 50 | 10 | 3 | ❌ |
| Pro | 799,000 | 100 | 200 | 50 | 20 | ✅ |
| Enterprise | 1,999,000 | ∞ | ∞ | ∞ | ∞ | ✅ |

## 🐳 Docker Compose Services

### Central Server
| Service | Port | Description |
|---------|------|-------------|
| redis | 6379 | Central broker |
| postgres | 5432 | Database |
| gateway | 9000 | API Gateway |
| bot | - | Telegram bot |
| worker | - | Local worker |

### Remote Worker
```bash
BROKER_URL=redis://CENTRAL:6379 docker compose -f docker-compose.worker.yml up -d
```

## 🔒 Security

- JWT authentication with 7-day expiry
- Password hashing with PBKDF2-SHA256 + salt
- Rate limiting per user (configurable)
- Device fingerprinting (SSAID) for anti-abuse
- CORS protection
- Admin-only routes

## 🧪 Development

```bash
# Run gateway locally
cd gateway
pip install -r requirements.txt
uvicorn app.main:app --reload --port 9000

# Run worker locally
cd worker
pip install -r requirements.txt
arq app.main.WorkerSettings

# Run bot locally
cd bot
pip install -r requirements.txt
python -m app.main
```

## 📝 License

MIT License — see [LICENSE](LICENSE) for details.

## 🙏 Credits

- [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) — The AI trading engine
- [HKUDS](https://github.com/HKUDS) — Hong Kong University Data Science Lab


---

## 🖥️ Multi-Server Fleet (v1.8)

**Architecture:** Gateway → ARQ queue (Redis) → N Workers (any server) → Engine Fleet (any server)

### Add an Engine (new AI-core server)
```bash
# 1. Run Vibe-Trading engine on the new server (port 8899)
# 2. Register it in the gateway:
curl -X POST http://GATEWAY:9001/api/v1/admin/fleet \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"engine-2","url":"http://NEW_SERVER:8899","max_concurrency":8}'
# Health probe runs automatically (FLEET_HEALTH_INTERVAL=30s, failover after 2 failures)
```

### Add a Worker (new processing server)
```bash
# On the NEW server:
git clone https://github.com/mahdiahmadian13-dotcom/Vibe-Trading-SaaS.git
cd Vibe-Trading-SaaS
BROKER_URL=redis://CENTRAL_IP:6379 \
ENGINE_URL=http://CENTRAL_IP:8899 \
ENGINE_API_KEY=$VIBE_ENGINE_API_KEY \
DATABASE_URL=postgresql+asyncpg://vt:PASS@CENTRAL_IP:5432/vibetrader \
WORKER_NAME=worker-eu-1 \
docker compose -f docker-compose.worker.yml up -d --build
# Heartbeat every 30s → appears in GET /api/v1/admin/workers
```

### Admin API
| Endpoint | Purpose |
|---|---|
| `GET/POST/PUT/DELETE /api/v1/admin/fleet` | Engine nodes CRUD + live health |
| `POST /api/v1/admin/fleet/{id}/health` | Force health probe |
| `GET /api/v1/admin/workers` | Live worker registry (DB mirror of Redis) |
| `POST /api/v1/admin/users/{id}/grant` | Grant/extend subscription manually |
| `PUT /api/v1/admin/users/{id}/toggle` | Enable/disable user |

### Payments (IDPay)
1. `POST /api/v1/payments/create` → returns bank link (user pays in Toman)
2. IDPay redirects back → `GET /api/v1/payments/callback` verifies server-side
3. On `status=100` the subscription is auto-extended (Payment + Subscription rows)
4. Bot: «اشتراک من» → 💳 خرید → payment link button

Env: `IDPAY_API_KEY`, `PUBLIC_BASE_URL` (or `PAYMENT_CALLBACK_URL`) in `.env`.
