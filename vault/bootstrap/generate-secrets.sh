#!/usr/bin/env sh
# Generate secure local bootstrap secrets for DjOpenKB.
#
# Creates or updates vault/bootstrap/djopenkb.env with:
# - DJANGO_SECRET_KEY
# - POSTGRES_PASSWORD
#
# It preserves existing non-generated lines such as GEMINI_API_KEY or LDAP_BIND_PASSWORD.
# Run from the project root:
#     sh vault/bootstrap/generate-secrets.sh

set -eu

OUTPUT_FILE="${OUTPUT_FILE:-vault/bootstrap/djopenkb.env}"
DJANGO_KEY_LENGTH="${DJANGO_KEY_LENGTH:-64}"
POSTGRES_PASSWORD_LENGTH="${POSTGRES_PASSWORD_LENGTH:-32}"

make_secret() {
    length="$1"
    alphabet="$2"
    python - "$length" "$alphabet" <<'PY'
import secrets
import sys
length = int(sys.argv[1])
alphabet = sys.argv[2]
print(''.join(secrets.choice(alphabet) for _ in range(length)))
PY
}

set_or_append_env_line() {
    file="$1"
    key="$2"
    value="$3"

    if [ -f "$file" ] && grep -q "^[[:space:]]*$key[[:space:]]*=" "$file"; then
        python - "$file" "$key" "$value" <<'PY'
from pathlib import Path
import re
import sys
path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
pattern = re.compile(rf'^\s*{re.escape(key)}\s*=')
lines = path.read_text(encoding='utf-8').splitlines()
new_lines = [f'{key}="{value}"' if pattern.match(line) else line for line in lines]
path.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')
PY
    else
        printf '%s="%s"\n' "$key" "$value" >> "$file"
    fi
}

mkdir -p "$(dirname "$OUTPUT_FILE")"

if [ ! -f "$OUTPUT_FILE" ]; then
    cat > "$OUTPUT_FILE" <<'HEADER'
# ---------------------------------------------------------------------
# DjOpenKB Vault bootstrap secrets
# Generated locally. Do not commit or share this file.
# After Vault is seeded and login works, delete this file from exported copies.
# ---------------------------------------------------------------------

# Required Django/PostgreSQL secrets
HEADER
fi

DJANGO_ALPHABET='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*(-_=+)'
# Keep PostgreSQL password shell/env friendly: no spaces, quotes, backslashes, or dollar signs.
POSTGRES_ALPHABET='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-@#%+=:.'

DJANGO_SECRET_KEY="$(make_secret "$DJANGO_KEY_LENGTH" "$DJANGO_ALPHABET")"
POSTGRES_PASSWORD="$(make_secret "$POSTGRES_PASSWORD_LENGTH" "$POSTGRES_ALPHABET")"

set_or_append_env_line "$OUTPUT_FILE" "DJANGO_SECRET_KEY" "$DJANGO_SECRET_KEY"
set_or_append_env_line "$OUTPUT_FILE" "POSTGRES_PASSWORD" "$POSTGRES_PASSWORD"

printf 'Generated secure secrets in: %s\n' "$OUTPUT_FILE"
printf 'Updated: DJANGO_SECRET_KEY and POSTGRES_PASSWORD\n'
printf 'Keep this file private. Do not commit or submit it.\n'
printf 'If Vault was already seeded, update Vault or reseed it so the new values are used.\n'
