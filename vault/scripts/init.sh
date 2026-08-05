#!/bin/sh
# INTERNAL ONE-SHOT VAULT INITIALISATION SCRIPT — do not run it directly.
#
# What it does:
#   Waits for Vault, performs first-time initialisation/unsealing when required,
#   enables KV v2, safely seeds or updates secret/djopenkb from the temporary
#   vault/bootstrap/djopenkb.env file, writes the app policy, and creates the
#   long-lived static read-only app token used by Django, Celery, and the scheduler.
#
# Normal server usage:
#   cd /opt/DjOpenKB
#   sudo docker compose up --build -d
#
# View the one-shot service result:
#   cd /opt/DjOpenKB
#   sudo docker compose logs --no-log-prefix vault-init
#
# First deployment/secret update only:
#   1. Prepare vault/bootstrap/djopenkb.env and protect it with chmod 600.
#   2. Run the normal Docker Compose command above.
#   3. Verify Vault and the application, then delete the temporary file.
#
# Never use "docker compose down -v" for a normal update because it can remove
# the persistent Vault and PostgreSQL volumes.

set -eu

export VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"
KEY_DIR="/vault/keys"
INIT_FILE="$KEY_DIR/vault-init.txt"
ROOT_TOKEN_FILE="$KEY_DIR/root-token.txt"
APP_TOKEN_FILE="$KEY_DIR/djopenkb-app-token.txt"
APP_TOKEN_ACCESSOR_FILE="$KEY_DIR/djopenkb-app-token-accessor.txt"
APP_TOKEN_STATIC_MARKER_FILE="$KEY_DIR/djopenkb-app-token-static.txt"
PENDING_REVOCATIONS_FILE="$KEY_DIR/pending-token-revocations.txt"
BOOTSTRAP_FILE="/vault/bootstrap/djopenkb.env"
POLICY_FILE="/vault/config/djopenkb-policy.hcl"
# This intentionally restores the original long-lived/static application-token
# model. Vault may cap the requested TTL according to its server configuration,
# but DjOpenKB will not renew or rotate a valid token automatically.
APP_TOKEN_TTL="87600h"

mkdir -p "$KEY_DIR"
chmod 700 "$KEY_DIR" || true

log() { echo "[vault-init] $*"; }

set_app_token_permissions() {
  chown 0:10001 "$APP_TOKEN_FILE" || true
  chmod 0440 "$APP_TOKEN_FILE" || true
  if [ -s "$APP_TOKEN_ACCESSOR_FILE" ]; then
    chown 0:0 "$APP_TOKEN_ACCESSOR_FILE" || true
    chmod 0600 "$APP_TOKEN_ACCESSOR_FILE" || true
  fi
  if [ -s "$APP_TOKEN_STATIC_MARKER_FILE" ]; then
    chown 0:0 "$APP_TOKEN_STATIC_MARKER_FILE" || true
    chmod 0600 "$APP_TOKEN_STATIC_MARKER_FILE" || true
  fi
  if [ -s "$PENDING_REVOCATIONS_FILE" ]; then
    chown 0:0 "$PENDING_REVOCATIONS_FILE" || true
    chmod 0600 "$PENDING_REVOCATIONS_FILE" || true
  fi
}

queue_accessor_for_revocation() {
  accessor="$1"
  [ -n "$accessor" ] || return 0
  touch "$PENDING_REVOCATIONS_FILE"
  chmod 0600 "$PENDING_REVOCATIONS_FILE" || true
  if ! grep -Fqx "$accessor" "$PENDING_REVOCATIONS_FILE" 2>/dev/null; then
    printf '%s\n' "$accessor" >> "$PENDING_REVOCATIONS_FILE"
  fi
}

process_pending_revocations() {
  [ -s "$PENDING_REVOCATIONS_FILE" ] || return 0

  pending_tmp="$PENDING_REVOCATIONS_FILE.tmp.$$"
  current_accessor="$(cat "$APP_TOKEN_ACCESSOR_FILE" 2>/dev/null || true)"
  : > "$pending_tmp"
  while IFS= read -r accessor; do
    [ -n "$accessor" ] || continue
    if [ -n "$current_accessor" ] && [ "$accessor" = "$current_accessor" ]; then
      log "WARNING: Refusing to revoke the accessor belonging to the current static application token." >&2
      continue
    fi
    if vault token revoke -accessor "$accessor" >/dev/null 2>&1; then
      log "Revoked a previously replaced application-token accessor."
    else
      printf '%s\n' "$accessor" >> "$pending_tmp"
      log "WARNING: Could not revoke one previous token accessor; it remains queued for a later vault-init run." >&2
    fi
  done < "$PENDING_REVOCATIONS_FILE"

  if [ -s "$pending_tmp" ]; then
    cat "$pending_tmp" > "$PENDING_REVOCATIONS_FILE"
    chmod 0600 "$PENDING_REVOCATIONS_FILE" || true
  else
    rm -f "$PENDING_REVOCATIONS_FILE"
  fi
  rm -f "$pending_tmp"
}

app_token_is_valid() {
  [ -s "$APP_TOKEN_FILE" ] || return 1
  token="$(cat "$APP_TOKEN_FILE")"
  [ -n "$token" ] || return 1
  vault token lookup "$token" >/dev/null 2>&1
}

record_current_accessor() {
  token="$1"
  accessor="$(vault token lookup -field=accessor "$token" 2>/dev/null || true)"
  if [ -n "$accessor" ]; then
    printf '%s\n' "$accessor" > "$APP_TOKEN_ACCESSOR_FILE"
  fi
}

write_static_app_token() {
  token="$1"
  accessor="$2"

  # Preserve the existing file inode because application containers use a
  # single-file bind mount. Replacing the inode after containers are created can
  # leave them reading an older mounted file.
  printf '%s\n' "$token" > "$APP_TOKEN_FILE"
  printf '%s\n' "$accessor" > "$APP_TOKEN_ACCESSOR_FILE"
  printf '%s\n' 'static-v1' > "$APP_TOKEN_STATIC_MARKER_FILE"
  set_app_token_permissions
}

create_static_app_token() {
  old_token=""
  old_accessor=""
  old_token_valid=false

  if app_token_is_valid; then
    old_token_valid=true
    old_token="$(cat "$APP_TOKEN_FILE")"
    old_accessor="$(vault token lookup -field=accessor "$old_token" 2>/dev/null || true)"
  fi

  token_tmp="$KEY_DIR/.djopenkb-app-token.new.$$"
  rm -f "$token_tmp"
  log "Creating one long-lived static read-only application token ..."
  if ! vault token create -policy=djopenkb-app -orphan -ttl="$APP_TOKEN_TTL" -field=token > "$token_tmp"; then
    log "WARNING: Vault rejected the requested long TTL; retrying with Vault's configured default token TTL." >&2
    if ! vault token create -policy=djopenkb-app -orphan -field=token > "$token_tmp"; then
      rm -f "$token_tmp"
      if [ "$old_token_valid" = true ]; then
        log "WARNING: Static-token replacement failed, so the existing valid application token was preserved." >&2
        set_app_token_permissions
        return 0
      fi
      log "ERROR: Vault could not create an application token and no valid existing token is available." >&2
      return 1
    fi
  fi

  new_token="$(cat "$token_tmp")"
  rm -f "$token_tmp"
  new_accessor="$(vault token lookup -field=accessor "$new_token" 2>/dev/null || true)"
  if [ -z "$new_token" ] || [ -z "$new_accessor" ]; then
    [ -n "$new_token" ] && vault token revoke "$new_token" >/dev/null 2>&1 || true
    if [ "$old_token_valid" = true ]; then
      log "WARNING: Vault did not return complete metadata for the replacement token; preserving the existing valid token." >&2
      set_app_token_permissions
      return 0
    fi
    log "ERROR: Vault did not return a usable application token and accessor." >&2
    return 1
  fi

  if [ -n "$old_accessor" ] && [ "$old_accessor" != "$new_accessor" ]; then
    queue_accessor_for_revocation "$old_accessor"
  fi

  write_static_app_token "$new_token" "$new_accessor"
  log "Static application token stored. Future vault-init runs will reuse it while it remains valid."
  process_pending_revocations || true
}

ensure_static_app_token() {
  if app_token_is_valid && [ "$(cat "$APP_TOKEN_STATIC_MARKER_FILE" 2>/dev/null || true)" = 'static-v1' ]; then
    current_token="$(cat "$APP_TOKEN_FILE")"
    record_current_accessor "$current_token"
    set_app_token_permissions
    log "Existing static application token is valid; reusing it without renewal or rotation."
    process_pending_revocations || true
    return 0
  fi

  if app_token_is_valid; then
    log "Existing application token predates static-token mode; replacing it once with a static token."
  else
    log "Application token is missing or invalid; creating a static token."
  fi
  create_static_app_token
}

log "Waiting for Vault server at $VAULT_ADDR ..."
i=0
while true; do
  set +e
  vault status >/tmp/vault-status.txt 2>&1
  rc=$?
  set -e
  if [ "$rc" -eq 0 ] || [ "$rc" -eq 2 ]; then
    break
  fi
  i=$((i + 1))
  if [ "$i" -gt 90 ]; then
    log "ERROR: Vault did not become reachable. Last status output:" >&2
    cat /tmp/vault-status.txt >&2 || true
    exit 1
  fi
  sleep 2
done

STATUS_OUT="$(vault status 2>/dev/null || true)"
if echo "$STATUS_OUT" | grep -q "Initialized[[:space:]]*false"; then
  log "Initializing Vault with 1 unseal key for local VM deployment ..."
  vault operator init -key-shares=1 -key-threshold=1 > "$INIT_FILE"
  chmod 600 "$INIT_FILE" || true
  awk '/Unseal Key 1:/ {print $4}' "$INIT_FILE" > "$KEY_DIR/unseal-key.txt"
  awk '/Initial Root Token:/ {print $4}' "$INIT_FILE" > "$ROOT_TOKEN_FILE"
  chmod 600 "$KEY_DIR/unseal-key.txt" "$ROOT_TOKEN_FILE" || true
fi

UNSEAL_KEY="$(cat "$KEY_DIR/unseal-key.txt")"
ROOT_TOKEN="$(cat "$ROOT_TOKEN_FILE")"

if vault status 2>/dev/null | grep -q "Sealed[[:space:]]*true"; then
  log "Unsealing Vault ..."
  vault operator unseal "$UNSEAL_KEY" >/dev/null
fi

export VAULT_TOKEN="$ROOT_TOKEN"

if ! vault secrets list -format=json 2>/dev/null | grep -q '"secret/"'; then
  log "Enabling KV v2 at secret/ ..."
  vault secrets enable -path=secret kv-v2
fi

if [ -f "$BOOTSTRAP_FILE" ]; then
  log "Seeding secret/djopenkb from $BOOTSTRAP_FILE ..."

  # Preserve existing values when an update bootstrap file leaves a field blank.
  # This lets an operator add/rotate SMTP credentials without having to copy
  # unrelated production secrets back out of Vault. Blank bootstrap fields are
  # therefore intentionally not a mechanism for deleting a stored secret.
  existing_secret() {
    vault kv get -field="$1" secret/djopenkb 2>/dev/null || true
  }

  EXISTING_DJANGO_SECRET_KEY="$(existing_secret DJANGO_SECRET_KEY)"
  EXISTING_DJANGO_FIELD_ENCRYPTION_KEY="$(existing_secret DJANGO_FIELD_ENCRYPTION_KEY)"
  EXISTING_POSTGRES_PASSWORD="$(existing_secret POSTGRES_PASSWORD)"
  EXISTING_AI_API_KEY="$(existing_secret AI_API_KEY)"
  EXISTING_GEMINI_API_KEY="$(existing_secret GEMINI_API_KEY)"
  EXISTING_OPENAI_API_KEY="$(existing_secret OPENAI_API_KEY)"
  EXISTING_ANTHROPIC_API_KEY="$(existing_secret ANTHROPIC_API_KEY)"
  EXISTING_LDAP_BIND_DN="$(existing_secret LDAP_BIND_DN)"
  EXISTING_LDAP_BIND_PASSWORD="$(existing_secret LDAP_BIND_PASSWORD)"
  EXISTING_LDAP_PLACEHOLDER_PASSWORD="$(existing_secret LDAP_PLACEHOLDER_PASSWORD)"
  EXISTING_SMTP_RELAY_USERNAME="$(existing_secret SMTP_RELAY_USERNAME)"
  EXISTING_SMTP_RELAY_PASSWORD="$(existing_secret SMTP_RELAY_PASSWORD)"
  EXISTING_SMTP_FROM_EMAIL="$(existing_secret SMTP_FROM_EMAIL)"

  # shellcheck disable=SC1090
  . "$BOOTSTRAP_FILE"

  DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-$EXISTING_DJANGO_SECRET_KEY}"
  DJANGO_FIELD_ENCRYPTION_KEY="${DJANGO_FIELD_ENCRYPTION_KEY:-$EXISTING_DJANGO_FIELD_ENCRYPTION_KEY}"
  POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$EXISTING_POSTGRES_PASSWORD}"
  AI_API_KEY="${AI_API_KEY:-$EXISTING_AI_API_KEY}"
  GEMINI_API_KEY="${GEMINI_API_KEY:-$EXISTING_GEMINI_API_KEY}"
  OPENAI_API_KEY="${OPENAI_API_KEY:-$EXISTING_OPENAI_API_KEY}"
  ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-$EXISTING_ANTHROPIC_API_KEY}"
  LDAP_BIND_DN="${LDAP_BIND_DN:-$EXISTING_LDAP_BIND_DN}"
  LDAP_BIND_PASSWORD="${LDAP_BIND_PASSWORD:-$EXISTING_LDAP_BIND_PASSWORD}"
  LDAP_PLACEHOLDER_PASSWORD="${LDAP_PLACEHOLDER_PASSWORD:-$EXISTING_LDAP_PLACEHOLDER_PASSWORD}"
  SMTP_RELAY_USERNAME="${SMTP_RELAY_USERNAME:-$EXISTING_SMTP_RELAY_USERNAME}"
  SMTP_FROM_EMAIL="${SMTP_FROM_EMAIL:-$EXISTING_SMTP_FROM_EMAIL}"

  # A temporary bootstrap file can safely request the SMTP password to be copied
  # from the already stored LDAP bind secret. This avoids writing a reused
  # service-account password into another plaintext bootstrap file.
  case "${SMTP_RELAY_PASSWORD_USE_LDAP_BIND_PASSWORD:-false}" in
    true)
      SMTP_RELAY_PASSWORD="${LDAP_BIND_PASSWORD:-}"
      ;;
    false|"")
      SMTP_RELAY_PASSWORD="${SMTP_RELAY_PASSWORD:-$EXISTING_SMTP_RELAY_PASSWORD}"
      ;;
    *)
      log "ERROR: SMTP_RELAY_PASSWORD_USE_LDAP_BIND_PASSWORD must be true or false." >&2
      exit 1
      ;;
  esac

  if [ -z "${DJANGO_SECRET_KEY:-}" ] || [ -z "${POSTGRES_PASSWORD:-}" ]; then
    log "ERROR: DJANGO_SECRET_KEY and POSTGRES_PASSWORD must be set for first-time Vault seeding." >&2
    exit 1
  fi

  vault kv put secret/djopenkb \
    DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-}" \
    DJANGO_FIELD_ENCRYPTION_KEY="${DJANGO_FIELD_ENCRYPTION_KEY:-}" \
    POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}" \
    AI_API_KEY="${AI_API_KEY:-}" \
    GEMINI_API_KEY="${GEMINI_API_KEY:-}" \
    OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
    ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
    LDAP_BIND_DN="${LDAP_BIND_DN:-}" \
    LDAP_BIND_PASSWORD="${LDAP_BIND_PASSWORD:-}" \
    LDAP_PLACEHOLDER_PASSWORD="${LDAP_PLACEHOLDER_PASSWORD:-}" \
    SMTP_RELAY_USERNAME="${SMTP_RELAY_USERNAME:-}" \
    SMTP_RELAY_PASSWORD="${SMTP_RELAY_PASSWORD:-}" \
    SMTP_FROM_EMAIL="${SMTP_FROM_EMAIL:-}" >/dev/null
  log "Secret seeded. You may now remove vault/bootstrap/djopenkb.env."
elif ! vault kv get secret/djopenkb >/dev/null 2>&1; then
  log "ERROR: secret/djopenkb does not exist and $BOOTSTRAP_FILE was not provided." >&2
  log "Copy vault/bootstrap/djopenkb.env.example to vault/bootstrap/djopenkb.env and fill it once." >&2
  exit 1
else
  log "Existing secret/djopenkb found. No bootstrap file needed."
fi

vault policy write djopenkb-app "$POLICY_FILE" >/dev/null
ensure_static_app_token

log "Vault is ready for DjOpenKB."
