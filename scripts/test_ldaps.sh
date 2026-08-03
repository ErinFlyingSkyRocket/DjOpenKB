#!/bin/sh
# Test LDAPS DNS resolution and certificate validation from the web container.
#
# What it does:
#   Runs /app/scripts/test_ldaps_tls.py using the web container's configured
#   LDAP_SERVER_URI and mounted LDAP CA certificate. It does not test passwords.
#
# Run on the DjOpenKB server:
#   cd /opt/DjOpenKB
#   sudo docker compose exec -T web sh /app/scripts/test_ldaps.sh
#
# No rebuild or restart is required. The web service must already be running.

set -eu
python /app/scripts/test_ldaps_tls.py
