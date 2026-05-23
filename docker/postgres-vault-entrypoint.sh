#!/bin/sh
set -eu

log() { echo "[postgres-vault] $*"; }

if [ "${VAULT_ENABLED:-false}" = "true" ]; then
  VAULT_ADDR="${VAULT_ADDR:-http://vault:18200}"
  VAULT_KV_MOUNT="${VAULT_KV_MOUNT:-secret}"
  VAULT_SECRET_PATH="${VAULT_SECRET_PATH:-djopenkb}"
  VAULT_TOKEN="${VAULT_TOKEN:-}"

  if [ -z "$VAULT_TOKEN" ] && [ -n "${VAULT_TOKEN_FILE:-}" ] && [ -f "$VAULT_TOKEN_FILE" ]; then
    VAULT_TOKEN="$(cat "$VAULT_TOKEN_FILE")"
  fi

  if [ -z "$VAULT_TOKEN" ]; then
    log "ERROR: VAULT_ENABLED=true but no VAULT_TOKEN or VAULT_TOKEN_FILE was provided." >&2
    exit 1
  fi

  log "Reading POSTGRES_PASSWORD from Vault at ${VAULT_KV_MOUNT}/${VAULT_SECRET_PATH} ..."
  i=0
  while true; do
    set +e
    json="$(curl -fsS -H "X-Vault-Token: $VAULT_TOKEN" \
      "$VAULT_ADDR/v1/$VAULT_KV_MOUNT/data/$VAULT_SECRET_PATH" 2>/tmp/postgres-vault-error.txt)"
    rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then
      break
    fi
    i=$((i + 1))
    if [ "$i" -gt 60 ]; then
      log "ERROR: Unable to read Vault secret after waiting." >&2
      cat /tmp/postgres-vault-error.txt >&2 || true
      exit 1
    fi
    sleep 2
  done

  POSTGRES_PASSWORD="$(printf '%s' "$json" | jq -r '.data.data.POSTGRES_PASSWORD // empty')"
  if [ -z "$POSTGRES_PASSWORD" ]; then
    log "ERROR: POSTGRES_PASSWORD is missing in Vault secret." >&2
    exit 1
  fi
  export POSTGRES_PASSWORD
fi

exec docker-entrypoint.sh "$@"
