#!/usr/bin/env bash
# ============================================================================
# Vibe-Trading SaaS — One-line Server Setup
# ============================================================================
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/.../setup.sh | bash
#   curl -fsSL https://.../setup.sh | bash -s -- --role worker --broker redis://IP:6379
#   curl -fsSL https://.../setup.sh | bash -s --role engine
# ============================================================================

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; exit 1; }
info()  { echo -e "${BLUE}[i]${NC} $*"; }

# ============================================================================
# Parse Arguments
# ============================================================================
ROLE="central"           # central | worker | engine | bot
BROKER_URL=""            # redis://IP:6379 (required for worker)
ENGINE_URL=""            # http://IP:8899 (required for worker)
GATEWAY_URL=""           # http://IP:9000 (required for bot)
PROJECT_DIR="/opt/vibe-trading-saas"
GIT_REPO="https://github.com/mahdiahmadian13-dotcom/Vibe-Trading-SaaS.git"
BRANCH="main"

while [[ $# -gt 0 ]]; do
    case $1 in
        --role)      ROLE="$2"; shift 2 ;;
        --broker)    BROKER_URL="$2"; shift 2 ;;
        --engine)    ENGINE_URL="$2"; shift 2 ;;
        --gateway)   GATEWAY_URL="$2"; shift 2 ;;
        --dir)       PROJECT_DIR="$2"; shift 2 ;;
        --branch)    BRANCH="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--role central|worker|engine|bot] [--broker redis://...] [--engine http://...]"
            exit 0
            ;;
        *) error "Unknown argument: $1" ;;
    esac
done

# ============================================================================
# System Requirements
# ============================================================================
install_system_deps() {
    log "Installing system dependencies..."
    
    apt-get update -qq
    apt-get install -y -qq \
        curl wget git \
        docker.io docker-compose-plugin \
        jq htop tmux \
        > /dev/null 2>&1
    
    # Docker Compose v2 (standalone)
    if ! command -v docker-compose &>/dev/null; then
        curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
            -o /usr/local/bin/docker-compose
        chmod +x /usr/local/bin/docker-compose
    fi
    
    # Start Docker
    systemctl enable docker
    systemctl start docker
    
    log "System dependencies installed"
}

# ============================================================================
# Clone / Update Repository
# ============================================================================
clone_or_update() {
    if [ -d "$PROJECT_DIR/.git" ]; then
        log "Updating repository..."
        cd "$PROJECT_DIR"
        git pull origin "$BRANCH" --quiet
    else
        log "Cloning repository..."
        rm -rf "$PROJECT_DIR"
        git clone --branch "$BRANCH" --depth 1 "$GIT_REPO" "$PROJECT_DIR"
    fi
    cd "$PROJECT_DIR"
}

# ============================================================================
# Generate Secrets
# ============================================================================
generate_secrets() {
    local env_file="$PROJECT_DIR/.env"
    
    if [ ! -f "$env_file" ]; then
        log "Generating secrets..."
        
        JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
        API_KEY=$(python3 -c "import secrets; print('vt-' + secrets.token_hex(24))")
        POSTGRES_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
        
        cat > "$env_file" << EOF
# ============================================================================
# Vibe-Trading SaaS — Environment Configuration
# Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# ============================================================================

# --- Roles ---
ROLE=${ROLE}

# --- Gateway ---
GATEWAY_HOST=0.0.0.0
GATEWAY_PORT=9000
JWT_SECRET=${JWT_SECRET}
API_KEY=${API_KEY}

# --- Redis ---
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_URL=redis://redis:6379/0

# --- PostgreSQL ---
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=vibetrader
POSTGRES_USER=vt
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}

# --- Vibe-Trading Engine ---
VIBE_ENGINE_URL=http://engine:8899
VIBE_ENGINE_API_KEY=${API_KEY}

# --- Telegram Bot ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USERS=

# --- Worker ---
WORKER_CONCURRENCY=4
WORKER_MAX_TASKS=100

# --- Payment (IDPay) ---
IDPAY_API_KEY=
IDPAY_MERCHANT_ID=

# --- Rate Limits ---
RATE_LIMIT_MESSAGES_PER_MINUTE=20
RATE_LIMIT_SESSIONS_PER_DAY=50

# --- Anti-Abuse ---
MAX_ACCOUNTS_PER_DEVICE=1
MESSAGE_MIN_INTERVAL_SECONDS=2.0
EOF
        
        log "Secrets generated. Edit .env to add:"
        warn "  - TELEGRAM_BOT_TOKEN"
        warn "  - IDPAY_API_KEY / IDPAY_MERCHANT_ID"
    else
        log "Using existing .env"
    fi
}

# ============================================================================
# Central Server Setup
# ============================================================================
setup_central() {
    log "Setting up CENTRAL server (Broker + Gateway + Bot)..."
    
    install_system_deps
    clone_or_update
    generate_secrets
    
    # Build and start
    cd "$PROJECT_DIR"
    docker compose build --quiet
    docker compose up -d
    
    # Wait for services
    sleep 5
    
    # Health check
    if curl -sf http://localhost:9000/health > /dev/null 2>&1; then
        log "Gateway is healthy ✓"
    else
        warn "Gateway may still be starting..."
    fi
    
    # Print info
    echo ""
    echo "=========================================="
    echo -e "${GREEN}CENTRAL SERVER READY${NC}"
    echo "=========================================="
    echo ""
    echo "  Gateway:  http://$(hostname -I | awk '{print $1}'):9000"
    echo "  Swagger:  http://$(hostname -I | awk '{print $1}'):9000/docs"
    echo "  Redis:    localhost:6379"
    echo "  Postgres: localhost:5432"
    echo ""
    echo "  To add worker servers:"
    echo "    curl -fsSL $GIT_REPO/raw/main/setup.sh | bash -s -- \\"
    echo "      --role worker --broker redis://$(hostname -I | awk '{print $1}'):6379"
    echo ""
}

# ============================================================================
# Worker Server Setup
# ============================================================================
setup_worker() {
    [ -z "$BROKER_URL" ] && error "Worker requires --broker redis://CENTRAL_IP:6379"
    
    log "Setting up WORKER server..."
    log "  Broker: $BROKER_URL"
    
    install_system_deps
    clone_or_update
    
    # Build worker image
    cd "$PROJECT_DIR"
    docker compose -f docker-compose.worker.yml build --quiet
    
    # Start worker
    docker compose -f docker-compose.worker.yml up -d
    
    log "Worker started and connected to broker"
    
    echo ""
    echo "=========================================="
    echo -e "${GREEN}WORKER SERVER READY${NC}"
    echo "=========================================="
    echo ""
    echo "  Worker:   Running"
    echo "  Broker:   $BROKER_URL"
    echo "  Engine:   ${ENGINE_URL:-auto-discover}"
    echo ""
    echo "  Add more workers by running the same command on another server!"
    echo ""
}

# ============================================================================
# Engine Server Setup
# ============================================================================
setup_engine() {
    log "Setting up ENGINE server (Vibe-Trading Docker)..."
    
    install_system_deps
    
    # Clone Vibe-Trading
    ENGINE_DIR="/opt/Vibe-Trading"
    if [ -d "$ENGINE_DIR/.git" ]; then
        cd "$ENGINE_DIR" && git pull
    else
        git clone --depth 1 https://github.com/HKUDS/Vibe-Trading.git "$ENGINE_DIR"
    fi
    
    cd "$ENGINE_DIR"
    
    # Setup env
    if [ ! -f agent/.env ]; then
        cp agent/.env.example agent/.env
        warn "Edit agent/.env to configure LLM provider"
    fi
    
    # Build and run
    DOCKER_BUILDKIT=1 docker compose build --quiet
    docker compose up -d
    
    log "Engine started on port 8899"
    
    echo ""
    echo "=========================================="
    echo -e "${GREEN}ENGINE SERVER READY${NC}"
    echo "=========================================="
    echo ""
    echo "  Engine: http://$(hostname -I | awk '{print $1}'):8899"
    echo "  Docs:   http://$(hostname -I | awk '{print $1}'):8899/docs"
    echo ""
    echo "  Update central .env with:"
    echo "    VIBE_ENGINE_URL=http://$(hostname -I | awk '{print $1}'):8899"
    echo ""
}

# ============================================================================
# Bot Server Setup
# ============================================================================
setup_bot() {
    [ -z "$GATEWAY_URL" ] && error "Bot requires --gateway http://CENTRAL_IP:9000"
    
    log "Setting up TELEGRAM BOT server..."
    
    install_system_deps
    clone_or_update
    generate_secrets
    
    cd "$PROJECT_DIR"
    
    # Update .env with gateway URL
    sed -i "s|^GATEWAY_URL=.*|GATEWAY_URL=$GATEWAY_URL|" .env
    
    # Build and start bot
    docker compose -f docker-compose.bot.yml build --quiet
    docker compose -f docker-compose.bot.yml up -d
    
    log "Bot started"
    
    echo ""
    echo "=========================================="
    echo -e "${GREEN}TELEGRAM BOT READY${NC}"
    echo "=========================================="
    echo ""
    echo "  Bot:      Running"
    echo "  Gateway:  $GATEWAY_URL"
    echo ""
}

# ============================================================================
# Main
# ============================================================================
echo ""
echo "=========================================="
echo "  Vibe-Trading SaaS — Server Setup"
echo "  Role: $ROLE"
echo "=========================================="
echo ""

case "$ROLE" in
    central) setup_central ;;
    worker)  setup_worker ;;
    engine)  setup_engine ;;
    bot)     setup_bot ;;
    *)       error "Unknown role: $ROLE (use: central, worker, engine, bot)" ;;
esac

log "Setup complete!"
