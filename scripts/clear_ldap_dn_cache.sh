#!/bin/sh
# Clear stale django-auth-ldap DN cache entries for one AD user only.
#
# What it does:
#   Deletes the targeted Redis/Django cache keys for the supplied username,
#   short-name alias, and linked email/UPN. It does not clear all Redis data,
#   MFA state, login lockouts, Celery data, sessions, or AI jobs.
#
# Run on the DjOpenKB server:
#   cd /opt/DjOpenKB
#   chmod +x scripts/clear_ldap_dn_cache.sh
#   ./scripts/clear_ldap_dn_cache.sh alice
#
# Provide extra aliases when needed:
#   ./scripts/clear_ldap_dn_cache.sh \
#     alice \
#     alice@<AD_DOMAIN>
#
# Use this only when one AD account is found by LDAP but a login format appears
# stale. No container restart or rebuild is required afterward.

set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <AD username-or-email> [additional aliases ...]" >&2
    echo "Example: $0 alice" >&2
    exit 2
fi

# Commas are not valid in the usernames used by DjOpenKB's LDAP login flow.
LDAP_CACHE_IDENTITIES=""
for identity in "$@"; do
    if [ -z "$LDAP_CACHE_IDENTITIES" ]; then
        LDAP_CACHE_IDENTITIES="$identity"
    else
        LDAP_CACHE_IDENTITIES="$LDAP_CACHE_IDENTITIES,$identity"
    fi
done

if ! sudo docker compose ps --status running web 2>/dev/null | grep -q 'djopenkb-web'; then
    echo "Error: the DjOpenKB web service is not running." >&2
    echo "Start it before clearing the LDAP DN cache." >&2
    exit 1
fi

sudo docker compose exec -T \
    -e LDAP_CACHE_IDENTITIES="$LDAP_CACHE_IDENTITIES" \
    web \
    python manage.py shell -c '
import os

from django.contrib.auth import get_user_model
from django.core.cache import cache

raw_identities = os.environ.get("LDAP_CACHE_IDENTITIES", "")
requested = [item.strip() for item in raw_identities.split(",") if item.strip()]

identities = []


def add_identity(value):
    value = (value or "").strip()
    if not value:
        return
    for candidate in (value, value.lower()):
        if candidate and candidate not in identities:
            identities.append(candidate)


for requested_identity in requested:
    add_identity(requested_identity)

    local_name = requested_identity.split("@", 1)[0].strip()
    add_identity(local_name)

    # Include the linked Django email address so a short username clears both
    # the short-name and UPN/email LDAP DN cache entries.
    user = (
        get_user_model()
        .objects.filter(username__iexact=local_name)
        .only("email")
        .first()
    )
    if user:
        add_identity(user.email)

sentinel = object()
cleared = 0

for identity in identities:
    key = f"django_auth_ldap.user_dn.{identity}"
    previous = cache.get(key, sentinel)
    existed = previous is not sentinel
    cache.delete(key)
    cleared += int(existed)
    status = "cleared" if existed else "not currently cached"
    print(f"{key}: {status}")

print(f"LDAP DN cache cleanup completed. Existing entries cleared: {cleared}.")
'

echo "You may now retry the AD login. No container restart is required."
