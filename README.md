# 🚀 Vibe-Trading SaaS

Multi-tenant SaaS platform wrapping [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) as a sellable product with Telegram bot, parallel workers, and horizontal scaling.

## ✨ Features

- 🤖 **Telegram Bot** — Professional Persian/English bot with AI chat, backtest, swarm analysis
- 👥 **Multi-Tenant** — User management, subscriptions, rate limiting, anti-abuse
- ⚡ **Parallel Workers** — Horizontal scaling, add servers with one command
- 🔐 **JWT Auth** — Secure authentication with device fingerprinting
- 💳 **Payment** — IDPay integration for Iranian market
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

### Add Worker Server

```bash
curl -fsSL https://raw.githubusercontent.com/mahdiahmadian13-dotcom/Vibe-Trading-SaaS/main/setup.sh | bash -s -- \
  --role worker \
  --broker redis://CENTRAL_IP:6379
```

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
