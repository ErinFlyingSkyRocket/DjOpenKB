# DjOpenKB Useful Commands

This document collects the day-to-day commands for running, maintaining, and troubleshooting DjOpenKB. It is intended as a quick operator reference after the application has already been deployed.

> Run commands from the project root where `docker-compose.yml` is located.

## Start, Stop, and Restart

Start the full application:

```bash
docker compose up -d --build
```

Stop the application without deleting data:

```bash
docker compose down
```

Restart only the Django web app and nginx:

```bash
docker compose restart web nginx
```

Rebuild only the web and nginx services after template/static/code changes:

```bash
docker compose up -d --build --force-recreate web nginx
```

Check service status:

```bash
docker compose ps
```

View live logs:

```bash
docker compose logs -f
```

View logs for a specific service:

```bash
docker compose logs -f web
docker compose logs -f nginx
docker compose logs -f db
docker compose logs -f vault
```

## Important Warning

Do not run this unless you intentionally want to delete Docker volumes:

```bash
docker compose down -v
```

This can remove persistent Vault/PostgreSQL data and may require re-seeding secrets or recreating database data depending on the deployment layout.

## Django Management

Run database migrations:

```bash
docker compose exec web python manage.py migrate
```

Create a Django superuser:

```bash
docker compose exec web python manage.py createsuperuser
```

Open the Django shell:

```bash
docker compose exec web python manage.py shell
```

Collect static files manually:

```bash
docker compose exec web python manage.py collectstatic --noinput
```

Check available management commands:

```bash
docker compose exec web python manage.py help
```

## Vault Secrets

Vault runs as the secret store for DjOpenKB. Secrets such as `POSTGRES_PASSWORD`, `DJANGO_SECRET_KEY`, API keys, and `LDAP_BIND_PASSWORD` are stored in Vault after bootstrap.

Check Vault status:

```bash
docker compose exec vault vault status
```

Read the DjOpenKB secret after logging in with an authorized Vault token:

```bash
docker compose exec vault vault kv get secret/djopenkb
```

Patch the LDAP service account password:

```bash
docker compose exec vault vault kv patch secret/djopenkb LDAP_BIND_PASSWORD="your-real-password"
docker compose restart web cleanup-scheduler
```

Patch an API key:

```bash
docker compose exec vault vault kv patch secret/djopenkb GEMINI_API_KEY="your-real-key" LLM_API_KEY="your-real-key"
docker compose restart web cleanup-scheduler
```

Re-run Vault init if you intentionally created `vault/bootstrap/djopenkb.env` for secret rotation:

```bash
docker compose up --force-recreate vault-init
docker compose restart web cleanup-scheduler
```

After Vault confirms the secret is seeded or patched, remove the plaintext bootstrap file:

```bash
rm -f vault/bootstrap/djopenkb.env
```

PowerShell:

```powershell
Remove-Item .\vault\bootstrap\djopenkb.env
```

## PostgreSQL

Open a PostgreSQL shell:

```bash
docker compose exec db psql -U djopenkb -d djopenkb
```

List tables inside `psql`:

```sql
\dt
```

Exit `psql`:

```sql
\q
```

If Django cannot connect and logs show `password authentication failed`, check that the password stored in Vault matches the password used when the PostgreSQL data directory was first initialized. Do not change `POSTGRES_PASSWORD` in Vault after the database already exists unless you also update the PostgreSQL user password.

## AD / LDAP Checks

Check whether the web container can reach the AD server:

```bash
docker compose exec web python -c "import socket; s=socket.socket(); s.settimeout(5); s.connect(('192.168.81.128',389)); print('LDAP 389 reachable'); s.close()"
```

Check Django LDAP settings:

```bash
docker compose exec web python manage.py shell -c "from django.conf import settings; print(settings.AUTH_LDAP_SERVER_URI); print(settings.AUTH_LDAP_BIND_DN); print(bool(settings.AUTH_LDAP_BIND_PASSWORD)); print(settings.AUTHENTICATION_BACKENDS)"
```

Test LDAP service account bind:

```bash
docker compose exec web python manage.py shell -c "import ldap; from django.conf import settings; conn=ldap.initialize(settings.AUTH_LDAP_SERVER_URI); conn.set_option(ldap.OPT_REFERRALS,0); conn.simple_bind_s(settings.AUTH_LDAP_BIND_DN, settings.AUTH_LDAP_BIND_PASSWORD); print('SERVICE BIND OK')"
```

Test LDAP search for a user:

```bash
docker compose exec web python manage.py test_ldap_auth alice
```

Test LDAP authentication interactively:

```bash
docker compose exec web python manage.py test_ldap_auth alice --auth
```

## OpenKB AI Sync

Sync Django articles into the integrated OpenKB AI data source:

```bash
docker compose exec web python manage.py sync_openkb_ai
```

Watch web logs while testing OpenKB AI:

```bash
docker compose logs -f web
```

## Cleanup and Admin Tools

Run stray upload cleanup manually:

```bash
docker compose exec web python manage.py cleanup_stray_upload_files
```

Run KB schema repair check manually:

```bash
docker compose exec web python manage.py repair_kb_schema
```

## Runtime Folders

These folders are created/tracked with `.gitkeep`, but their runtime contents should not be committed:

```text
vault/bootstrap/
vault/file/
vault/keys/
vault/logs/
postgres-data/
openkb-data/
```

If needed, create them manually on Linux:

```bash
mkdir -p vault/bootstrap vault/file vault/keys vault/logs postgres-data openkb-data/raw openkb-data/wiki
```

PowerShell:

```powershell
New-Item -ItemType Directory -Force vault\bootstrap, vault\file, vault\keys, vault\logs, postgres-data, openkb-data\raw, openkb-data\wiki
```

## Updating From GitHub

Pull the latest code:

```bash
git pull
```

Rebuild and restart:

```bash
docker compose up -d --build --force-recreate
```

For template or locale-only changes, restarting web/nginx is usually enough:

```bash
docker compose restart web nginx
```
