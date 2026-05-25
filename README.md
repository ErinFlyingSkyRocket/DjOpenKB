# DjOpenKB Deployment Guide

DjOpenKB is deployed with Docker Compose. The application runs behind Nginx on HTTPS port `8080`, uses PostgreSQL for Django data, stores application secrets in Vault, and keeps OpenKB knowledge-base content under `openkb-data/`.

This guide focuses on **how to install, run, maintain, and update the application**.

---

## 1. Server Requirements

Install these on the Linux server:

```bash
sudo apt update
sudo apt install -y git openssl
```

Install Docker Engine and Docker Compose Plugin:

```bash
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable docker
sudo systemctl start docker
```

Optional, allow your current Linux user to run Docker without `sudo`:

```bash
sudo usermod -aG docker $USER
newgrp docker
```

Check that Docker is ready:

```bash
docker --version
docker compose version
```

---

## 2. Download the Application

Clone the repository on the Linux server:

```bash
git clone https://github.com/ErinFlyingSkyRocket/DjOpenKB.git
cd DjOpenKB
```

If the repository already exists, update it with:

```bash
cd DjOpenKB
git pull
```

---

## 3. Configure Non-Secret Settings

Create the runtime `.env` file from the example:

```bash
cp .env.example .env
nano .env
```

The `.env` file should contain non-secret deployment settings such as:

```env
DJANGO_DEBUG=false

POSTGRES_DB=djopenkb
POSTGRES_USER=djopenkb
POSTGRES_HOST=db
POSTGRES_PORT=5432

OPENKB_BASE_DIR=OpenKB-main
OPENKB_DATA_DIR=openkb-data
OPENKB_AI_PROVIDER=openkb-cli
OPENKB_GEMINI_MODEL=gemini/gemini-2.5-flash
LITELLM_DROP_PARAMS=true

LDAP_ENABLED=true
LDAP_SERVER_URI=ldap://<AD-SERVER-IP>:389
LDAP_AD_DOMAIN=nextlabs.com
LDAP_NETBIOS_DOMAIN=NEXTLABS
LDAP_ALLOWED_EMAIL_DOMAINS=nextlabs.com
LDAP_USER_SEARCH_BASE=DC=nextlabs,DC=com
LDAP_USER_FILTER=(|(userPrincipalName=%(user)s)(sAMAccountName=%(user)s)(mail=%(user)s))
LDAP_BIND_DN=<service-account>@nextlabs.com

USE_SQLITE=false

VAULT_KV_MOUNT=secret
VAULT_SECRET_PATH=djopenkb
VAULT_AUTO_UNSEAL_INTERVAL_SECONDS=15
```

Do **not** put passwords, API keys, or Django secret keys in `.env`.

---

## 4. Configure Vault Bootstrap Secrets

Vault stores application secrets such as:

```text
DJANGO_SECRET_KEY
POSTGRES_PASSWORD
LDAP_BIND_PASSWORD
GEMINI_API_KEY
LLM_API_KEY
```

Create the bootstrap secret file:

```bash
cp vault/bootstrap/djopenkb.env.example vault/bootstrap/djopenkb.env
nano vault/bootstrap/djopenkb.env
```

Use quoted values for secrets:

```env
DJANGO_SECRET_KEY="replace-with-a-long-random-django-secret-key"
POSTGRES_PASSWORD="replace-with-a-stable-postgres-password"

GEMINI_API_KEY="replace-with-your-api-key"
LLM_API_KEY="replace-with-your-api-key"

LDAP_BIND_PASSWORD="replace-with-service-account-password"
LDAP_PLACEHOLDER_PASSWORD="replace-with-placeholder-password-or-leave-random"
```

Important notes:

```text
POSTGRES_PASSWORD must stay stable after the database is created.
Changing POSTGRES_PASSWORD later requires updating the password inside PostgreSQL too.
Do not run docker compose down -v unless you intentionally want to wipe Vault/Postgres data.
```

After Vault has seeded the secret successfully, remove the plaintext bootstrap file:

```bash
rm -f vault/bootstrap/djopenkb.env
```

---

## 5. Generate Local HTTPS Certificate

Generate the Nginx certificate:

```bash
cd nginx
chmod +x generate-localhost-cert.sh
./generate-localhost-cert.sh
cd ..
```

This creates:

```text
nginx/certs/localhost.crt
nginx/certs/localhost.key
```

For a real deployment, replace these with a proper certificate for the server hostname.

---

## 6. Start the Application

From the project root:

```bash
docker compose up --build -d
```

Watch logs:

```bash
docker compose logs -f
```

Check running services:

```bash
docker compose ps
```

Expected services include:

```text
djopenkb-vault
djopenkb-vault-auto-unseal
djopenkb-vault-init
djopenkb-postgres
djopenkb-web
djopenkb-nginx
djopenkb-cleanup-scheduler
```

Open the site:

```text
https://<server-ip>:8080
```

For local testing on the server:

```text
https://127.0.0.1:8080
```

---

## 7. Create a Local Admin Account

After the containers are running, create a Django superuser:

```bash
docker compose exec web python manage.py createsuperuser
```

Use this account for Django admin/local fallback access.

---

## 8. Verify AD Login

Check that the web container can reach the AD server:

```bash
docker compose exec web python -c "import socket; s=socket.socket(); s.settimeout(5); s.connect(('<AD-SERVER-IP>',389)); print('LDAP 389 reachable'); s.close()"
```

Check the LDAP settings loaded by Django:

```bash
docker compose exec web python manage.py shell -c "from django.conf import settings; print(settings.AUTH_LDAP_SERVER_URI); print(settings.AUTH_LDAP_BIND_DN); print(bool(settings.AUTH_LDAP_BIND_PASSWORD))"
```

Test LDAP service-account bind and user search:

```bash
docker compose exec web python manage.py test_ldap_auth <username> --bind-dn <service-account>@nextlabs.com --prompt-bind-password
```

Test full AD authentication:

```bash
docker compose exec web python manage.py test_ldap_auth <username> --auth
```

Then sign in from the login page using the domain login option.

---

## 9. Normal Restart

For normal restart without wiping data:

```bash
docker compose down
docker compose up -d
```

Or restart selected services:

```bash
docker compose restart web nginx
```

Do **not** use this for normal restart:

```bash
docker compose down -v
```

That removes Docker volumes and can wipe persistent data.

---

## 10. Updating the Application

Pull the latest code:

```bash
git pull
```

Rebuild and recreate the application containers:

```bash
docker compose up -d --build --force-recreate web nginx cleanup-scheduler
```

If database migrations are included:

```bash
docker compose exec web python manage.py migrate
```

If static files changed:

```bash
docker compose exec web python manage.py collectstatic --noinput
```

For full rebuild:

```bash
docker compose up -d --build --force-recreate
```

---

## 11. Updating Vault Secrets Later

If you need to update LDAP/API secrets later, recreate the bootstrap file temporarily:

```bash
cp vault/bootstrap/djopenkb.env.example vault/bootstrap/djopenkb.env
nano vault/bootstrap/djopenkb.env
```

Set only the secrets you intentionally want to update, then run:

```bash
docker compose up --force-recreate vault-init
docker compose restart web cleanup-scheduler
```

After confirming the application works:

```bash
rm -f vault/bootstrap/djopenkb.env
```

For `POSTGRES_PASSWORD`, do not rotate it this way unless you also update the password inside the existing PostgreSQL database.

---

## 12. Backup and Restore

Back up these persistent folders:

```text
postgres-data/
vault/file/
vault/keys/
openkb-data/
nginx/certs/
```

Example backup command:

```bash
tar -czf djopenkb-backup-$(date +%Y%m%d).tar.gz postgres-data vault/file vault/keys openkb-data nginx/certs
```

To restore, stop the application, restore the folders, then start Docker Compose again:

```bash
docker compose down
tar -xzf djopenkb-backup-YYYYMMDD.tar.gz
docker compose up -d
```

---

## 13. Useful Commands

View logs:

```bash
docker compose logs -f web
docker compose logs -f nginx
docker compose logs -f vault
docker compose logs -f db
```

Run migrations:

```bash
docker compose exec web python manage.py migrate
```

Create superuser:

```bash
docker compose exec web python manage.py createsuperuser
```

Open Django shell:

```bash
docker compose exec web python manage.py shell
```

Check Vault secret access:

```bash
docker compose exec vault vault status
```

Re-sync OpenKB AI data if needed:

```bash
docker compose exec web python manage.py sync_openkb_ai
```

Clean stray upload files manually:

```bash
docker compose exec web python manage.py cleanup_stray_upload_files --noinput
```

---

## 14. Common Issues

### Website does not start after changing `POSTGRES_PASSWORD`

The existing PostgreSQL database still expects the old password. Keep `POSTGRES_PASSWORD` stable after first boot, or update the password inside Postgres before changing Vault.

### AD login fails with `INVALID_CREDENTIALS data 52e`

This usually means the LDAP service account password is wrong or stale in Vault. Update `LDAP_BIND_PASSWORD` in Vault and restart the web container.

### Browser shows certificate warning

This is expected for a self-signed local certificate. Use a proper certificate for production.

### Docker cannot find `docker-compose.yml`

Run commands from the project root:

```bash
cd DjOpenKB
```

or specify the compose file explicitly:

```bash
docker compose -f /path/to/DjOpenKB/docker-compose.yml ps
```
