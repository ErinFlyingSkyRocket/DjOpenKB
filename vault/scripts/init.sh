#!/bin/sh
set -eu

export VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"
KEY_DIR="/vault/keys"
INIT_FILE="$KEY_DIR/vault-init.txt"
ROOT_TOKEN_FILE="$KEY_DIR/root-token.txt"
APP_TOKEN_FILE="$KEY_DIR/djopenkb-app-token.txt"
BOOTSTRAP_FILE="/vault/bootstrap/djopenkb.env"
POLICY_FILE="/vault/config/djopenkb-policy.hcl"

mkdir -p "$KEY_DIR"
chmod 700 "$KEY_DIR" || true

log() { echo "[vault-init] $*"; }

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
  # shellcheck disable=SC1090
  . "$BOOTSTRAP_FILE"

  if [ -z "${DJANGO_SECRET_KEY:-}" ] || [ -z "${POSTGRES_PASSWORD:-}" ]; then
    log "ERROR: DJANGO_SECRET_KEY and POSTGRES_PASSWORD must be set in $BOOTSTRAP_FILE." >&2
    exit 1
  fi

  vault kv put secret/djopenkb \
    DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-}" \
    DJANGO_FIELD_ENCRYPTION_KEY="${DJANGO_FIELD_ENCRYPTION_KEY:-}" \
    POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}" \
    AI_API_KEY="${AI_API_KEY:-}" \
    LDAP_BIND_PASSWORD="${LDAP_BIND_PASSWORD:-}" \
    LDAP_PLACEHOLDER_PASSWORD="${LDAP_PLACEHOLDER_PASSWORD:-}" >/dev/null
  log "Secret seeded. You may now remove vault/bootstrap/djopenkb.env."
elif ! vault kv get secret/djopenkb >/dev/null 2>&1; then
  log "ERROR: secret/djopenkb does not exist and $BOOTSTRAP_FILE was not provided." >&2
  log "Copy vault/bootstrap/djopenkb.env.example to vault/bootstrap/djopenkb.env and fill it once." >&2
  exit 1
else
  log "Existing secret/djopenkb found. No bootstrap file needed."
fi

vault policy write djopenkb-app "$POLICY_FILE" >/dev/null
vault token create -policy=djopenkb-app -orphan -ttl=87600h -field=token > "$APP_TOKEN_FILE" \
  || vault token create -policy=djopenkb-app -orphan -field=token > "$APP_TOKEN_FILE"
# The Django, Celery, and scheduler containers run as UID/GID 10001.
# Keep the token unreadable to unrelated host users, but permit that app group
# to read the single bind-mounted token file.
chown 0:10001 "$APP_TOKEN_FILE" || true
chmod 0440 "$APP_TOKEN_FILE" || true

log "Vault is ready for DjOpenKB."
