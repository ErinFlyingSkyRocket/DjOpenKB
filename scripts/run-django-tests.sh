#!/usr/bin/env bash
set -euo pipefail

# DjOpenKB Linux/Docker Compose test runner.
# Run from the project root, for example:
#   cd /opt/DjOpenKB
#   ./scripts/run-django-tests.sh
#   ./scripts/run-django-tests.sh kb.testsuite.test_roles_matrix

TEST_TARGET="${1:-kb}"
VERBOSITY="${VERBOSITY:-2}"
KEEPDB_FLAG="${KEEPDB_FLAG:---keepdb}"

printf '\n[DjOpenKB tests] Checking Docker Compose services...\n'
sudo docker compose ps

printf '\n[DjOpenKB tests] Running Django system check...\n'
sudo docker compose exec web python manage.py check

printf '\n[DjOpenKB tests] Checking for missing migrations...\n'
sudo docker compose exec web python manage.py makemigrations --check --dry-run

printf '\n[DjOpenKB tests] Running test target: %s\n' "${TEST_TARGET}"
# shellcheck disable=SC2086
sudo docker compose exec web python manage.py test "${TEST_TARGET}" ${KEEPDB_FLAG} --verbosity="${VERBOSITY}"
