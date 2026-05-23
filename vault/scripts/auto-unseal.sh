#!/bin/sh
set -eu

export VAULT_ADDR="${VAULT_ADDR:-http://vault:18200}"
KEY_DIR="/vault/keys"
UNSEAL_KEY_FILE="$KEY_DIR/unseal-key.txt"
ROOT_TOKEN_FILE="$KEY_DIR/root-token.txt"
APP_TOKEN_FILE="$KEY_DIR/djopenkb-app-token.txt"
POLICY_FILE="/vault/config/djopenkb-policy.hcl"

log() { echo "[vault-auto-unseal] $*"; }

wait_for_vault() {
  i=0
  while true; do
    set +e
    vault status >/tmp/vault-status.txt 2>&1
    rc=$?
    set -e
    if [ "$rc" -eq 0 ] || [ "$rc" -eq 2 ]; then
      return 0
    fi
    i=$((i + 1))
    if [ "$i" -gt 120 ]; then
      log "Vault did not become reachable. Last status output:" >&2
      cat /tmp/vault-status.txt >&2 || true
      return 1
    fi
    sleep 2
  done
}

unseal_if_needed() {
  if [ ! -f "$UNSEAL_KEY_FILE" ]; then
    log "No unseal key file found yet. Waiting for vault-init first-time setup."
    return 0
  fi
  if vault status 2>/dev/null | grep -q "Sealed[[:space:]]*true"; then
    log "Vault is sealed. Unsealing automatically for local VM deployment ..."
    vault operator unseal "$(cat "$UNSEAL_KEY_FILE")" >/dev/null
  fi
}

ensure_app_token() {
  if [ ! -f "$ROOT_TOKEN_FILE" ]; then
    log "No root token file found yet. Waiting for vault-init first-time setup."
    return 0
  fi

  export VAULT_TOKEN="$(cat "$ROOT_TOKEN_FILE")"
  if [ -f "$POLICY_FILE" ]; then
    vault policy write djopenkb-app "$POLICY_FILE" >/dev/null || true
  fi

  if [ ! -s "$APP_TOKEN_FILE" ]; then
    log "App token missing. Creating a new read-only DjOpenKB app token ..."
    vault token create -policy=djopenkb-app -orphan -ttl=87600h -field=token > "$APP_TOKEN_FILE" \
      || vault token create -policy=djopenkb-app -orphan -field=token > "$APP_TOKEN_FILE"
    chmod 600 "$APP_TOKEN_FILE" || true
  fi
}

log "Starting automatic unseal/watch service."
while true; do
  if wait_for_vault; then
    unseal_if_needed || true
    if vault status 2>/dev/null | grep -q "Sealed[[:space:]]*false"; then
      ensure_app_token || true
    fi
  fi
  sleep "${VAULT_AUTO_UNSEAL_INTERVAL_SECONDS:-15}"
done
