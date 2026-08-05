#!/bin/sh
# INTERNAL VAULT SERVICE SCRIPT — do not run this file directly on the host.
#
# Watches the local Vault service, unseals it, validates the read-only
# application token, renews it before expiry, and replaces/revokes it when it is
# missing or invalid. The token accessor is stored separately for safe
# revocation without retaining another usable secret.

set -eu

export VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"
KEY_DIR="/vault/keys"
UNSEAL_KEY_FILE="$KEY_DIR/unseal-key.txt"
ROOT_TOKEN_FILE="$KEY_DIR/root-token.txt"
APP_TOKEN_FILE="$KEY_DIR/djopenkb-app-token.txt"
APP_TOKEN_ACCESSOR_FILE="$KEY_DIR/djopenkb-app-token-accessor.txt"
POLICY_FILE="/vault/config/djopenkb-policy.hcl"
APP_TOKEN_PERIOD="${VAULT_APP_TOKEN_PERIOD:-24h}"
APP_TOKEN_RENEW_BEFORE_SECONDS="${VAULT_APP_TOKEN_RENEW_BEFORE_SECONDS:-43200}"

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

write_app_token_files() {
  token="$1"
  accessor="$2"
  printf '%s\n' "$token" > "$APP_TOKEN_FILE"
  printf '%s\n' "$accessor" > "$APP_TOKEN_ACCESSOR_FILE"
  # The application services need the token, but only Vault maintenance
  # services need the accessor used for revocation.
  chown 0:10001 "$APP_TOKEN_FILE" || true
  chmod 0440 "$APP_TOKEN_FILE" || true
  chown 0:0 "$APP_TOKEN_ACCESSOR_FILE" || true
  chmod 0600 "$APP_TOKEN_ACCESSOR_FILE" || true
}

issue_app_token() {
  old_token=""
  old_accessor=""
  [ -s "$APP_TOKEN_FILE" ] && old_token="$(cat "$APP_TOKEN_FILE")"
  [ -s "$APP_TOKEN_ACCESSOR_FILE" ] && old_accessor="$(cat "$APP_TOKEN_ACCESSOR_FILE")"

  new_token="$(
    vault token create -policy=djopenkb-app -orphan -period="$APP_TOKEN_PERIOD" -field=token 2>/dev/null \
      || vault token create -policy=djopenkb-app -orphan -ttl="$APP_TOKEN_PERIOD" -renewable=true -field=token
  )"
  new_accessor="$(vault token lookup -field=accessor "$new_token")"
  if [ -z "$new_token" ] || [ -z "$new_accessor" ]; then
    [ -n "$new_token" ] && vault token revoke "$new_token" >/dev/null 2>&1 || true
    return 1
  fi

  write_app_token_files "$new_token" "$new_accessor"
  if [ -n "$old_accessor" ] && [ "$old_accessor" != "$new_accessor" ]; then
    vault token revoke -accessor "$old_accessor" >/dev/null 2>&1 || true
  elif [ -n "$old_token" ] && [ "$old_token" != "$new_token" ]; then
    vault token revoke "$old_token" >/dev/null 2>&1 || true
  fi
  log "Issued a replacement read-only application token and revoked the previous credential."
}

ensure_app_token() {
  if [ ! -s "$ROOT_TOKEN_FILE" ]; then
    log "No root token file found yet. Waiting for vault-init first-time setup."
    return 0
  fi

  export VAULT_TOKEN="$(cat "$ROOT_TOKEN_FILE")"
  [ -f "$POLICY_FILE" ] && vault policy write djopenkb-app "$POLICY_FILE" >/dev/null || true

  if [ ! -s "$APP_TOKEN_FILE" ]; then
    log "Application token is missing; issuing a replacement."
    issue_app_token
    return
  fi

  current_token="$(cat "$APP_TOKEN_FILE")"
  if ! vault token lookup "$current_token" >/dev/null 2>&1; then
    log "Application token is invalid or expired; issuing a replacement."
    issue_app_token
    return
  fi

  current_accessor="$(vault token lookup -field=accessor "$current_token" 2>/dev/null || true)"
  if [ -n "$current_accessor" ]; then
    stored_accessor=""
    [ -s "$APP_TOKEN_ACCESSOR_FILE" ] && stored_accessor="$(cat "$APP_TOKEN_ACCESSOR_FILE")"
    if [ "$stored_accessor" != "$current_accessor" ]; then
      printf '%s\n' "$current_accessor" > "$APP_TOKEN_ACCESSOR_FILE"
      chown 0:0 "$APP_TOKEN_ACCESSOR_FILE" || true
      chmod 0600 "$APP_TOKEN_ACCESSOR_FILE" || true
    fi
  fi

  ttl="$(vault token lookup -field=ttl "$current_token" 2>/dev/null || echo 0)"
  case "$ttl" in
    ''|*[!0-9]*) ttl=0 ;;
  esac
  case "$APP_TOKEN_RENEW_BEFORE_SECONDS" in
    ''|*[!0-9]*) APP_TOKEN_RENEW_BEFORE_SECONDS=43200 ;;
  esac

  if [ "$ttl" -le "$APP_TOKEN_RENEW_BEFORE_SECONDS" ]; then
    if vault token renew "$current_token" >/dev/null 2>&1; then
      log "Renewed the read-only application token before expiry."
    else
      log "Token renewal failed; issuing a replacement."
      issue_app_token
    fi
  fi
}

log "Starting automatic unseal/token watch service."
while true; do
  if wait_for_vault; then
    unseal_if_needed || true
    if vault status 2>/dev/null | grep -q "Sealed[[:space:]]*false"; then
      ensure_app_token || true
    fi
  fi
  sleep "${VAULT_AUTO_UNSEAL_INTERVAL_SECONDS:-15}"
done
