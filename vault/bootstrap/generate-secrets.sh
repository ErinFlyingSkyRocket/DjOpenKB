#!/usr/bin/env sh
# Safely creates first-time DjOpenKB bootstrap secrets.
#
# Existing real secrets are preserved. Values are generated only when the
# matching line is blank or still an obvious placeholder.
#
# Optional, explicit rotation (dangerous on a deployed instance):
#   ROTATE_GENERATED_SECRETS=1 sh vault/bootstrap/generate-secrets.sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUTPUT_FILE="${OUTPUT_FILE:-$SCRIPT_DIR/djopenkb.env}"
EXAMPLE_FILE="${EXAMPLE_FILE:-$SCRIPT_DIR/djopenkb.env.example}"
DJANGO_KEY_LENGTH="${DJANGO_KEY_LENGTH:-72}"
POSTGRES_PASSWORD_LENGTH="${POSTGRES_PASSWORD_LENGTH:-40}"
FIELD_ENCRYPTION_KEY_LENGTH="${FIELD_ENCRYPTION_KEY_LENGTH:-72}"
PLACEHOLDER_PASSWORD_LENGTH="${PLACEHOLDER_PASSWORD_LENGTH:-40}"
ROTATE_GENERATED_SECRETS="${ROTATE_GENERATED_SECRETS:-0}"

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "ERROR: python3/python not found. Install python3 or python-is-python3 first." >&2
    exit 1
fi

mkdir -p "$(dirname -- "$OUTPUT_FILE")"

CREATED_NEW_FILE=0
if [ ! -f "$OUTPUT_FILE" ]; then
    CREATED_NEW_FILE=1
    if [ -f "$EXAMPLE_FILE" ]; then
        cp "$EXAMPLE_FILE" "$OUTPUT_FILE"
    else
        cat > "$OUTPUT_FILE" <<'HEADER'
# ---------------------------------------------------------------------
# DjOpenKB Vault bootstrap secrets
# ---------------------------------------------------------------------
# Generated locally. Do not commit or share this file.
# Do not put spaces around "=".
# Generated values are alphanumeric-only and should stay unquoted.

DJANGO_SECRET_KEY=replace-with-a-long-random-django-secret-key
DJANGO_FIELD_ENCRYPTION_KEY=replace-with-a-long-random-field-encryption-key
POSTGRES_PASSWORD=replace-with-stable-postgres-password

AI_API_KEY=
GEMINI_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
LDAP_BIND_PASSWORD=
LDAP_PLACEHOLDER_PASSWORD=replace-with-placeholder-password-or-leave-random

# Direct SMTP review-notification service account.
SMTP_RELAY_USERNAME=
SMTP_RELAY_PASSWORD=
# Set true only for a controlled transition using LDAP_BIND_PASSWORD.
SMTP_RELAY_PASSWORD_USE_LDAP_BIND_PASSWORD=false
HEADER
    fi
fi

"$PYTHON_BIN" - \
    "$OUTPUT_FILE" \
    "$DJANGO_KEY_LENGTH" \
    "$POSTGRES_PASSWORD_LENGTH" \
    "$FIELD_ENCRYPTION_KEY_LENGTH" \
    "$PLACEHOLDER_PASSWORD_LENGTH" \
    "$CREATED_NEW_FILE" \
    "$ROTATE_GENERATED_SECRETS" <<'PY'
import re
import secrets
import string
import sys
from pathlib import Path

path = Path(sys.argv[1])
django_len = int(sys.argv[2])
postgres_len = int(sys.argv[3])
field_key_len = int(sys.argv[4])
placeholder_len = int(sys.argv[5])
created_new_file = sys.argv[6] == "1"
rotate = sys.argv[7] == "1"

for name, value in {
    "DJANGO_KEY_LENGTH": django_len,
    "POSTGRES_PASSWORD_LENGTH": postgres_len,
    "FIELD_ENCRYPTION_KEY_LENGTH": field_key_len,
    "PLACEHOLDER_PASSWORD_LENGTH": placeholder_len,
}.items():
    if value < 1:
        raise SystemExit(f"ERROR: {name} must be greater than zero.")

alphabet = string.ascii_letters + string.digits

def make_secret(length: int) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(length))

def is_placeholder(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return True
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {"'", '"'}:
        candidate = candidate[1:-1]
    return bool(re.match(r"^(replace-with|change[-_]?me|example|todo|your[-_]?|<.+>)", candidate, re.I))

# utf-8-sig accepts an old Windows PowerShell BOM if present. Write back as
# BOM-free LF text so the Linux /bin/sh source step remains reliable.
text = path.read_text(encoding="utf-8-sig")
lines = text.splitlines()

new_values = {
    "DJANGO_SECRET_KEY": make_secret(django_len),
    "POSTGRES_PASSWORD": make_secret(postgres_len),
    "DJANGO_FIELD_ENCRYPTION_KEY": make_secret(field_key_len),
    "LDAP_PLACEHOLDER_PASSWORD": make_secret(placeholder_len),
}

assignment = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
existing_keys = set()
for line in lines:
    match = assignment.match(line)
    if match:
        existing_keys.add(match.group(2))

looks_like_full_bootstrap = created_new_file or bool(
    {"DJANGO_SECRET_KEY", "POSTGRES_PASSWORD", "DJANGO_FIELD_ENCRYPTION_KEY"} & existing_keys
)

out = []
updated = []
for line in lines:
    match = assignment.match(line)
    if match:
        leading, key, current_value = match.groups()
        if key in new_values and (rotate or is_placeholder(current_value)):
            out.append(f"{leading}{key}={new_values[key]}")
            updated.append(key)
            continue
    out.append(line)

added_smtp = []
if looks_like_full_bootstrap:
    missing_smtp = [
        key for key in (
            "SMTP_RELAY_USERNAME",
            "SMTP_RELAY_PASSWORD",
            "SMTP_RELAY_PASSWORD_USE_LDAP_BIND_PASSWORD",
        )
        if key not in existing_keys
    ]
    if missing_smtp:
        if out and out[-1].strip():
            out.append("")
        out.append("# Direct SMTP review-notification service account. Fill these only when SMTP notifications are enabled.")
        for key in missing_smtp:
            if key == "SMTP_RELAY_USERNAME":
                out.append("SMTP_RELAY_USERNAME=")
            elif key == "SMTP_RELAY_PASSWORD":
                out.append("SMTP_RELAY_PASSWORD=")
            else:
                out.append("# Set true only for a controlled transition using LDAP_BIND_PASSWORD; false is recommended.")
                out.append("SMTP_RELAY_PASSWORD_USE_LDAP_BIND_PASSWORD=false")
            added_smtp.append(key)

with path.open("w", encoding="utf-8", newline="\n") as handle:
    handle.write("\n".join(out) + "\n")

print(f"Bootstrap file: {path}")
print("Generated: " + (", ".join(updated) if updated else "none (existing non-placeholder secrets were preserved)"))
if added_smtp:
    print("Added direct-SMTP placeholders: " + ", ".join(added_smtp))
elif not looks_like_full_bootstrap and not created_new_file:
    print("Detected an update-only bootstrap file; no unrelated settings were appended.")
print("Manual values preserved/not generated: AI API keys, LDAP_BIND_PASSWORD, SMTP_RELAY_USERNAME, SMTP_RELAY_PASSWORD")
print()
print("Review the file before using it. Do not commit, upload, or submit it.")
if rotate:
    print("WARNING: ROTATE_GENERATED_SECRETS=1 was used. Do not apply rotated POSTGRES_PASSWORD or DJANGO_FIELD_ENCRYPTION_KEY to an existing deployment without a deliberate migration plan.")
PY
