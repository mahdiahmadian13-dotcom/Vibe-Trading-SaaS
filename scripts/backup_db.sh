#!/bin/bash
# Vibe-Trading SaaS — automated encrypted DB backup (specs/002 hygiene)
#
# - pg_dump (custom format, compressed) of the live vibetrader DB
# - AES-256 encrypted at rest (openssl enc) with BACKUP_ENC_KEY from .env
# - SHA256 manifest per file (tamper-evident)
# - retention: keep last N days locally (default 14), prune older
# - optional rclone sync to Cloudflare R2 when R2 credentials exist
#
# Env (from /root/vibe-trading-saas/.env or env override):
#   BACKUP_DIR           default /root/vibe-backups
#   BACKUP_KEEP_DAYS     default 14
#   BACKUP_ENC_KEY       REQUIRED (32+ chars) — generated on first run if absent
#   R2_REMOTE            optional rclone remote name (e.g. r2:vibe-backups)
set -euo pipefail

ENV_FILE="${ENV_FILE:-/root/vibe-trading-saas/.env}"
# Source only simple KEY=VALUE lines — .env contains stray non-export lines
# (e.g. "XXX_PLACEHOLDERhttp://...") that would kill `set -a` sourcing.
if [ -f "$ENV_FILE" ]; then
  while IFS='=' read -r k v; do
    case "$k" in ''|'#'*) continue ;; esac
    case "$k" in *[!A-Za-z0-9_]*) continue ;; esac
    [ -n "${k:-}" ] && [ -n "${v:-}" ] && export "$k=$v"
  done < "$ENV_FILE"
fi

BACKUP_DIR="${BACKUP_DIR:-/root/vibe-backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_RAW="$BACKUP_DIR/vibetrader_${STAMP}.dump"
OUT_ENC="$OUT_RAW.enc"
LOG_TAG="vibe-backup"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

log() { echo "[$(date '+%F %T')] $*"; }

# --- encryption key: auto-generate once, never printed ----------------------
if [ -z "${BACKUP_ENC_KEY:-}" ]; then
  BACKUP_ENC_KEY="$(openssl rand -base64 48)"
  if [ -f "$ENV_FILE" ] && ! grep -q '^BACKUP_ENC_KEY=' "$ENV_FILE"; then
    printf '\n# Auto-generated %s — DO NOT COMMIT, DO NOT LOSE (backups are useless without it)\nBACKUP_ENC_KEY=%s\n' "$(date +%F)" "$BACKUP_ENC_KEY" >> "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    log "generated BACKUP_ENC_KEY into $ENV_FILE"
  else
    log "FATAL: BACKUP_ENC_KEY unset and cannot persist to $ENV_FILE"; exit 1
  fi
fi

# --- dump + encrypt ----------------------------------------------------------
log "starting pg_dump → $OUT_RAW"
docker exec vibe-trading-saas-postgres-1 sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -Z6' > "$OUT_RAW"

[ -s "$OUT_RAW" ] || { log "FATAL: empty dump"; rm -f "$OUT_RAW"; exit 1; }

openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt \
  -in "$OUT_RAW" -out "$OUT_ENC" -pass env:BACKUP_ENC_KEY
rm -f "$OUT_RAW"

sha256sum "$OUT_ENC" > "$OUT_ENC.sha256"

SIZE=$(du -h "$OUT_ENC" | cut -f1)
log "encrypted backup ready: $OUT_ENC ($SIZE)"

# --- retention ---------------------------------------------------------------
DELETED=$(find "$BACKUP_DIR" -name 'vibetrader_*.dump.enc' -mtime +"$KEEP_DAYS" -print -delete | wc -l)
find "$BACKUP_DIR" -name 'vibetrader_*.dump.enc.sha256' -mtime +"$KEEP_DAYS" -delete
log "retention: pruned $DELETED old backups (keep ${KEEP_DAYS}d)"

# --- offsite (R2 via rclone) — best-effort -----------------------------------
if [ -n "${R2_REMOTE:-}" ] && command -v rclone >/dev/null 2>&1; then
  if rclone copy "$OUT_ENC" "$R2_REMOTE/" --backup-dir "$R2_REMOTE/old/" >/dev/null 2>&1 && \
     rclone copy "$OUT_ENC.sha256" "$R2_REMOTE/" >/dev/null 2>&1; then
    log "offsite: synced to $R2_REMOTE"
  else
    log "WARN: offsite sync failed (backup is still local)"
  fi
else
  log "offsite: skipped (R2_REMOTE/rclone not configured)"
fi

log "DONE"
