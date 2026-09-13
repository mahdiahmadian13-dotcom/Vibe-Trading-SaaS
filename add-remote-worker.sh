#!/usr/bin/env bash
# ============================================================================
# Vibe-Trading SaaS — remote worker bootstrap (run on the NEW server)
# Usage:
#   bash add-remote-worker.sh <CENTRAL_IP> <ENGINE_API_KEY> <DB_PASS> [WORKER_NAME]
#
# Downloads worker/, shared/ + compose from GitHub and starts the worker.
# Requires: docker + docker compose plugin + git (private: token in URL).
# ============================================================================
set -euo pipefail

CENTRAL_IP="${1:?Usage: add-remote-worker.sh <CENTRAL_IP> <ENGINE_API_KEY> <DB_PASS> [WORKER_NAME]}"
ENGINE_API_KEY="${2:?missing engine api key}"
DB_PASS="${3:?missing postgres password}"
WORKER_NAME="${4:-}"   # empty => auto worker-<hostname>

REPO="https://github.com/mahdiahmadian13-dotcom/Vibe-Trading-SaaS.git"
DIR="vibe-worker"

echo "==> cloning worker sources"
if [ -d "$DIR" ]; then
  cd "$DIR" && git pull -q || true
else
  git clone -q --depth 1 "$REPO" "$DIR" && cd "$DIR"
fi

echo "==> starting worker (name=${WORKER_NAME:-auto})"
BROKER_URL="redis://$CENTRAL_IP:6379/0" \
ENGINE_URL="http://$CENTRAL_IP:8899" \
ENGINE_API_KEY="$ENGINE_API_KEY" \
DATABASE_URL="postgresql+asyncpg://vt:$DB_PASS@$CENTRAL_IP:5432/vibetrader" \
WORKER_NAME="$WORKER_NAME" \
docker compose -f docker-compose.worker.yml up -d --build

echo "==> done. check: docker compose -f docker-compose.worker.yml logs -f worker"
echo "    (worker appears in admin panel -> Nodes tab within ~30s)"
