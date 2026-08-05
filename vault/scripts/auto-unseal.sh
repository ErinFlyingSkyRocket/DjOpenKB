#!/bin/sh
# INTERNAL VAULT SERVICE SCRIPT — do not run this file directly on the host.
#
# Watches the local Vault service and automatically unseals it with the
# protected local key file. Application-token creation is intentionally handled
# only by the one-shot vault-init service. This watcher does not renew or rotate
# the static DjOpenKB application token.

set -eu

export VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"
KEY_DIR="/vault/keys"
UNSEAL_KEY_FILE="$KEY_DIR/unseal-key.txt"

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
  if [ ! -s "$UNSEAL_KEY_FILE" ]; then
    log "No unseal key file found yet. Waiting for vault-init first-time setup."
    return 0
  fi
  if vault status 2>/dev/null | grep -q "Sealed[[:space:]]*true"; then
    log "Vault is sealed. Unsealing automatically for local VM deployment ..."
    vault operator unseal "$(cat "$UNSEAL_KEY_FILE")" >/dev/null
  fi
}

log "Starting automatic unseal-only watch service."
while true; do
  if wait_for_vault; then
    unseal_if_needed || true
  fi
  sleep "${VAULT_AUTO_UNSEAL_INTERVAL_SECONDS:-15}"
done
