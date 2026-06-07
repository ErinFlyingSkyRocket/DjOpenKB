#!/usr/bin/env sh
# Generate secure local bootstrap secrets for DjOpenKB.
#
# Creates or updates vault/bootstrap/djopenkb.env with:
# - DJANGO_SECRET_KEY
# - POSTGRES_PASSWORD
# - LDAP_PLACEHOLDER_PASSWORD
#
# It preserves existing comments and manual lines such as:
# - AI_API_KEY
# - LDAP_BIND_PASSWORD
#
# IMPORTANT:
# The current Vault bootstrap file is read as a shell-style env file.
# To avoid Linux shell parsing errors, this script generates alphanumeric-only
# values and the examples use no quotes.
#
# Run from the project root:
#     chmod +x vault/bootstrap/generate-secrets.sh
#     ./vault/bootstrap/generate-secrets.sh

set -eu

OUTPUT_FILE="${OUTPUT_FILE:-vault/bootstrap/djopenkb.env}"
EXAMPLE_FILE="${EXAMPLE_FILE:-vault/bootstrap/djopenkb.env.example}"
DJANGO_KEY_LENGTH="${DJANGO_KEY_LENGTH:-72}"
POSTGRES_PASSWORD_LENGTH="${POSTGRES_PASSWORD_LENGTH:-40}"
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
# Use no quotes.
# Do not put spaces around "=".
# Avoid spaces and shell special characters in values.

DJANGO_SECRET_KEY=replace-with-a-long-random-django-secret-key
POSTGRES_PASSWORD=replace-with-stable-postgres-password

AI_API_KEY=replace-with-selected-ai-provider-api-key
LDAP_BIND_PASSWORD=replace-with-real-svc-djopenkb-password
LDAP_PLACEHOLDER_PASSWORD=replace-with-placeholder-password-or-leave-random
HEADER
    fi
fi

"$PYTHON_BIN" - "$OUTPUT_FILE" "$DJANGO_KEY_LENGTH" "$POSTGRES_PASSWORD_LENGTH" "$PLACEHOLDER_PASSWORD_LENGTH" <<'PYCODE'
import secrets
import string
import sys
from pathlib import Path

path = Path(sys.argv[1])
django_len = int(sys.argv[2])
postgres_len = int(sys.argv[3])
placeholder_len = int(sys.argv[4])

alphabet = string.ascii_letters + string.digits

def make_secret(length: int) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(length))

replacements = {
    "DJANGO_SECRET_KEY": make_secret(django_len),
    "POSTGRES_PASSWORD": make_secret(postgres_len),
    "LDAP_PLACEHOLDER_PASSWORD": make_secret(placeholder_len),
}

legacy_key_names = {"GEMINI_API_KEY", "LLM_API_KEY"}
legacy_value = ""

text = path.read_text(encoding="utf-8") if path.exists() else ""
lines = text.splitlines()
found = {key: False for key in replacements}
found_ai_key = False
out = []

for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        out.append(line)
        continue

    key, sep, value = stripped.partition("=")
    if sep and key in legacy_key_names:
        candidate = value.strip()
        if candidate and "replace-with" not in candidate.lower() and not legacy_value:
            legacy_value = candidate
        continue

    if stripped.startswith("AI_API_KEY="):
        found_ai_key = True
        out.append(line)
        continue

    replaced = False
    for name, secret_value in replacements.items():
        if stripped.startswith(name + "="):
            out.append(f"{name}={secret_value}")
            found[name] = True
            replaced = True
            break

    if not replaced:
        out.append(line)

if not found_ai_key:
    if out and out[-1].strip():
        out.append("")
    out.append("# OpenKB AI provider key. Use the key for the provider selected by OPENKB_AI_MODEL.")
    out.append(f"AI_API_KEY={legacy_value or 'replace-with-selected-ai-provider-api-key'}")

if not found["LDAP_PLACEHOLDER_PASSWORD"]:
    if out and out[-1].strip():
        out.append("")
    out.append("# Only used if LDAP_PLACEHOLDER_ENABLED=true.")
    out.append(f"LDAP_PLACEHOLDER_PASSWORD={replacements['LDAP_PLACEHOLDER_PASSWORD']}")

path.write_text("\n".join(out) + "\n", encoding="utf-8")

print(f"Generated bootstrap secrets in: {path}")
print("Updated: DJANGO_SECRET_KEY, POSTGRES_PASSWORD, LDAP_PLACEHOLDER_PASSWORD")
print("Preserved: comments, AI_API_KEY, LDAP_BIND_PASSWORD")
print()
print("Next: edit AI_API_KEY and LDAP_BIND_PASSWORD manually.")
print("Use no quotes, no spaces around '=', and avoid spaces/shell symbols.")
print("Good example: LDAP_BIND_PASSWORD=P@ssw0rd")
print("Avoid: LDAP_BIND_PASSWORD=\"P@ssw0rd!\"")
PYCODE
