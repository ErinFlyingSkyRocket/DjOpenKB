#!/usr/bin/env sh
# Generate secure local bootstrap secrets for DjOpenKB.
#
# Creates or updates vault/bootstrap/djopenkb.env with:
# - DJANGO_SECRET_KEY
# - POSTGRES_PASSWORD
# - DJANGO_FIELD_ENCRYPTION_KEY
# - LDAP_PLACEHOLDER_PASSWORD
#
# It preserves existing comments and manual lines such as:
# - AI_API_KEY / provider-specific AI keys
# - LDAP_BIND_PASSWORD
#
# IMPORTANT:
# The current Vault bootstrap file is read as a shell-style env file.
# To avoid Linux shell parsing errors, this script generates alphanumeric-only
# values and keeps generated values unquoted. Manual passwords can be single
# quoted if they contain shell special characters.
#
# Run from the project root:
#     chmod +x vault/bootstrap/generate-secrets.sh
#     ./vault/bootstrap/generate-secrets.sh

set -eu

OUTPUT_FILE="${OUTPUT_FILE:-vault/bootstrap/djopenkb.env}"
EXAMPLE_FILE="${EXAMPLE_FILE:-vault/bootstrap/djopenkb.env.example}"
DJANGO_KEY_LENGTH="${DJANGO_KEY_LENGTH:-72}"
POSTGRES_PASSWORD_LENGTH="${POSTGRES_PASSWORD_LENGTH:-40}"
FIELD_ENCRYPTION_KEY_LENGTH="${FIELD_ENCRYPTION_KEY_LENGTH:-72}"
PLACEHOLDER_PASSWORD_LENGTH="${PLACEHOLDER_PASSWORD_LENGTH:-40}"

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "ERROR: python3/python not found. Install python3 or python-is-python3 first." >&2
    exit 1
fi

mkdir -p "$(dirname "$OUTPUT_FILE")"

if [ ! -f "$OUTPUT_FILE" ]; then
    if [ -f "$EXAMPLE_FILE" ]; then
        cp "$EXAMPLE_FILE" "$OUTPUT_FILE"
    else
        cat > "$OUTPUT_FILE" <<'HEADER'
# ---------------------------------------------------------------------
# DjOpenKB Vault bootstrap secrets
# ---------------------------------------------------------------------
# Generated locally. Do not commit or share this file.
# After Vault is seeded and login works, delete this file from exported copies.
#
# Do not put spaces around "=".
# Generated values are alphanumeric-only and should stay unquoted.
# Manual passwords can use single quotes if they contain shell symbols.

DJANGO_SECRET_KEY=replace-with-a-long-random-django-secret-key
DJANGO_FIELD_ENCRYPTION_KEY=replace-with-a-long-random-field-encryption-key
POSTGRES_PASSWORD=replace-with-stable-postgres-password

AI_API_KEY=replace-with-selected-ai-provider-api-key
GEMINI_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
LDAP_BIND_PASSWORD=replace-with-real-svc-djopenkb-password
LDAP_PLACEHOLDER_PASSWORD=replace-with-placeholder-password-or-leave-random
HEADER
    fi
fi

"$PYTHON_BIN" - "$OUTPUT_FILE" "$DJANGO_KEY_LENGTH" "$POSTGRES_PASSWORD_LENGTH" "$FIELD_ENCRYPTION_KEY_LENGTH" "$PLACEHOLDER_PASSWORD_LENGTH" <<'PY'
import secrets
import string
import sys
from pathlib import Path

path = Path(sys.argv[1])
django_len = int(sys.argv[2])
postgres_len = int(sys.argv[3])
field_key_len = int(sys.argv[4])
placeholder_len = int(sys.argv[5])

# Alphanumeric-only generated values.
# This avoids Linux shell/env parsing problems while still being strong
# because the values are long and generated with secrets.
alphabet = string.ascii_letters + string.digits

def make_secret(length: int) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(length))

replacements = {
    "DJANGO_SECRET_KEY": make_secret(django_len),
    "POSTGRES_PASSWORD": make_secret(postgres_len),
    "DJANGO_FIELD_ENCRYPTION_KEY": make_secret(field_key_len),
    "LDAP_PLACEHOLDER_PASSWORD": make_secret(placeholder_len),
}

text = path.read_text(encoding="utf-8") if path.exists() else ""
lines = text.splitlines()
found = {key: False for key in replacements}
out = []

for line in lines:
    stripped = line.strip()
    replaced = False

    for key, value in replacements.items():
        if stripped.startswith(key + "="):
            out.append(f"{key}={value}")
            found[key] = True
            replaced = True
            break

    if not replaced:
        out.append(line)

if not found["LDAP_PLACEHOLDER_PASSWORD"]:
    if out and out[-1].strip():
        out.append("")
    out.append("# Only used if LDAP_PLACEHOLDER_ENABLED=true.")
    out.append(f"LDAP_PLACEHOLDER_PASSWORD={replacements['LDAP_PLACEHOLDER_PASSWORD']}")

# Keep provider-specific AI key placeholders available. Do not generate or
# overwrite API keys because those are manually issued by the provider.
for key in ("AI_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LDAP_BIND_PASSWORD"):
    if not any(line.strip().startswith(key + "=") for line in out):
        if out and out[-1].strip():
            out.append("")
        if key == "AI_API_KEY":
            out.append("# General fallback AI provider key.")
        elif key in {"GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}:
            out.append("# Optional provider-specific AI key.")
        elif key == "LDAP_BIND_PASSWORD":
            out.append("# AD LDAP service account password.")
        out.append(f"{key}=")

path.write_text("\n".join(out) + "\n", encoding="utf-8")

print(f"Generated bootstrap secrets in: {path}")
print("Updated: DJANGO_SECRET_KEY, DJANGO_FIELD_ENCRYPTION_KEY, POSTGRES_PASSWORD, LDAP_PLACEHOLDER_PASSWORD")
print("Preserved: comments, AI_API_KEY/GEMINI_API_KEY/OPENAI_API_KEY/ANTHROPIC_API_KEY, LDAP_BIND_PASSWORD")
print()
print("Next: edit the correct AI provider key and LDAP_BIND_PASSWORD manually.")
print("Use no spaces around '='. Generated values are unquoted.")
print("Good simple example: LDAP_BIND_PASSWORD=P@ssw0rd")
print("If needed, use single quotes: LDAP_BIND_PASSWORD='P@ssw0rd!abc$123'")
print("Avoid double quotes for values containing $, `, or \\ .")
PY
