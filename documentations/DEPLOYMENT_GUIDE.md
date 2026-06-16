# DjOpenKB Deployment Guide

This guide explains how to deploy DjOpenKB on a Linux server using Docker Compose.

DjOpenKB integrates with VectifyAI OpenKB for the AI knowledge base. OpenKB must be initialized locally in the `openkb-data/` folder before the Django AI sync command is run.

The project can be deployed for a local/internal network without buying a public domain name. Users can access it through the Linux server IP address, for example:

```text
https://<linux-server-ip>:8080
```

---

## 1. Prepare the Linux server

Log in to the Linux server using SSH or the local terminal.

Update the package list.

```bash
sudo apt update
```

Upgrade installed packages.

```bash
sudo apt upgrade -y
```

Install the required packages.

```bash
sudo apt install -y git curl ca-certificates openssl python3 python-is-python3 python3-venv nano unzip docker.io docker-compose-v2
```

Check Docker is installed.

```bash
docker --version
docker compose version
```

If Docker gives a permission error, use `sudo docker compose`.

```bash
sudo docker compose version
```

Optional: allow your Linux user to run Docker without `sudo`.

```bash
sudo usermod -aG docker $USER
```

You must log out and log back in before this takes effect. Until then, continue using:

```bash
sudo docker compose ...
```

---

## 2. Create the deployment folder

Create the `/opt` deployment folder if it does not already exist.

```bash
sudo mkdir -p /opt
```

Move into `/opt`.

```bash
cd /opt
```

If this is a fresh deployment, clone the project from GitHub.

```bash
sudo git clone https://github.com/ErinFlyingSkyRocket/DjOpenKB.git
```

Give your Linux user ownership of the project folder.

```bash
sudo chown -R $USER:$USER /opt/DjOpenKB
```

Move into the project folder.

```bash
cd /opt/DjOpenKB
```

Confirm you are in the correct folder.

```bash
pwd
ls
```

You should see files such as:

```text
docker-compose.yml
Dockerfile
manage.py
djopenkb/
kb/
nginx/
vault/
documentations/
```

---

## 3. Pull latest code for an existing deployment

If the project folder already exists, do not clone again. Move into the existing folder.

```bash
cd /opt/DjOpenKB
```

Check the current Git status.

```bash
git status
```

Pull the latest version.

```bash
git pull
```

If Git says there are local changes, review them before pulling. Do not overwrite local secrets such as `.env` or `vault/bootstrap/djopenkb.env`.

---

## 4. Create the `.env` file

Copy the example `.env` file.

```bash
cp .env.example .env
```

Open it for editing.

```bash
nano .env
```

Set the main runtime values.

```env
DJANGO_DEBUG=false

POSTGRES_DB=djopenkb
POSTGRES_USER=djopenkb
POSTGRES_HOST=db
POSTGRES_PORT=5432

OPENKB_BASE_DIR=OpenKB-main
OPENKB_DATA_DIR=openkb-data
OPENKB_AI_PROVIDER=openkb-cli
OPENKB_AI_MODEL=gemini/gemini-2.5-flash
LITELLM_DROP_PARAMS=true

# Production shared cache/rate-limit backend. Required when DJANGO_DEBUG=false.
REDIS_URL=redis://redis:6379/1
DJANGO_ALLOW_LOCAL_CACHE_FALLBACK=false

# OpenKB AI safety limits.
OPENKB_AI_MAX_PROMPT_CHARS=1000
OPENKB_AI_RATE_LIMIT_MAX_REQUESTS=5
OPENKB_AI_RATE_LIMIT_WINDOW_SECONDS=60
OPENKB_AI_RATE_LIMIT_BLOCK_SECONDS=1800
OPENKB_AI_TIMEOUT_SECONDS=90
OPENKB_AI_CONCURRENCY_LIMIT=2
OPENKB_AI_CONCURRENCY_LOCK_SECONDS=120

# Fallback authentication lockout values used before Site settings are available.
# The real production policy is managed in Django Admin -> Site settings.
AUTH_LOCKOUT_STRIKE_TTL_SECONDS=604800

USE_SQLITE=false

VAULT_KV_MOUNT=secret
VAULT_SECRET_PATH=djopenkb
VAULT_AUTO_UNSEAL_INTERVAL_SECONDS=15
```

Set the allowed host values for the Linux server IP address.

Replace `192.168.81.50` with the actual Linux server IP.

```env
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,web,nginx,192.168.81.50
DJANGO_CSRF_TRUSTED_ORIGINS=https://localhost:8080,https://127.0.0.1:8080,https://192.168.81.50:8080
```

Find your Linux server IP if needed.

```bash
hostname -I
```

---

## 5. Configure LDAP or LDAPS in `.env`

If Active Directory login is not needed, keep LDAP disabled.

```env
LDAP_ENABLED=false
LDAP_PLACEHOLDER_ENABLED=false
LDAP_PLACEHOLDER_AUTO_CREATE_USERS=false
```

If LDAPS is enabled, update the LDAP section based on the Windows Server / Active Directory setup.

Example:

```env
LDAP_ENABLED=true
LDAP_PLACEHOLDER_ENABLED=false
LDAP_PLACEHOLDER_AUTO_CREATE_USERS=false

LDAP_SERVER_URI=ldaps://<DOMAIN_CONTROLLER_FQDN>:636
LDAP_START_TLS=false
LDAP_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/ad-ca.crt
LDAP_TLS_REQUIRE_CERT=demand
LDAP_ALLOW_INSECURE=false

# Example AD domain values. Replace these with your real AD details.
# AD DNS domain / UPN suffix example: openkb.local
# NetBIOS domain example: OPENKB
LDAP_AD_DOMAIN=openkb.local
LDAP_NETBIOS_DOMAIN=OPENKB
LDAP_ALLOWED_EMAIL_DOMAINS=openkb.local,company.com

# For openkb.local, use DC=openkb,DC=local.
# For example.corp.local, use DC=example,DC=corp,DC=local.
LDAP_USER_SEARCH_BASE=DC=openkb,DC=local
LDAP_USER_FILTER=(|(sAMAccountName=%(user)s)(userPrincipalName=%(user)s)(userPrincipalName=%(user)s@openkb.local)(mail=%(user)s)(mail=%(user)s@openkb.local)(mail=%(user)s@company.com)(userPrincipalName=%(user)s@company.com))
LDAP_BIND_DN=svc_djopenkb@openkb.local
LDAP_EXTRA_HOSTNAME=dc01.openkb.local
LDAP_EXTRA_SHORT_HOSTNAME=dc01
LDAP_DC_IP=<AD_DC_IP>
```

Notes:

```text
- `openkb.local` is only an example placeholder. Replace it with the organisation's real AD DNS domain, such as `corp.local` or `qapf1.qalab01.nextlabs.com`.
- Convert an AD domain name to `LDAP_USER_SEARCH_BASE` by splitting the dots into `DC=` parts. Example: `openkb.local` becomes `DC=openkb,DC=local`.
- `LDAP_BIND_DN` should use the actual AD service account UPN that can bind/search, for example `svc_djopenkb@openkb.local`.
- `LDAP_ALLOWED_EMAIL_DOMAINS` can include both the public email domain and the AD UPN suffix.
- `LDAP_EXTRA_HOSTNAME`, `LDAP_EXTRA_SHORT_HOSTNAME`, and `LDAP_DC_IP` are only needed when Docker/Linux cannot resolve the Domain Controller hostname.
- Keep `LDAP_SERVER_URI` as the hostname/FQDN for LDAPS certificate validation.
```

For full LDAPS setup, refer to:

```text
documentations/LDAP_LDAPS_SETUP.md
documentations/WINDOWS_SERVER_2022_AD_LDAPS_SETUP.md
```
## 6. Generate Vault bootstrap secrets

Make the secret generator executable.

```bash
chmod +x vault/bootstrap/generate-secrets.sh
```

Run it.

```bash
./vault/bootstrap/generate-secrets.sh
```

Open the generated Vault bootstrap file.

```bash
nano vault/bootstrap/djopenkb.env
```

Confirm it contains the required secrets.

```env
DJANGO_SECRET_KEY=generated-random-value
POSTGRES_PASSWORD=generated-random-value
DJANGO_FIELD_ENCRYPTION_KEY=generated-random-value

# Use the key for the provider selected by OPENKB_AI_MODEL.
# Keep AI_API_KEY for compatibility and set the provider-specific key as well.
AI_API_KEY=your-selected-ai-provider-api-key
GEMINI_API_KEY=your-gemini-api-key-if-using-gemini
OPENAI_API_KEY=your-openai-api-key-if-using-openai
ANTHROPIC_API_KEY=your-anthropic-api-key-if-using-claude

LDAP_BIND_PASSWORD=your-ad-service-account-password
LDAP_PLACEHOLDER_PASSWORD=generated-random-value
```

Important notes:

```text
- Do not commit or share vault/bootstrap/djopenkb.env.
- For a fresh setup, generate POSTGRES_PASSWORD before the first startup.
- For an existing database, do not change POSTGRES_PASSWORD unless you also update it inside Postgres.
- Use plain `KEY=value` format with no quotes and no spaces around `=` where possible.
- Keep DJANGO_SECRET_KEY stable after deployment. Changing it can invalidate sessions and signed data.
- Keep DJANGO_FIELD_ENCRYPTION_KEY stable after deployment. Changing it can make encrypted MFA secrets unreadable unless data is reset or re-encrypted.
- Keep POSTGRES_PASSWORD stable after the database is created. If Vault and Postgres passwords no longer match, Django/Postgres access can fail.
- Keep Vault data and keys safe. If Vault data/keys are lost, Django may not be able to read the stored database, LDAP, field-encryption, and AI secrets.
- After Vault is seeded and login works, remove vault/bootstrap/djopenkb.env from exported/shared copies.
```

Recommended practice:

```text
Save the final generated DJANGO_SECRET_KEY, DJANGO_FIELD_ENCRYPTION_KEY, and POSTGRES_PASSWORD securely in an offline password manager or protected administrator record.
Do not regenerate these values on an existing deployment unless you intentionally rotate them and know the matching update steps.
```

---

## 7. Initialise OpenKB data locally

OpenKB must be initialized once in the local `openkb-data/` folder because Docker mounts this folder into the Django container.

During first deployment, create a temporary local Python virtual environment only to run `openkb init`. After OpenKB has created `openkb-data/.openkb/`, the temporary virtual environment is no longer needed and can be removed.

Initialize OpenKB locally at:

```text
/opt/DjOpenKB/openkb-data
```

Move to the project root.

```bash
cd /opt/DjOpenKB
```

Create a temporary local Python virtual environment for OpenKB initialization.

```bash
python3 -m venv .openkb-venv
source .openkb-venv/bin/activate
python -m pip install --upgrade pip
```

Install OpenKB. For this project, the OpenKB source is already included under `OpenKB-main/`, so install from that local folder.

```bash
pip install -e OpenKB-main
```

If preferred, the official VectifyAI OpenKB package can also be installed from PyPI.

```bash
pip install openkb
```

Create and enter the OpenKB data folder.

```bash
mkdir -p openkb-data
cd openkb-data
```

Run OpenKB init locally.

```bash
openkb init
```

If the `openkb` command is not found, use the local Python module command instead.

```bash
PYTHONPATH=../OpenKB-main python -m openkb.cli init
```

OpenKB will ask for a model in LiteLLM format. When you see this prompt:

```text
Model (enter for default shown by your installed OpenKB version):
```

type the same model name configured in `.env` as `OPENKB_AI_MODEL`.

For the current recommended setup, enter:

```text
gemini/gemini-2.5-flash
```

### Common OpenKB model inputs

| Provider | Example model input |
|---|---|
| Gemini Flash | `gemini/gemini-2.5-flash` |
| Gemini Pro | `gemini/gemini-2.5-pro` |
| OpenAI model | `openai/<model-name>` or the exact model string supported by your OpenKB/LiteLLM version |
| Anthropic Claude Haiku | `anthropic/claude-3-5-haiku-latest` |
| Anthropic Claude Sonnet | `anthropic/claude-3-5-sonnet-latest` |
| OpenRouter | `openrouter/openai/gpt-4o-mini` |
| Groq | `groq/llama-3.1-8b-instant` |
| Mistral | `mistral/mistral-small-latest` |
| Cohere | `cohere/command-r` |
| Ollama local model | `ollama/llama3.1` |

Some OpenKB versions may not show many prompts and may silently create the configuration files. That is acceptable.

Check that OpenKB created the local runtime configuration.

```bash
ls -la
ls -la .openkb
```

If `.openkb/` exists, OpenKB init has completed.

Return to the project root and deactivate the virtual environment.

```bash
cd /opt/DjOpenKB
deactivate
```

Because `.openkb-venv` is only used for OpenKB initialization, remove it after confirming `openkb-data/.openkb/` exists.

```bash
rm -rf .openkb-venv
```

Do not commit or share the generated OpenKB runtime configuration folder:

```text
openkb-data/.openkb/
.openkb-venv/
```

The old `openkb-data/.env` file is not required for the current DjOpenKB setup because the AI model is configured through `.env` and the AI API keys are stored in Vault as `AI_API_KEY` and provider-specific keys such as `GEMINI_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`.

---

## 8. Generate the local Nginx HTTPS certificate

Make the certificate script executable.

```bash
chmod +x nginx/certs/generate-localhost-cert.sh
```

Run the script.

```bash
./nginx/certs/generate-localhost-cert.sh
```

Nginx expects the certificate files at these paths inside the container:

```text
/etc/nginx/certs/localhost.crt
/etc/nginx/certs/localhost.key
```

The Docker Compose mount maps that to these paths on the Linux host:

```text
nginx/certs/localhost.crt
nginx/certs/localhost.key
```

Check that the files exist.

```bash
ls -l nginx/certs/localhost.crt nginx/certs/localhost.key
```

Set permissions.

```bash
chmod 644 nginx/certs/localhost.crt
chmod 600 nginx/certs/localhost.key
```

This is a self-signed certificate for local/intranet testing. The browser may show a warning.

---

## 9. Add the LDAPS CA certificate if using AD

If LDAPS is enabled, the Windows Server / Active Directory CA certificate must be available to the Linux server and mounted into the Django `web` container.

Recommended Windows export format:

```text
Base-64 encoded X.509 (.CER)
```

Rename or copy the exported certificate to:

```text
ldap-certs/ad-ca.crt
```

Check that the file exists on the Linux server:

```bash
ls -l ldap-certs/ad-ca.crt
```

The file should be PEM/readable text. Check it with:

```bash
head -n 5 ldap-certs/ad-ca.crt
```

Expected:

```text
-----BEGIN CERTIFICATE-----
...
```

If the file shows unreadable binary characters instead, it was exported as DER/binary. Convert it on the Linux server:

```bash
openssl x509 -inform DER -in ldap-certs/ad-ca.crt -out ldap-certs/ad-ca.pem
mv ldap-certs/ad-ca.crt ldap-certs/ad-ca.der.bak
mv ldap-certs/ad-ca.pem ldap-certs/ad-ca.crt
```

Then verify it again:

```bash
openssl x509 -in ldap-certs/ad-ca.crt -noout -subject -issuer -dates
```

If certificate validation still fails with `unable to get local issuer certificate`, the file is readable but the CA chain is incomplete. Export the Root CA and any Issuing/Intermediate CA certificates from Windows Server, convert them to PEM if needed, then combine them:

```bash
cat issuing-ca.crt root-ca.crt > ldap-certs/ad-ca.crt
```

If the Linux server or Docker container cannot resolve the AD hostname, make sure these fields are set in `.env`:

```env
LDAP_EXTRA_HOSTNAME=<DOMAIN_CONTROLLER_FQDN>
LDAP_EXTRA_SHORT_HOSTNAME=<DOMAIN_CONTROLLER_SHORT_HOSTNAME>
LDAP_DC_IP=<AD_DC_IP>
```

Replace `<AD_DC_IP>` with the actual Windows Server Domain Controller IP. Keep `LDAP_SERVER_URI` as the Domain Controller hostname/FQDN, not the IP address, so LDAPS hostname validation can work correctly.
## 10. Start the Docker stack

Start and build all containers.

```bash
sudo docker compose up -d --build
```

Check container status.

```bash
sudo docker compose ps
```

Check Vault init logs.

```bash
sudo docker compose logs -f vault-init
```

Check web logs.

```bash
sudo docker compose logs -f web
```

Check Nginx logs.

```bash
sudo docker compose logs -f nginx
```

If everything is successful, the `web`, `db`, `redis`, `vault`, `cleanup-scheduler`, and `nginx` services should be running.

---

## 11. Fix Vault init failure during fresh testing

If `vault-init` fails, check the Vault bootstrap file with line numbers.

```bash
nl -ba vault/bootstrap/djopenkb.env
```

The file should use valid `KEY=VALUE` lines.

Examples:

```env
DJANGO_SECRET_KEY=randomvalue
POSTGRES_PASSWORD=randomvalue
LDAP_BIND_PASSWORD=password-without-quotes-when-possible
```

Do not put spaces around `=`.

Correct:

```env
DJANGO_SECRET_KEY=randomvalue
```

Wrong:

```env
DJANGO_SECRET_KEY = randomvalue
```

For a fresh failed deployment only, reset Vault state and start again.

```bash
sudo docker compose down
sudo rm -rf vault/file vault/keys
mkdir -p vault/file vault/keys
chmod 700 vault/keys
sudo docker compose up -d --build
```

Do not reset Vault on a real deployment unless you understand that it removes local Vault state.

---

## 12. Run Django setup commands

Run migrations. This also creates the admin-configurable authentication lockout policy stages and seeds the default policy if it does not already exist.

```bash
sudo docker compose exec web python manage.py migrate
```

Collect static files.

```bash
sudo docker compose exec web python manage.py collectstatic --noinput
```

Create the first local Django admin account.

```bash
sudo docker compose exec web python manage.py createsuperuser
```

Run the Django deployment check.

```bash
sudo docker compose exec web python manage.py check --deploy
```

The admin account can access:

```text
https://<linux-server-ip>:8080/admin/
```

Most user, article, permission, and log management can be handled through the Django Admin site.

---

## 13. Sync Django articles into OpenKB

After Docker is running and database migrations are complete, sync published Django articles into the local OpenKB data folder.

```bash
sudo docker compose exec web python manage.py sync_openkb_ai
```

If many articles are added or changed later, run the sync command again.

```bash
sudo docker compose exec web python manage.py sync_openkb_ai
```

Check that the container can see the locally initialized OpenKB data folder.

```bash
sudo docker compose exec web ls -la /app/openkb-data
sudo docker compose exec web ls -la /app/openkb-data/.openkb
```

---

## 14. Test LDAPS connection

If LDAPS is enabled, test from inside the web container.

First check the CA certificate mount:

```bash
sudo docker compose exec web ls -l /etc/ssl/certs/djopenkb-ldap/ad-ca.crt
sudo docker compose exec web head -n 5 /etc/ssl/certs/djopenkb-ldap/ad-ca.crt
```

The first line should be:

```text
-----BEGIN CERTIFICATE-----
```

Then test LDAPS:

```bash
sudo docker compose exec web sh scripts/test_ldaps.sh
```

Expected successful output:

```text
TLS handshake OK
LDAPS DNS + TLS certificate validation looks good.
```

You can also test manually:

```bash
sudo docker compose exec web openssl s_client \
  -connect <DOMAIN_CONTROLLER_FQDN>:636 \
  -servername <DOMAIN_CONTROLLER_FQDN> \
  -CAfile /etc/ssl/certs/djopenkb-ldap/ad-ca.crt
```

Expected certificate result:

```text
Verify return code: 0 (ok)
```

If it fails, refer to:

```text
documentations/LDAP_LDAPS_SETUP.md
documentations/WINDOWS_SERVER_2022_AD_LDAPS_SETUP.md
```
## 15. Access the website

Open the website using the Linux server IP address.

```text
https://<linux-server-ip>:8080
```

Example:

```text
https://192.168.81.50:8080
```

If using the self-signed certificate, accept the browser warning for the lab/internal environment.

### 15.1 Expected access-control behaviour

Current DjOpenKB is configured as a login-only internal knowledge base.

Expected browser behaviour:

```text
https://<linux-server-ip>:8080/          → login page
https://<linux-server-ip>:8080/home/     → requires login
normal article/search/profile/admin URLs → require login
anonymous access to protected paths       → 404
/admin/login/                             → hidden / 404
/admin/                                   → allowed only after normal login, Admin Users/superuser sync, and admin CIDR/VPN checks
```

After login and MFA completion where applicable, users are redirected to `/home/`.

Default role behaviour:

```text
New normal local/AD user → Regular User group
Disabled User          → highest precedence; removes admin/role groups, clears direct permissions, unchecks staff/superuser, and redirects to the disabled-account page
Regular User            → fallback viewer role; view published articles and vote
Article Writer          → create and submit articles; includes view/vote access and removes redundant Regular User
Article Approver        → review/manage pending articles and pending updates; includes view/vote access and removes redundant Regular User
Article Manager         → create/edit/manage articles, review approvals, and delete articles; includes view/vote access and removes redundant Regular User
Admin Users             → full administrator source of truth; sets staff/superuser, removes normal standard role groups, and preserves account source
```

Direct user permission checkboxes in Django Admin are add-on content permissions only. They grant exceptions on top of group membership and do not remove group permissions. They do not grant Django Admin access.

Admin setting precedence:

```text
Disabled User wins over every other standard role.
Admin Users grants full admin/superuser access unless Disabled User is also assigned.
Regular User is the fallback viewer role and is only auto-added when no other standard role exists. Article Writer, Article Approver, and Article Manager are elevated content roles that may be combined with each other and do not need Regular User.
Custom future groups, such as email notification groups, are preserved.
Django Active = whether the account can sign in at all.
Disabled User = account retained but restricted to the disabled-account page/sign-out flow.
```

Account source is preserved during promotion/demotion:

```text
Local user + Admin Users  → Local admin
LDAP user + Admin Users   → LDAP admin
Local admin removed from Admin Users → Local user
LDAP admin removed from Admin Users  → LDAP user
```

Admins may edit Account Type and Source in Django Admin for recovery cases, such as converting an AD/LDAP account into a local account after the AD account is deleted while preserving article ownership.

### 15.2 Quick post-login validation checklist

After deployment, test these flows once:

```text
1. Incognito / anonymous request to /home/ returns 404 or forces login according to the login guard.
2. / displays the login page.
3. A new local or AD user lands in Regular User when no other standard role is assigned and can view published articles.
4. A user moved to Disabled User loses staff/superuser status, cannot use functions, and is sent to the disabled-account page.
5. A user in Admin Users becomes staff/superuser and other normal standard role groups are removed.
6. Removing a user from Admin Users and placing them back into a normal role removes staff/superuser status.
7. Local users promoted to Admin Users show as Local admin; LDAP users promoted to Admin Users show as LDAP admin.
8. Article Writer can create and submit an article.
9. Article Approver can approve/reject pending articles.
10. Article Manager can create, edit/manage, approve/reject, and delete articles without needing Django Admin access.
10. Admin Users can access admin tools and /admin/ from the allowed admin network/VPN.
11. /admin/login/ does not expose the normal Django admin login page.
12. Search only returns title/keyword matches.
13. Homepage tabs paginate correctly according to the Articles per page setting.
14. Keyword suggestion refresh only suggests existing manually-created keywords that exactly appear in the current draft title/body.
```

---

## 16. Normal Docker Compose operation commands

This section is useful after the first deployment is completed.

### Check service status

```bash
sudo docker compose ps
```

### Start services

```bash
sudo docker compose up -d
```

### Start and rebuild services

Use this after code, dependency, Dockerfile, or Docker Compose changes.

```bash
sudo docker compose up -d --build
```

### Stop services without deleting data

```bash
sudo docker compose down
```

This stops containers but keeps normal bind-mounted project data such as PostgreSQL data, Vault data, uploaded files, and OpenKB data.

### Restart only the Django web container

Use this after small Python/template/config changes that do not require a rebuild.

```bash
sudo docker compose restart web
```

### Restart Nginx only

Use this after changing Nginx configuration or certificates.

```bash
sudo docker compose restart nginx
```

### Restart the cleanup scheduler only

Use this after changing scheduled cleanup scripts or maintenance commands.

```bash
sudo docker compose restart cleanup-scheduler
```

### Restart multiple services

```bash
sudo docker compose restart web nginx cleanup-scheduler
```

### View logs

Follow live logs.

```bash
sudo docker compose logs -f web
sudo docker compose logs -f nginx
sudo docker compose logs -f db
sudo docker compose logs -f vault
sudo docker compose logs -f cleanup-scheduler
```

Show only recent logs.

```bash
sudo docker compose logs --tail=100 web
sudo docker compose logs --tail=100 nginx
sudo docker compose logs --tail=100 vault
sudo docker compose logs --tail=100 cleanup-scheduler
```

### Open a shell inside the web container

```bash
sudo docker compose exec web sh
```

Exit the shell with:

```bash
exit
```

### Run common Django commands

Run migrations. This also creates the admin-configurable authentication lockout policy stages and seeds the default policy if it does not already exist.

```bash
sudo docker compose exec web python manage.py migrate
```

Collect static files.

```bash
sudo docker compose exec web python manage.py collectstatic --noinput
```

Check Django configuration.

```bash
sudo docker compose exec web python manage.py check
sudo docker compose exec web python manage.py check --deploy
```

Create a Django superuser.

```bash
sudo docker compose exec web python manage.py createsuperuser
```

Sync published Django articles into OpenKB AI data.

```bash
sudo docker compose exec web python manage.py sync_openkb_ai
```

Compile translations after `.po` file changes.

```bash
sudo docker compose exec web python manage.py compilemessages
```

Clean general activity logs.

```bash
sudo docker compose exec web python manage.py cleanup_activity_logs --dry-run
sudo docker compose exec web python manage.py cleanup_activity_logs
```

### Important warning about deleting volumes

Do not run this on a real deployment unless you intentionally want to delete Docker-managed volumes.

```bash
sudo docker compose down -v
```

For this project, local bind-mounted folders such as `postgres-data/`, `vault/file/`, `vault/keys/`, `openkb-data/`, and uploaded files should also be protected. Do not delete them unless you are intentionally resetting the environment.

### Production hardening note for the `/app` bind mount

During development, the Compose bind mount below is convenient because host code changes appear immediately in the web container:

```yaml
- .:/app
```

For a final production-style deployment, remove the full-project bind mount where possible and rebuild the Docker image with:

```bash
sudo docker compose up -d --build
```

This reduces the chance that `.env`, Vault material, local certificates, `.git/`, or temporary files are visible inside the running web container. Keep `.dockerignore` updated so secrets and runtime folders are not copied into the image during build.

---

## 17. Pull latest updates later

Use this section when the code has been updated on GitHub and the Linux instance should follow the latest version.

### 17.1 Move into the project folder

```bash
cd /opt/DjOpenKB
```

Confirm the current branch.

```bash
git branch --show-current
```

The normal branch should be:

```text
main
```

Check for local changes.

```bash
git status
```

### 17.2 Pull the latest code safely

For the normal case where there are no local code changes:

```bash
git pull --ff-only origin main
```

If this works, continue to the restart/update steps below.

### 17.3 If Git pull is blocked by local changes

If Git says local changes would be overwritten, review the changed files first.

```bash
git status
```

Show what changed.

```bash
git diff
```

If the changes are not needed and you want the instance to follow GitHub exactly, restore only the affected tracked files.

Example:

```bash
git restore nginx/certs/generate-localhost-cert.sh
git restore vault/bootstrap/generate-secrets.sh
```

Then pull again.

```bash
git pull --ff-only origin main
```

If `.openkb-venv/` appears as an untracked folder, it is only the temporary OpenKB initialization environment. After `openkb-data/.openkb/` exists, `.openkb-venv/` can be removed.

Do not blindly delete these important local files or folders:

```text
.env
vault/bootstrap/djopenkb.env
vault/keys/
vault/file/
openkb-data/
postgres-data/
nginx/certs/localhost.key
```

### 17.4 Quick restart after small code changes

For small Python/template changes where dependencies and Dockerfile did not change:

```bash
sudo docker compose restart web
```

Then check:

```bash
sudo docker compose exec web python manage.py check
sudo docker compose logs --tail=100 web
```

### 17.5 Normal update after pulling new code

This is the recommended general update flow after `git pull`.

```bash
sudo docker compose up -d --build
sudo docker compose exec web python manage.py migrate
sudo docker compose exec web python manage.py collectstatic --noinput
sudo docker compose exec web python manage.py compilemessages
sudo docker compose exec web python manage.py sync_openkb_ai
sudo docker compose exec web python manage.py check --deploy
sudo docker compose ps
```

### 17.6 When to use each update command

| Situation | Recommended command |
|---|---|
| Only template or small Python view changes | `sudo docker compose restart web` |
| Python dependencies changed in `requirements.txt` | `sudo docker compose up -d --build` |
| Dockerfile or Docker Compose changed | `sudo docker compose up -d --build` |
| Database model or migration changed | `sudo docker compose exec web python manage.py migrate` |
| Static files changed | `sudo docker compose exec web python manage.py collectstatic --noinput` |
| Translation `.po` files changed | `sudo docker compose exec web python manage.py compilemessages` |
| OpenKB article sync logic changed | `sudo docker compose exec web python manage.py sync_openkb_ai` |
| Nginx config or certificate changed | `sudo docker compose restart nginx` |
| Cleanup scheduler script changed | `sudo docker compose restart cleanup-scheduler` |

### 17.7 Re-initialise OpenKB if needed

Normally, you do not need to re-initialise OpenKB after every update.

Only repeat the temporary OpenKB initialization steps if the `openkb-data/.openkb/` folder is missing or damaged.

```bash
cd /opt/DjOpenKB
python3 -m venv .openkb-venv
source .openkb-venv/bin/activate
python -m pip install --upgrade pip
pip install -e OpenKB-main
mkdir -p openkb-data
cd openkb-data
openkb init
cd /opt/DjOpenKB
deactivate
rm -rf .openkb-venv
```

After code or article changes, usually only sync published Django articles:

```bash
sudo docker compose exec web python manage.py sync_openkb_ai
```

### 17.8 Confirm the update is healthy

```bash
sudo docker compose ps
sudo docker compose exec web python manage.py check
sudo docker compose logs --tail=100 web
sudo docker compose logs --tail=100 nginx
```

Open the website again:

```text
https://<linux-server-ip>:8080
```

---

## 17.9 Article workflow and bulk import/export notes

### Published article updates require approval

Normal users can edit their own published articles, but the live published article is not overwritten immediately. The edited version is saved as a pending update and waits for admin review.

```text
Current published version: remains visible to readers
Edited version: stored separately as pending update
Admin approves: pending update replaces the published article
Admin rejects: published article remains unchanged and feedback is shown to the author
```

Admins can review both new pending articles and pending updates from the **Manage Pending Articles** admin tool.

### Bulk export/import purpose

The **Bulk import/export articles** admin tool creates article backup or migration ZIP files. Exported ZIP files contain actual article content, not just article names. They can include:

```text
article titles
article body / Markdown
keywords
article workflow status
pending update content and pending update keywords
review comments/history where applicable
referenced image files
OpenKB sync metadata
```

Exporting does not delete, move, or unpublish existing articles. It only downloads an administrator backup copy.

### Export splitting and import limits

The import upload limit is 100 MB per ZIP file. To keep exported files importable, split export targets about 95 MB per part.

```text
Recommended maximum import ZIP size: 100 MB
Export split target: about 95 MB per part
Uncompressed import safety limit: about 200 MB
Article image upload limit: 2 MB per image
```

If a split export is downloaded, it is an outer package containing several inner part ZIP files, for example:

```text
djopenkb_articles_export_part_001.zip
djopenkb_articles_export_part_002.zip
djopenkb_articles_export_part_003.zip
```

To restore a split export:

```text
1. Extract the outer split package ZIP.
2. Go to the admin Bulk import/export articles page.
3. Import each inner part ZIP one by one.
4. Confirm the imported published articles appear on the website.
5. Run the OpenKB sync command if needed.
```

After a large import, run:

```bash
sudo docker compose exec web python manage.py sync_openkb_ai
sudo docker compose exec web python manage.py check
```

### Homepage search, tabs, and article count setting

The main search is intentionally simple. It matches only published article titles and manually entered article keywords. It does not search article body content, Markdown files, internal paths, or relevance scores.

The homepage article panel uses three paginated tabs:

```text
Trending Topics
Most Liked
Most Recent Articles
```

The number of articles shown per page is controlled in:

```text
Django Admin → KB → Site settings → Articles per page
```

Valid range:

```text
Minimum: 5
Maximum: 100
Default: 10
```

### Manual existing-keyword suggestions

When adding or editing an article, users can click **Refresh** in the Suggested keywords area. The browser scans the current title/body and shows only keywords that already exist on published articles and exactly appear in the current draft.

This behaviour deliberately avoids:

```text
built-in keyword lists
AI guessing
similarity scores
usage-count badges
filler-word filtering
```

Keyword chips scroll horizontally so the article form does not grow too tall.

## 17.10 Authentication lockout and production rate-limit notes

### Admin-configurable password/MFA lockout policy

Password and MFA lockout policy is managed from Django Admin:

```text
Django Admin -> Site settings -> Authentication lockout policy stages
```

Each stage can define:

```text
Failed attempts required
Block duration
Repeat count
Sort order
Enabled/disabled state
```

Default simplified policy after migration:

| Stage | Failed attempts | Block duration | Repeat count |
|---|---:|---:|---:|
| 1 | 10 | 5 minutes | 2 |
| 2 | 5 | 15 minutes | 2 |
| 3 | 3 | 1 hour | repeat forever |

`repeat_count=0` means the stage repeats forever. The last stage uses `repeat_count=0`, so repeated attacks continue receiving a 1-hour block instead of escalating to a full-day block.

The policy no longer uses a separate failure time window. Failed counters stay active until successful password login/MFA verification, an administrator reset, or the lockout escalation memory expiry (`AUTH_LOCKOUT_STRIKE_TTL_SECONDS`, default 7 days).

Successful password login resets the password lockout history. Successful MFA verification resets the MFA lockout history. Administrators can manually reset lockout state from the Django Admin user/profile actions.

### Redis-backed AI and authentication protection

Production deployments should keep Redis enabled:

```env
REDIS_URL=redis://redis:6379/1
DJANGO_ALLOW_LOCAL_CACHE_FALLBACK=false
```

Redis is used so password/MFA lockout counters, AI rate limits, and AI concurrency counters are shared across all Gunicorn workers. Without Redis, each worker would have its own local-memory counters, which is not suitable for production.

## 18. Files not to share

Do not commit or share these files/folders:

```text
.env
.env.*
!.env.example
vault/bootstrap/djopenkb.env
vault/keys/
vault/file/
openkb-data/.openkb/
.openkb-venv/
ldap-certs/
nginx/certs/*.key
postgres-data/
exported article ZIP backups
```

The public repository should only contain examples, scripts, and safe default configuration.

---


Bulk article export ZIP files should also be treated as sensitive because they can contain internal article content, keywords, pending updates, review notes, and uploaded images. Store them only in approved backup locations.

## 19. Troubleshooting quick notes

### Docker permission denied

Use:

```bash
sudo docker compose ...
```

or add the user to the Docker group and log out/in:

```bash
sudo usermod -aG docker $USER
```

### `python: not found`

Install Python alias support:

```bash
sudo apt install -y python-is-python3
```

### OpenKB command not found

Create or activate the temporary OpenKB initialization virtual environment:

```bash
cd /opt/DjOpenKB
source .openkb-venv/bin/activate
```

Then check:

```bash
openkb --help
```

If still missing:

```bash
pip install -e OpenKB-main
```

Or use:

```bash
cd /opt/DjOpenKB/openkb-data
PYTHONPATH=../OpenKB-main python -m openkb.cli init
```

### OpenKB data folder not found

Initialize OpenKB locally on the Linux host using a temporary virtual environment:

```bash
cd /opt/DjOpenKB
python3 -m venv .openkb-venv
source .openkb-venv/bin/activate
python -m pip install --upgrade pip
pip install -e OpenKB-main
mkdir -p openkb-data
cd openkb-data
openkb init
cd /opt/DjOpenKB
deactivate
rm -rf .openkb-venv
sudo docker compose restart web cleanup-scheduler
sudo docker compose exec web python manage.py sync_openkb_ai
```

### Nginx certificate file not found

Nginx expects:

```text
nginx/certs/localhost.crt
nginx/certs/localhost.key
```

Regenerate the certificate.

```bash
chmod +x nginx/certs/generate-localhost-cert.sh
./nginx/certs/generate-localhost-cert.sh
ls -l nginx/certs/localhost.crt nginx/certs/localhost.key
sudo docker compose restart nginx
```

### Vault init failed because of bootstrap syntax

Check the bootstrap file.

```bash
nl -ba vault/bootstrap/djopenkb.env
```

Make sure values are valid `KEY=VALUE` lines.

### Postgres password changed accidentally

If the database already exists, changing `POSTGRES_PASSWORD` in Vault alone is not enough. The password stored in Vault must match the real password inside PostgreSQL.

Use the safest option first:

```text
1. Restore the original POSTGRES_PASSWORD value in vault/bootstrap/djopenkb.env.
2. Re-seed Vault.
3. Restart the web and database services.
```

Only change the PostgreSQL password inside the database if you intentionally want to rotate it.

### OpenKB chatbot errors

First confirm OpenKB was initialized locally:

```bash
ls -la /opt/DjOpenKB/openkb-data
ls -la /opt/DjOpenKB/openkb-data/.openkb
sudo docker compose exec web ls -la /app/openkb-data
sudo docker compose exec web ls -la /app/openkb-data/.openkb
```

Then sync Django articles:

```bash
sudo docker compose exec web python manage.py sync_openkb_ai
```


### Redis required in production

When `DJANGO_DEBUG=false`, the web container expects Redis to be available unless `DJANGO_ALLOW_LOCAL_CACHE_FALLBACK=true` is explicitly set. For production, do not enable local-cache fallback. Check Redis status with:

```bash
sudo docker compose ps redis
sudo docker compose logs redis --tail=80
```

If Redis is not running, restart the stack:

```bash
sudo docker compose up -d redis
sudo docker compose restart web cleanup-scheduler
```

### User blocked by password or MFA lockout

If a user is blocked because of repeated wrong password or MFA attempts, an administrator can reset the lockout state from Django Admin:

```text
Django Admin -> Users -> open user -> Authentication lockout -> Reset password/MFA lockout
```

Admins can also use the bulk action on selected users or profiles.
