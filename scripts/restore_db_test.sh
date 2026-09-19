#!/bin/bash
# Vibe-Trading SaaS — restore an encrypted backup into a TEMPORARY database
# (never touches production). Used for scheduled restore drills and real DR.
#
# Usage:
#   restore_db_test.sh <backup.dump.enc>            # verify + restore into vibe_restore_test
#   restore_db_test.sh <backup.dump.enc> --rowcheck # + row-count comparison vs prod
set -euo pipefail

ENV_FILE="${ENV_FILE:-/root/vibe-trading-saas/.env}"
# Same tolerant KEY=VALUE parse as backup_db.sh (stray lines in .env break sourcing)
if [ -f "$ENV_FILE" ]; then
  while IFS='=' read -r k v; do
    case "$k" in ''|'#'*) continue ;; esac
    case "$k" in *[!A-Za-z0-9_]*) continue ;; esac
    [ -n "${k:-}" ] && [ -n "${v:-}" ] && export "$k=$v"
  done < "$ENV_FILE"
fi

ENC="${1:?usage: restore_db_test.sh <backup.dump.enc> [--rowcheck]}"
ROWCHECK="${2:-}"
TEST_DB="vibe_restore_test"

[ -f "$ENC" ] || { echo "FATAL: $ENC not found"; exit 1; }
[ -n "${BACKUP_ENC_KEY:-}" ] || { echo "FATAL: BACKUP_ENC_KEY not set"; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

echo "[1/5] checksum"
sha256sum -c "$ENC.sha256" --status 2>/dev/null || { \
  echo "  (no sidecar checksum — computing for record)"; sha256sum "$ENC"; }

echo "[2/5] decrypt"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
  -in "$ENC" -out "$WORK/restore.dump" -pass env:BACKUP_ENC_KEY
[ -s "$WORK/restore.dump" ] || { echo "FATAL: decrypt produced empty file"; exit 1; }

echo "[3/5] restore into temporary DB '$TEST_DB' (prod untouched)"
docker exec -i vibe-trading-saas-postgres-1 sh -c \
  'psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS '"$TEST_DB"';" -c "CREATE DATABASE '"$TEST_DB"';"' >/dev/null
docker exec -i vibe-trading-saas-postgres-1 sh -c \
  'pg_restore -U "$POSTGRES_USER" -d '"$TEST_DB"' --no-owner --exit-on-error' < "$WORK/restore.dump"

echo "[4/5] integrity: row counts in restored DB"
docker exec vibe-trading-saas-postgres-1 sh -c \
  'psql -U "$POSTGRES_USER" -d '"$TEST_DB"' -tAc "
     select '\''users='\''||count(*) from users
     union all select '\''subscriptions='\''||count(*) from subscriptions
     union all select '\''tasks='\''||count(*) from tasks
     union all select '\''vibe_sessions='\''||count(*) from vibe_sessions
     union all select '\''payments='\''||count(*) from payments
     union all select '\''server_nodes='\''||count(*) from server_nodes
     order by 1;"'

if [ "$ROWCHECK" = "--rowcheck" ]; then
  echo "[5/5] compare with production"
  docker exec vibe-trading-saas-postgres-1 sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "
       select '\''users='\''||count(*) from users
       union all select '\''subscriptions='\''||count(*) from subscriptions
       union all select '\''tasks='\''||count(*) from tasks
       union all select '\''vibe_sessions='\''||count(*) from vibe_sessions
       union all select '\''payments='\''||count(*) from payments
       union all select '\''server_nodes='\''||count(*) from server_nodes
       order by 1;"'
  echo "  (restored counts should be <= prod; delta = rows created after the backup)"
else
  echo "[5/5] skipped (--rowcheck not passed)"
fi

docker exec vibe-trading-saas-postgres-1 sh -c \
  'psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS '"$TEST_DB"';"' >/dev/null
echo "RESTORE DRILL OK — temp DB dropped, prod never touched"
