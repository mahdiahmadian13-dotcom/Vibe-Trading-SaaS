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

## 🖥️ Multi-Server Fleet (v2.0 — scalable worker fleet)

**Architecture:** Gateway → dispatcher (per-worker ARQ queues, Redis) → N full-node servers (worker + local engine + agent each) → results mirrored to center.

### Add a server (auto-install from the panel, recommended)
Admin panel → Nodes tab → «افزودن سرور»: enter SSH host/user + password or key.
The gateway installs everything over SSH (docker → private net → engine →
workers → agent → capability bench) with live per-step progress + retry.
SSH secrets are stored Fernet-encrypted (`FLEET_MASTER_KEY` in `.env`).

### Add a server (manual join, alternative path)
On the NEW server: install the node agent, then it heartbeats every 30s
(`POST /api/v1/node/{join_token}/heartbeat`) and reconciles
`desired_workers` locally.

### Private networking (required for remote nodes)
Remote nodes reach the center ONLY over Tailscale/VPN. On center + nodes
(join the same tailnet), then set the `*_PUBLIC` URLs to the center's
Tailscale IP (100.x.y.z). Never expose 6379/5432/8899 publicly —
`PUBLISH_REDIS/PUBLISH_PG` stay `127.0.0.1`.

### Worker caps + autoscale
Each server has `min/max_workers` + `autoscale_enabled` (panel → server
detail). The autoscaler (60s loop) sets `desired = ceil(pending/6)` inside
`[min,max]` with hysteresis + 5-min cooldown; manual panel edits always win.

### Watchdog + Telegram alerts
Silent servers flip `online → degraded (60s) → offline (120s)` with
auto-drain (queued tasks move to fallback, nothing lost) and Telegram
alerts to `FLEET_ALERT_TG_IDS`. Recovery flips back to `online`.

### Coupon quotas (free tier)
1 backtest/day (Tehran midnight, no accumulation) + 1 swarm/week (Monday).
Paid plans unlimited (for now). Referral = +2 permanent credit after daily
coupon. Platform-faulted tasks refundable from the panel.

### Staged rollout updates
Admin panel → «بروزرسانی همه ورکرها»: engine core updates, then nodes roll
out one-by-one (drain → bump epoch → health → back) with cancel + per-node
progress. Failed node rolls back, rest wait for admin decision.

### Admin API (fleet subset)
| Endpoint | Purpose |
|---|---|
| `POST /api/v1/admin/fleet/servers` | Register + auto-install a server |
| `GET /api/v1/admin/servers` | List incl. live worker loads + capability |
| `PATCH /api/v1/admin/servers/{id}` | Caps / autoscale / drain |
| `POST /api/v1/admin/servers/{id}/token/rotate` | Revoke + regenerate join token |
| `GET /api/v1/admin/fleet/metrics/live` | 5s dashboard snapshot |
| `GET /api/v1/admin/fleet/metrics/history` | Downsampled history (≤500 pts) |
| `GET/POST/PATCH/DELETE /api/v1/admin/roles` | Definable admin roles + perms |
| `POST /api/v1/admin/tasks/{id}/refund` | Refund platform-faulted coupon |
| `DELETE /api/v1/admin/fleet/update/{id}` | Cancel a running rollout |
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
