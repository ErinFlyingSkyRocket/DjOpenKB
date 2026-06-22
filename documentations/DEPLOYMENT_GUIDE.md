# DjOpenKB Deployment and Operations Guide

This guide is for Linux administrators who install, run, update, back up, and troubleshoot DjOpenKB.

It intentionally covers **deployment and day-to-day service operations only**. Application workflows, article lifecycle details, security controls, and role permissions are documented separately in `documentations/FULL_FEATURE_DOCUMENTATION.md`.

## 1. Deployment scope and command convention

### Assumptions

- Ubuntu/Debian-style Linux host.
- Project directory: `/opt/DjOpenKB`.
- Docker Compose services: `vault`, `vault-init`, `vault-auto-unseal`, `db`, `redis`, `web`, `nginx`, and `cleanup-scheduler`.
- Nginx exposes HTTPS on port `8080`.
- The initial certificate is self-signed. Use a certificate trusted by intended client devices for an internet-facing deployment.
- The bundled OpenKB source is in `OpenKB-main/`.

Commands below use `sudo docker compose`. If the Linux administrator is already in the Docker group, `sudo` may be omitted.

```bash
sudo usermod -aG docker $USER
```

Log out and back in before using Docker without `sudo`.

### Persistent deployment state

Do not delete, commit, attach, or include the following files/folders in a public repository, issue, email, or shared project ZIP:

```text
.env
vault/bootstrap/djopenkb.env
vault/file/
vault/keys/
vault/logs/
postgres-data/
openkb-data/
openkb-data-internal/
nginx/certs/localhost.key
exported article ZIP files
SQL backups
```

These items contain secrets, database data, AI index data, TLS private keys, or audit-sensitive application state. For a recovery that preserves chatbot knowledge, back up `openkb-data/` and `openkb-data-internal/` together with the database.

---

## 2. Prepare the Linux host

Install system packages and enable Docker:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y \
  git curl ca-certificates openssl nano unzip \
  python3 python-is-python3 python3-venv \
  docker.io docker-compose-v2
sudo systemctl enable --now docker
```

Verify the installation:

```bash
docker --version
docker compose version
sudo systemctl status docker --no-pager
```

If UFW is enabled, allow the published HTTPS port only from networks that should reach the service:

```bash
sudo ufw allow 8080/tcp
sudo ufw status verbose
```

Use cloud firewall/security-group controls as well. Do not expose Vault port `8200`, PostgreSQL port `5432`, Redis port `6379`, or Gunicorn port `8000` to the network.

---

## 3. Obtain the project

For a new deployment:

```bash
sudo mkdir -p /opt
cd /opt
sudo git clone https://github.com/ErinFlyingSkyRocket/DjOpenKB.git
sudo chown -R "$USER":"$USER" /opt/DjOpenKB
cd /opt/DjOpenKB
```

For an existing deployment:

```bash
cd /opt/DjOpenKB
git status
git branch --show-current
```

Confirm the main files exist:

```bash
ls docker-compose.yml manage.py .env.example
ls djopenkb kb nginx vault OpenKB-main documentations
```

---

## 4. Configure `.env` (non-secret settings)

Create the runtime environment file:

```bash
cd /opt/DjOpenKB
cp .env.example .env
chmod 600 .env
nano .env
```

`.env` must contain **non-secret configuration only**. Put passwords, API keys, the Django secret key, and the encryption key in `vault/bootstrap/djopenkb.env` during first-time Vault seeding.

### 4.1 Example public server values

This example assumes users browse to `https://kb.example.com:8080` and the Linux host has IP address `198.51.100.25`.

```env
DJANGO_DEBUG=false

# Include every hostname or IP address users may enter in the browser.
# Do not include https:// or a port here.
DJANGO_ALLOWED_HOSTS=kb.example.com,198.51.100.25,localhost,127.0.0.1,web,nginx

# Use the exact browser origins, including https:// and :8080 when users use port 8080.
DJANGO_CSRF_TRUSTED_ORIGINS=https://kb.example.com:8080,https://198.51.100.25:8080,https://localhost:8080,https://127.0.0.1:8080

MFA_TOTP_ISSUER=Knowledge Repository
MFA_TOTP_VALID_WINDOW=2

POSTGRES_DB=djopenkb
POSTGRES_USER=djopenkb
POSTGRES_HOST=db
POSTGRES_PORT=5432

REDIS_URL=redis://redis:6379/1
DJANGO_ALLOW_LOCAL_CACHE_FALLBACK=false

USE_SQLITE=false
VAULT_KV_MOUNT=secret
VAULT_SECRET_PATH=djopenkb
VAULT_AUTO_UNSEAL_INTERVAL_SECONDS=15
```

Use `hostname -I` to identify the Linux host IP address:

```bash
hostname -I
```

If a reverse proxy later presents the service publicly on standard HTTPS port `443`, add that exact origin too, for example:

```env
DJANGO_CSRF_TRUSTED_ORIGINS=https://kb.example.com,https://kb.example.com:8080
```

Keep `DJANGO_ALLOW_LOCAL_CACHE_FALLBACK=false` when `DJANGO_DEBUG=false`. Redis is required for shared rate limiting, lockout handling, and AI concurrency controls across Gunicorn workers.

### 4.2 OpenKB and AI runtime values

Use this standard OpenKB configuration:

```env
OPENKB_BASE_DIR=OpenKB-main
OPENKB_DATA_DIR=openkb-data
OPENKB_INTERNAL_DATA_DIR=openkb-data-internal
OPENKB_AI_PROVIDER=openkb-cli
OPENKB_AI_MODEL=gemini/gemini-2.5-flash
LITELLM_DROP_PARAMS=true

OPENKB_AI_MAX_PROMPT_CHARS=1000
OPENKB_AI_RATE_LIMIT_MAX_REQUESTS=5
OPENKB_AI_RATE_LIMIT_WINDOW_SECONDS=60
OPENKB_AI_RATE_LIMIT_BLOCK_SECONDS=1800
OPENKB_AI_TIMEOUT_SECONDS=90
OPENKB_AI_CONCURRENCY_LIMIT=2
OPENKB_AI_CONCURRENCY_LOCK_SECONDS=120
```

The model is selected with `OPENKB_AI_MODEL`. The API key remains in Vault, not `.env`.

| Provider example | Example `OPENKB_AI_MODEL` | Bootstrap key supported by the current Vault script |
|---|---|---|
| Google Gemini Flash | `gemini/gemini-2.5-flash` | `AI_API_KEY=<Google AI key>` |
| Google Gemini Pro | `gemini/gemini-2.5-pro` | `AI_API_KEY=<Google AI key>` |
| OpenAI | `openai/gpt-5.5` | `AI_API_KEY=<OpenAI API key>` |
| Anthropic Claude Haiku | `anthropic/claude-3-5-haiku-latest` | `AI_API_KEY=<Anthropic API key>` |
| Anthropic Claude Sonnet | `anthropic/claude-3-5-sonnet-latest` | `AI_API_KEY=<Anthropic API key>` |
| OpenRouter | `openrouter/openai/gpt-4o-mini` | `AI_API_KEY=<OpenRouter API key>` |
| Groq | `groq/llama-3.1-8b-instant` | `AI_API_KEY=<Groq API key>` |
| Mistral | `mistral/mistral-small-latest` | `AI_API_KEY=<Mistral API key>` |
| Cohere | `cohere/command-r` | `AI_API_KEY=<Cohere API key>` |
| Local Ollama | `ollama/llama3.1` | Provider/local-runtime-specific setup required |

Use only model strings supported by the installed OpenKB/LiteLLM version and by the selected provider account. Test a model change in a controlled environment before making it available to users.

**Current implementation note:** Django reads `AI_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY`. However, the current `vault/scripts/init.sh` writes only `AI_API_KEY` into Vault during its normal bootstrap process. Therefore, use `AI_API_KEY` as the supported standard for all provider choices unless the Vault init script is deliberately extended and tested to store provider-specific keys.

### 4.3 Optional cleanup interval

The cleanup service defaults to one run every 24 hours. To change it, add this optional value to `.env`:

```env
CLEANUP_INTERVAL_SECONDS=86400
```

Do not set an unusually short interval on a production host unless there is a clear operational reason.

---

## 5. Configure Nginx host details and administrator network access

Edit `nginx/nginx.conf` before exposing the service to users:

```bash
cd /opt/DjOpenKB
nano nginx/nginx.conf
```

### 5.1 Server name

Update the `server_name` line to include the intended DNS name and server IP, for example:

```nginx
server_name kb.example.com 198.51.100.25 localhost 127.0.0.1;
```

### 5.2 Django Admin network allowlist

The Nginx `geo $djopenkb_admin_network_allowed` block is an outer access control for `/admin/`. Replace the sample management range with the real management subnet, for example:

```nginx
geo $djopenkb_admin_network_allowed {
    default 0;
    10.20.30.0/24 1;
    127.0.0.1/32 1;
    ::1/128 1;
}
```

Do not use `0.0.0.0/0` for the administrator allowlist. After an Nginx change:

```bash
sudo docker compose restart nginx
sudo docker compose logs --tail=80 nginx
```

---

## 6. Configure Vault bootstrap secrets

Vault is used for Django, database, LDAP, and AI secrets. The bootstrap file is read only during first-time setup or an intentional secret update.

Create it from the example and generate strong local values:

```bash
cd /opt/DjOpenKB
cp vault/bootstrap/djopenkb.env.example vault/bootstrap/djopenkb.env
chmod 600 vault/bootstrap/djopenkb.env
chmod +x vault/bootstrap/generate-secrets.sh
./vault/bootstrap/generate-secrets.sh
nano vault/bootstrap/djopenkb.env
```

For a Gemini example, the important values look like this. Do not copy placeholder values literally:

```env
DJANGO_SECRET_KEY=<generated-by-script>
DJANGO_FIELD_ENCRYPTION_KEY=<generated-by-script>
POSTGRES_PASSWORD=<generated-by-script>

AI_API_KEY=<your-google-ai-api-key>
LDAP_BIND_PASSWORD=<service-account-password>
LDAP_PLACEHOLDER_PASSWORD=<generated-by-script>
```

For OpenAI, Anthropic, OpenRouter, Groq, Mistral, or Cohere, keep the same key name and replace only the value:

```env
AI_API_KEY=<the-api-key-for-the-model-selected-in-OPENKB_AI_MODEL>
```

Important rules:

- Never store any secret in `.env`, Git, screenshots, documentation, tickets, or shared ZIP files.
- Do not casually rotate `DJANGO_SECRET_KEY`; signed values and sessions can be affected.
- Do not casually rotate `DJANGO_FIELD_ENCRYPTION_KEY`; existing encrypted MFA data can become unreadable.
- Do not change `POSTGRES_PASSWORD` on an existing database unless the PostgreSQL password is rotated in a coordinated maintenance procedure.
- The bootstrap file uses `KEY=value` format with no spaces around `=`.
- For an LDAP password that contains shell-special characters, use single quotes, for example `LDAP_BIND_PASSWORD='Example!Password$123'`.

After Vault has seeded successfully, remove `vault/bootstrap/djopenkb.env` from any source package or exported backup copy. Retain it only in a protected administrator location if local policy permits.

---

## 7. Configure LDAP or LDAPS (optional)

When Active Directory is not used, keep LDAP disabled:

```env
LDAP_ENABLED=false
LDAP_PLACEHOLDER_ENABLED=false
LDAP_PLACEHOLDER_AUTO_CREATE_USERS=false
```

### 7.1 LDAPS example using a Domain Controller

This example uses:

```text
AD DNS / UPN domain: ad.example.com
Domain Controller FQDN: dc01.ad.example.com
Domain Controller IP: 10.20.30.10
NetBIOS domain: AD
Service account: svc_djopenkb@ad.example.com
Search base: DC=ad,DC=example,DC=com
```

Add the following non-secret values to `.env`:

```env
LDAP_ENABLED=true
LDAP_PLACEHOLDER_ENABLED=false
LDAP_PLACEHOLDER_AUTO_CREATE_USERS=false

# Use the Domain Controller FQDN, not its IP address. The FQDN must match
# the LDAPS certificate name/SAN presented by the Domain Controller.
LDAP_SERVER_URI=ldaps://dc01.ad.example.com:636
LDAP_START_TLS=false
LDAP_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/ad-ca.crt
LDAP_TLS_REQUIRE_CERT=demand
LDAP_ALLOW_INSECURE=false
LDAP_NETWORK_TIMEOUT=5
LDAP_OPERATION_TIMEOUT=5

# AD/UPN domain and NetBIOS domain.
LDAP_AD_DOMAIN=ad.example.com
LDAP_NETBIOS_DOMAIN=AD
LDAP_ALLOWED_EMAIL_DOMAINS=ad.example.com,example.com

# AD directory search location and supported username/email forms.
LDAP_USER_SEARCH_BASE=DC=ad,DC=example,DC=com
LDAP_USER_FILTER=(|(sAMAccountName=%(user)s)(userPrincipalName=%(user)s)(userPrincipalName=%(user)s@ad.example.com)(mail=%(user)s)(mail=%(user)s@ad.example.com)(mail=%(user)s@example.com)(userPrincipalName=%(user)s@example.com))

# Low-privilege service account used only to bind and search.
LDAP_BIND_DN=svc_djopenkb@ad.example.com

# Set these to the actual DC values so Docker can resolve the FQDN even where
# internal DNS is not available inside containers.
LDAP_EXTRA_HOSTNAME=dc01.ad.example.com
LDAP_EXTRA_SHORT_HOSTNAME=dc01
LDAP_DC_IP=10.20.30.10
```

Place only the public AD issuing CA certificate in the project. Do not copy a Domain Controller private key, PFX file, or full credential bundle into DjOpenKB.

```bash
cd /opt/DjOpenKB
sudo install -d -m 755 ldap-certs
sudo install -m 644 /path/to/ad-issuing-ca.crt ldap-certs/ad-ca.crt
openssl x509 -in ldap-certs/ad-ca.crt -noout -subject -issuer -dates
```

If the certificate export is DER/binary instead of PEM/Base-64:

```bash
cd /opt/DjOpenKB
openssl x509 -inform DER -in ldap-certs/ad-ca.crt -out ldap-certs/ad-ca.pem
mv ldap-certs/ad-ca.crt ldap-certs/ad-ca.der.bak
mv ldap-certs/ad-ca.pem ldap-certs/ad-ca.crt
```

Add the service-account password to `vault/bootstrap/djopenkb.env`:

```env
LDAP_BIND_PASSWORD=<password-for-svc_djopenkb@ad.example.com>
```

Start the stack, then verify TLS and search/bind connectivity without exposing a user password:

```bash
sudo docker compose exec web python scripts/test_ldaps_tls.py
sudo docker compose exec web python manage.py test_ldap_auth alice@ad.example.com
```

To test a user sign-in interactively, use the command below only from a protected administrator terminal. It prompts for the user's AD password instead of placing it on the command line:

```bash
sudo docker compose exec -it web python manage.py test_ldap_auth alice@ad.example.com --auth
```

For Windows Server certificate setup and AD prerequisites, refer to:

```text
documentations/LDAP_LDAPS_SETUP.md
documentations/WINDOWS_SERVER_2022_AD_LDAPS_SETUP.md
```

---

## 8. Configure HTTPS certificates

The supplied script creates a local self-signed certificate:

```bash
cd /opt/DjOpenKB
chmod +x nginx/certs/generate-localhost-cert.sh
./nginx/certs/generate-localhost-cert.sh
chmod 644 nginx/certs/localhost.crt
chmod 600 nginx/certs/localhost.key
```

The generated certificate is intended for local/lab use and will generate browser warnings for normal public DNS names. For an internet-facing service, replace these files with a certificate and private key trusted by the intended devices, keeping the paths expected by `nginx/nginx.conf`:

```text
nginx/certs/localhost.crt
nginx/certs/localhost.key
```

Then restart Nginx:

```bash
sudo docker compose restart nginx
```

---

## 9. Initialise the local OpenKB data directory (required once)

DjOpenKB packages the OpenKB source in `OpenKB-main/`, and the Docker image installs it for the running `web` and `cleanup-scheduler` services. However, a **fresh server still needs the public OpenKB data directory initialised locally** before the Django AI sync can build and query the knowledge base.

This one-time host step creates the required OpenKB layout under:

```text
/opt/DjOpenKB/openkb-data/
├── .openkb/config.yaml
├── .openkb/hashes.json
├── raw/
└── wiki/
```

Without `openkb-data/.openkb/config.yaml`, the OpenKB chatbot may fail because there is no OpenKB knowledge-base configuration to query.

> Run this section once for a new server, or again only if `openkb-data/.openkb/` is missing or deliberately rebuilt. Do not run `openkb init` over a healthy existing `openkb-data/` directory.

### 9.1 Create a temporary host virtual environment

Run these commands on the Linux host, not inside a Docker container:

```bash
cd /opt/DjOpenKB
python3 -m venv .openkb-venv
source .openkb-venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ./OpenKB-main
```

The virtual environment is only used to run the initial OpenKB CLI command on the host. The runtime Docker image installs the bundled package separately through the project `Dockerfile`.

### 9.2 Initialise `openkb-data`

```bash
cd /opt/DjOpenKB
mkdir -p openkb-data
cd openkb-data
openkb init
```

OpenKB will prompt for two values:

1. **Model** — enter the exact model configured in `/opt/DjOpenKB/.env` as `OPENKB_AI_MODEL`.
2. **LLM API Key** — press **Enter** and leave it blank.

For example, when `.env` contains:

```env
OPENKB_AI_MODEL=gemini/gemini-2.5-flash
```

enter:

```text
gemini/gemini-2.5-flash
```

at the model prompt, then press Enter at the API-key prompt.

The production chatbot receives its API key from Vault through Django at query time. Leave the OpenKB API-key prompt blank for this deployment. If OpenKB still creates `openkb-data/.env`, it is local generated state and is ignored by Git; it does not need to be removed just to run future `git pull` updates.

If the `openkb` command is not found, use the module form while the virtual environment is still active:

```bash
cd /opt/DjOpenKB/openkb-data
PYTHONPATH=/opt/DjOpenKB/OpenKB-main python -m openkb.cli init
```

### 9.3 Verify the OpenKB setup and keep the local setup state

Verify that the expected files/folders now exist:

```bash
cd /opt/DjOpenKB
ls -la openkb-data
ls -la openkb-data/.openkb
cat openkb-data/.openkb/config.yaml
```

Expected items include:

```text
openkb-data/.openkb/config.yaml
openkb-data/.openkb/hashes.json
openkb-data/raw/
openkb-data/wiki/
```

The `model:` entry in `openkb-data/.openkb/config.yaml` should match `OPENKB_AI_MODEL` in `.env`.

Keep the locally generated OpenKB files and the temporary `.openkb-venv/` directory in place. They are needed only on the host for local OpenKB maintenance, but they do **not** normally affect source updates:

```text
openkb-data/.openkb/
openkb-data/raw/
openkb-data/wiki/
openkb-data/.env              (if OpenKB created it)
.openkb-venv/
```

The project `.gitignore` ignores generated `openkb-data/` content and `.openkb-venv/`, so these files do not normally appear in `git status` and are not changed by a normal `git pull`. Do not remove them merely to update the project.

For security hygiene, leave the OpenKB API-key prompt blank and use the Vault-managed application key. If a provider key was intentionally entered into `openkb-data/.env`, keep that file protected with Linux permissions; its presence still does not block `git pull`.

Optional hardening for the local OpenKB directory:

```bash
cd /opt/DjOpenKB
chmod 700 openkb-data
```

### 9.4 Internal OpenKB data directory

Do **not** normally run `openkb init` manually in `openkb-data-internal/`.

When you run:

```bash
sudo docker compose exec web python manage.py sync_openkb_ai --scope internal
```

DjOpenKB creates `openkb-data-internal/` as needed and copies the public OpenKB configuration model into its internal runtime tree. This keeps the public and internal AI indexes separate while using the same selected model.

After the Docker stack is running, rebuild both indexes and verify isolation:

```bash
sudo docker compose exec web python manage.py sync_openkb_ai --scope all
sudo docker compose exec web python manage.py check_internal_article_isolation --sync-first
```

## 10. Start the stack and complete first-time setup

Build and start the services:

```bash
cd /opt/DjOpenKB
sudo docker compose up -d --build
sudo docker compose ps
```

Expected persistent services:

```text
vault
vault-auto-unseal
redis
db
web
nginx
cleanup-scheduler
```

`vault-init` is a one-time container and normally exits successfully after Vault is initialized, unsealed, and seeded.

Check logs before creating users:

```bash
sudo docker compose logs --tail=150 vault-init
sudo docker compose logs --tail=150 web
sudo docker compose logs --tail=100 nginx
sudo docker compose logs --tail=100 db
```

Run health checks:

```bash
sudo docker compose exec web python manage.py check
sudo docker compose exec web python manage.py check --deploy
sudo docker compose exec web python manage.py migrate --noinput
sudo docker compose exec web python manage.py collectstatic --noinput
```

Create the first local Django administrator:

```bash
sudo docker compose exec web python manage.py createsuperuser
```

The command prompts for username, email, and password. The administrator must complete normal MFA enrolment after first signing in through the main login page.

Ensure DjOpenKB role groups are present and assign any existing users without a role group to an appropriate default role:

```bash
sudo docker compose exec web python manage.py seed_djopenkb_roles --assign-missing-users
```

After completing the one-time OpenKB initialisation in Section 9 and after published articles exist, build both AI indexes:

```bash
sudo docker compose exec web python manage.py sync_openkb_ai --scope all
sudo docker compose exec web python manage.py check_internal_article_isolation --sync-first
```

Basic browser/crawler checks from the host:

```bash
curl -k https://127.0.0.1:8080/robots.txt
curl -k -I https://127.0.0.1:8080/login/
```

Expected results include:

```text
User-agent: *
Disallow: /

X-Robots-Tag: noindex, nofollow, noarchive, nosnippet, noimageindex
```

Open the service in a browser:

```text
https://kb.example.com:8080
```

The default Django `/admin/login/` route is intentionally hidden. Sign in through the main application login, complete MFA, then enter Django Admin from the application navigation while connected from an allowed management network.

### 10.1 First administrator sign-in

After the `createsuperuser` command succeeds, complete the initial administrator setup through the browser:

1. Browse to `https://kb.example.com:8080` (replace with the deployed address).
2. Sign in with the local superuser username and password created above.
3. Complete the normal MFA enrolment and verify the code.
4. Confirm the browser/client IP is included in the Nginx Django Admin allowlist from Section 5.2.
5. Use the **Admin** navigation entry, complete the separate Admin MFA prompt, then open Django Admin.

Do not try to browse directly to `/admin/login/`; that route is intentionally hidden. If the Admin navigation does not open, first check the Nginx administrator network allowlist and the web/nginx logs.

---

## 11. Normal Docker service operations

Run these commands from `/opt/DjOpenKB`.

### Status and logs

```bash
sudo docker compose ps
sudo docker compose logs --tail=120 web
sudo docker compose logs --tail=120 nginx
sudo docker compose logs --tail=120 db
sudo docker compose logs --tail=120 redis
sudo docker compose logs --tail=120 vault
sudo docker compose logs --tail=120 vault-auto-unseal
sudo docker compose logs --tail=120 cleanup-scheduler
```

Follow a live service log:

```bash
sudo docker compose logs -f web
```

### Start, stop, rebuild, and restart

```bash
# Start existing containers.
sudo docker compose up -d

# Rebuild images and start services after Python, dependency, Docker, or Nginx changes.
sudo docker compose up -d --build

# Stop containers but preserve mounted deployment data and named volumes.
sudo docker compose down

# Restart only Django after a small template or Python change.
sudo docker compose restart web

# Restart Nginx after its configuration or certificate changes.
sudo docker compose restart nginx

# Restart scheduled cleanup after scheduler changes.
sudo docker compose restart cleanup-scheduler

# Restart both application-related services.
sudo docker compose restart web cleanup-scheduler
```

Do **not** use `sudo docker compose down -v` as routine maintenance. Do not delete `postgres-data/`, `vault/file/`, `vault/keys/`, `openkb-data/`, or `openkb-data-internal/` unless intentionally rebuilding a disposable test deployment.

### Enter a container shell

```bash
sudo docker compose exec web sh
sudo docker compose exec db sh
sudo docker compose exec vault sh
```

Exit with:

```bash
exit
```

---

## 12. Django operational commands

```bash
# Apply migrations manually when required.
sudo docker compose exec web python manage.py migrate --noinput

# Check Django configuration and deployment settings.
sudo docker compose exec web python manage.py check
sudo docker compose exec web python manage.py check --deploy

# Rebuild static files.
sudo docker compose exec web python manage.py collectstatic --noinput

# Compile locale files after changing .po translation files.
sudo docker compose exec web python manage.py compilemessages

# Open an interactive Django shell.
sudo docker compose exec -it web python manage.py shell

# Create another local Django administrator.
sudo docker compose exec web python manage.py createsuperuser

# Create/update the predefined DjOpenKB role groups.
sudo docker compose exec web python manage.py seed_djopenkb_roles

# Assign role groups to users that currently have none.
sudo docker compose exec web python manage.py seed_djopenkb_roles --assign-missing-users

# Inspect MFA diagnostics without printing the stored MFA secret.
sudo docker compose exec web python manage.py diagnose_mfa <username-or-email>

# Reset one user's MFA in a controlled administrator action.
sudo docker compose exec web python manage.py reset_user_mfa <username-or-email> --yes
```

Use Django Admin or supported management commands for application data. Avoid direct database writes to Django tables unless a documented recovery procedure explicitly requires it.

### 12.1 User-account maintenance in Django Admin

Use Django Admin for normal account administration. This preserves the built-in Django Admin audit entry and the DjOpenKB append-only Admin activity-log record. Do not add, disable, or delete users directly in PostgreSQL.

#### Open the Users page

1. Sign in through the main application login and complete normal MFA.
2. Select **Admin**, complete Admin MFA, and enter Django Admin from an allowed administrator network.
3. Open **Authentication and Authorization → Users**.

The user list supports username/email search and filters for active status, authentication source, and MFA setup state.

#### Create local accounts

Use `createsuperuser` for the first or emergency replacement Django administrator:

```bash
cd /opt/DjOpenKB
sudo docker compose exec web python manage.py createsuperuser
```

For an ordinary local user, use **Users → Add user** in Django Admin. Set the local password, save the user, then assign the required DjOpenKB group from the user change page or an approved bulk action. New non-admin local accounts receive the normal fallback role automatically.

For Active Directory users, do **not** create a duplicate local Django account with the same username. The user should first sign in through the main login using Active Directory; DjOpenKB then creates/synchronises the local profile. Afterwards, use Django Admin to assign the required role groups or perform support actions such as MFA reset.

Role capability descriptions are intentionally kept in `documentations/FULL_FEATURE_DOCUMENTATION.md`; this deployment guide documents only how to operate the service.

#### Disable or re-enable a user

For normal offboarding or temporary suspension, prefer disabling rather than deleting the account:

1. In **Users**, select the account.
2. Choose the action **Set selected users as Disabled User**.
3. Apply the action and confirm the result in **Admin activity logs**.

This retains historical ownership and audit information while blocking Knowledge Repository access. The Disabled User role also removes Django Admin access from that account.

To re-enable the account, select it and assign the required normal role through an approved action or its group membership. If the account was separately marked inactive, open the user record and re-enable the **Active** checkbox as well. Do not re-enable a user until the access request has been authorised.

For an LDAP/Active Directory account, disabling access inside DjOpenKB does not disable the source AD account. Disable the account in Active Directory as well when organisation policy requires complete offboarding.

#### Reset MFA or clear a login lockout

From the selected user change page, use the supported **Reset MFA** or **Reset password/MFA lockout** control. The matching bulk actions are also available on the Users list. These actions are logged and do not reveal the user's MFA secret.

The equivalent controlled shell commands are:

```bash
sudo docker compose exec web python manage.py reset_user_mfa <username-or-email> --yes
sudo docker compose exec web python manage.py diagnose_mfa <username-or-email>
```

#### Permanently delete a user

Permanent deletion should be exceptional. Use it only after checking that a disabled account is not sufficient and after ensuring there is at least one other working administrator account.

1. In **Authentication and Authorization → Users**, open the account.
2. Select **Delete** and carefully review Django Admin's deletion confirmation page.
3. Confirm the deletion only after checking the affected records.
4. Review **Admin activity logs** to confirm the administrator, time, target username, and delete action were recorded.

User deletion has these operational effects:

- The user profile and MFA device are removed.
- The user's article votes are removed.
- Articles remain in the knowledge base, but their `owner` becomes empty. Author snapshot fields preserve the historical display information.
- Article approval, deletion-queue, and deletion-request user references become empty where appropriate; the article records remain.
- Authentication, activity, image-upload, and Admin audit logs retain their historical snapshot data for the configured log-retention period.

After deleting a user who owned articles, open **My Profile → Admin tools → Scan orphan articles** and reassign the orphaned articles to an appropriate active account, or manage them according to the approved content-retention process. Do not delete a user simply to remove an article; use the application article deletion workflow instead.

For LDAP/Active Directory users, deleting the DjOpenKB user record does not delete the AD account. A later successful AD login can create a new DjOpenKB profile again. Disable the account in Active Directory, or keep the DjOpenKB account as Disabled User, when the goal is to prevent future access.

### 12.2 Routine service-maintenance checklist

Use this small routine after important updates or when troubleshooting:

```bash
cd /opt/DjOpenKB
sudo docker compose ps
sudo docker compose logs --tail=100 web
sudo docker compose logs --tail=100 nginx
sudo docker compose exec web python manage.py check --deploy
```

Also check scheduled cleanup, database backups, and disk usage at the intervals defined by local operations policy. Sections 14, 16, and 17 contain the exact PostgreSQL, scheduler, backup, and disk commands.

---

## 13. OpenKB AI operations

### Synchronise AI indexes

Use the all-scope command after published content changes, restoration from the published-article deletion queue, a model change, or an AI indexing deployment change:

```bash
sudo docker compose exec web python manage.py sync_openkb_ai --scope all
```

Scope-specific commands:

```bash
sudo docker compose exec web python manage.py sync_openkb_ai --scope public
sudo docker compose exec web python manage.py sync_openkb_ai --scope internal
sudo docker compose exec web python manage.py sync_openkb_ai --scope all
```

Check that internal source data has not entered the public OpenKB runtime tree:

```bash
sudo docker compose exec web python manage.py check_internal_article_isolation --sync-first
```

### Change the AI model or `AI_API_KEY`

1. Update the non-secret model value in `.env`.

   ```bash
   cd /opt/DjOpenKB
   nano .env
   ```

2. Temporarily restore the protected bootstrap file from its secure administrator location, then update only the required `AI_API_KEY` value.

   ```bash
   nano vault/bootstrap/djopenkb.env
   ```

3. Re-seed the Vault secret bundle and restart services that read the secrets at process startup.

   ```bash
   sudo docker compose up -d --force-recreate vault-init
   sudo docker compose restart web cleanup-scheduler
   ```

4. Rebuild both AI indexes and verify isolation.

   ```bash
   sudo docker compose exec web python manage.py sync_openkb_ai --scope all
   sudo docker compose exec web python manage.py check_internal_article_isolation --sync-first
   ```

5. Remove the temporary bootstrap file from working copies/export packages again.

   ```bash
   rm -f vault/bootstrap/djopenkb.env
   ```

### Re-initialise OpenKB only when required

Use this recovery procedure only when `openkb-data/.openkb/` is missing, damaged, or intentionally rebuilt. Follow the same one-time process in **Section 9**, including entering the model from `OPENKB_AI_MODEL` and pressing Enter at the OpenKB API-key prompt.

After restoring `openkb-data/.openkb/config.yaml`, restart the affected services and rebuild the AI data:

```bash
cd /opt/DjOpenKB
sudo docker compose restart web cleanup-scheduler
sudo docker compose exec web python manage.py sync_openkb_ai --scope all
sudo docker compose exec web python manage.py check_internal_article_isolation --sync-first
```

Inspect the runtime trees from both the host and the container:

```bash
ls -la /opt/DjOpenKB/openkb-data
ls -la /opt/DjOpenKB/openkb-data/.openkb
ls -la /opt/DjOpenKB/openkb-data-internal
sudo docker compose exec web ls -la /app/openkb-data
sudo docker compose exec web ls -la /app/openkb-data/.openkb
sudo docker compose exec web ls -la /app/openkb-data-internal
```

To confirm the Docker image has the bundled OpenKB CLI package available:

```bash
sudo docker compose exec web sh -lc 'PYTHONPATH=/app/OpenKB-main python -m openkb.cli --help'
```

---

## 14. PostgreSQL access and maintenance

Use PostgreSQL commands for inspection, backup, and recovery support. Prefer Django Admin or management commands for normal content/user changes.

### Open an interactive PostgreSQL prompt

```bash
cd /opt/DjOpenKB
sudo docker compose exec -it -u postgres db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Useful read-only `psql` commands:

```sql
\conninfo
\dt
\d kb_suggestedarticle
SELECT COUNT(*) FROM auth_user;
SELECT COUNT(*) FROM kb_suggestedarticle;
\q
```

Do not run ad-hoc `UPDATE`, `DELETE`, `DROP`, or schema changes in production unless following a reviewed recovery procedure.

### Back up PostgreSQL

Create a protected local backup folder:

```bash
sudo install -d -m 700 /var/backups/djopenkb
```

Create a timestamped SQL backup:

```bash
sudo sh -c 'cd /opt/DjOpenKB && docker compose exec -T -u postgres db sh -lc '\''pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"'\'' > /var/backups/djopenkb/postgres-$(date +%F-%H%M%S).sql'
sudo chmod 600 /var/backups/djopenkb/postgres-*.sql
```

Verify the newest backup is non-empty:

```bash
sudo ls -lh /var/backups/djopenkb/postgres-*.sql | tail
```

Test database restoration only on a separate non-production environment. Never test destructive restore commands against the live service.

---

## 15. Vault access and maintenance

Vault is deliberately bound to `127.0.0.1:8200` on the Linux host. Do not publish port `8200` through public firewalls.

### Check Vault status

No token is needed to check sealed/initialized status:

```bash
cd /opt/DjOpenKB
sudo docker compose exec vault vault status
sudo docker compose logs --tail=120 vault
sudo docker compose logs --tail=120 vault-auto-unseal
sudo docker compose logs --tail=160 vault-init
```

### Check secret metadata without printing secret values

The root token file is a break-glass administrator credential. Keep its filesystem permissions restrictive and do not paste its contents into tickets or terminals shared with others.

```bash
cd /opt/DjOpenKB
sudo docker compose exec \
  -e VAULT_TOKEN="$(sudo cat vault/keys/root-token.txt)" \
  vault sh -lc 'vault kv metadata get secret/djopenkb'
```

### Interactive Vault administrator shell

Use only from a protected administrator terminal:

```bash
cd /opt/DjOpenKB
sudo docker compose exec -it \
  -e VAULT_TOKEN="$(sudo cat vault/keys/root-token.txt)" \
  vault sh
```

Inside the Vault shell:

```bash
vault status
vault token lookup
vault kv metadata get secret/djopenkb
```

`vault kv get secret/djopenkb` prints the secret values to screen. Use it only when a break-glass recovery task genuinely requires viewing the values.

Exit the container shell:

```bash
exit
```

### Optional local-only Vault UI access through SSH

From an administrator workstation, create a local SSH tunnel:

```bash
ssh -L 8200:127.0.0.1:8200 <linux-admin-user>@kb.example.com
```

Then open this address only on the administrator workstation:

```text
http://127.0.0.1:8200
```

Do not expose the Vault UI directly to the internet. The root token remains highly sensitive even when the UI is accessed through a tunnel.

### Fresh test deployment Vault reset only

This is for a failed **disposable test deployment** only. It destroys Vault state and breaks access to secrets stored there. Do not run it against a live system.

```bash
cd /opt/DjOpenKB
sudo docker compose down
sudo rm -rf vault/file vault/keys
mkdir -p vault/file vault/keys
chmod 700 vault/keys
sudo docker compose up -d --build
```

---

## 16. Scheduled cleanup and retention operations

The `cleanup-scheduler` service runs these Django commands on the configured interval:

```text
cleanup_stray_upload_files --noinput
cleanup_article_deletion_queue --noinput
cleanup_auth_activity_logs --noinput
cleanup_activity_logs --noinput
```

Inspect the scheduler:

```bash
sudo docker compose ps cleanup-scheduler
sudo docker compose logs --tail=160 cleanup-scheduler
```

Run safe dry-run checks manually:

```bash
sudo docker compose exec web python manage.py cleanup_stray_upload_files --dry-run
sudo docker compose exec web python manage.py cleanup_article_deletion_queue --dry-run
sudo docker compose exec web python manage.py cleanup_auth_activity_logs --dry-run
sudo docker compose exec web python manage.py cleanup_activity_logs --dry-run
```

Operational retention values are maintained in Django Admin → Site settings. Relevant administrator settings include session timeout, admin MFA idle timeout, activity-log retention, authentication-log retention, upload cleanup age, article deletion-queue retention, and pagination limits.

A published-article deletion retention value of `0` causes permanent deletion immediately after MFA confirmation. Use that setting only when immediate deletion is intended.

---

## 17. Backups and disk monitoring

Create the protected backup folder if it does not already exist:

```bash
sudo install -d -m 700 /var/backups/djopenkb
```

Back up deployment runtime data to an approved secure location:

```bash
sudo tar -C /opt/DjOpenKB -czf /var/backups/djopenkb/runtime-$(date +%F-%H%M%S).tar.gz \
  .env vault/file vault/keys vault/logs postgres-data \
  openkb-data openkb-data-internal nginx/certs
sudo chmod 600 /var/backups/djopenkb/runtime-*.tar.gz
```

These archives contain secrets and application data. Encrypt them and store them according to organisational backup policy.

Monitor disk usage:

```bash
df -h
du -sh /opt/DjOpenKB/postgres-data \
       /opt/DjOpenKB/openkb-data \
       /opt/DjOpenKB/openkb-data-internal \
       /opt/DjOpenKB/vault/file 2>/dev/null
sudo docker system df
```

---

## 18. Update the deployment from Git

For the normal day-to-day project update, these commands are enough:

```bash
cd /opt/DjOpenKB
git pull --ff-only
sudo docker compose up -d --build
sudo docker compose ps
sudo docker compose logs --tail=100 web
```

The `web` service automatically runs Django migrations, the knowledge-base schema repair, and `collectstatic` before Gunicorn starts. Therefore, do **not** run `migrate` or `collectstatic` separately after every normal `git pull`.

The local runtime files below are ignored by Git and are not normally modified or deleted by `git pull`:

```text
.env
vault/bootstrap/djopenkb.env
vault/file/
vault/keys/
postgres-data/
openkb-data/
openkb-data-internal/
.openkb-venv/
nginx/certs/
```

Run `git status --short` only when you have edited project source files directly on the server, or when `git pull` reports a conflict. Ignored runtime data will not normally appear in this command.

### Only when needed after an update

Run these extra commands only for the related change, not after every update:

```bash
# Translation source (.po) files changed and compiled .mo files were not included.
sudo docker compose exec web python manage.py compilemessages

# Article data was imported/restored, OpenKB was repaired/re-initialised,
# or an AI index needs to be rebuilt manually.
sudo docker compose exec web python manage.py sync_openkb_ai --scope all

# Perform a deployment-security health check after an important configuration,
# authentication, infrastructure, or dependency update.
sudo docker compose exec web python manage.py check --deploy
```

For a documentation-only Git update, no Docker command is required. For a deliberately small template/Python update where no dependency, Docker, Compose, Nginx, or migration-related file changed, this quicker restart is also sufficient:

```bash
cd /opt/DjOpenKB
sudo docker compose restart web
sudo docker compose logs --tail=100 web
```

If `git pull --ff-only` fails because the server contains tracked source-code changes, stop and review before resolving it:

```bash
cd /opt/DjOpenKB
git status
git branch --show-current
git fetch origin
git log --oneline HEAD..origin/main
```

Do not use `git clean -fdx`; it can remove ignored deployment state such as OpenKB data, Vault files, and local secrets.

---

## 19. Troubleshooting

### Nginx returns `502 Bad Gateway`

The Django/Gunicorn container usually failed to start or is not ready.

```bash
cd /opt/DjOpenKB
sudo docker compose ps
sudo docker compose logs --tail=180 web
sudo docker compose logs --tail=120 nginx
```

Typical causes: a Python import error, a misplaced file, an unapplied migration, a missing Vault secret, a malformed `.env` value, or a failed LDAP import. Correct the reported error and rebuild:

```bash
sudo docker compose up -d --build
```

### Vault initialization fails

```bash
cd /opt/DjOpenKB
nl -ba vault/bootstrap/djopenkb.env
sudo docker compose logs --tail=180 vault-init
sudo docker compose exec vault vault status
```

Check for malformed `KEY=value` lines, missing `DJANGO_SECRET_KEY`, or missing `POSTGRES_PASSWORD`.

### OpenKB AI chat fails

```bash
cd /opt/DjOpenKB
grep -E 'OPENKB_AI_(PROVIDER|MODEL)' .env
sudo docker compose logs --tail=180 web
sudo docker compose exec web python manage.py sync_openkb_ai --scope all
sudo docker compose exec web python manage.py check_internal_article_isolation --sync-first
```

Confirm that the model string matches the API key stored in Vault and that the provider account permits the model.

### LDAPS connection fails

```bash
cd /opt/DjOpenKB
openssl x509 -in ldap-certs/ad-ca.crt -noout -subject -issuer -dates
sudo docker compose exec web python scripts/test_ldaps_tls.py
sudo docker compose exec web python manage.py test_ldap_auth alice@ad.example.com
```

Confirm that:

- `LDAP_SERVER_URI` uses the DC FQDN, not the IP address.
- The DC certificate matches that FQDN.
- `ldap-certs/ad-ca.crt` is the issuing CA certificate in PEM format.
- Docker can resolve the FQDN, either through internal DNS or the configured `LDAP_EXTRA_*` values.
- The service account password is present in Vault.

### MFA codes fail for multiple users

Verify the host time first:

```bash
date -u
timedatectl status
```

Then use the safe diagnostic:

```bash
sudo docker compose exec web python manage.py diagnose_mfa <username-or-email>
```

### Redis is unavailable in production

```bash
sudo docker compose ps redis
sudo docker compose logs --tail=120 redis
sudo docker compose up -d redis
sudo docker compose restart web cleanup-scheduler
```

Keep `DJANGO_ALLOW_LOCAL_CACHE_FALLBACK=false` for normal production operation.

---

## 20. Quick first-install checklist

```bash
cd /opt/DjOpenKB
cp .env.example .env
nano .env

cp vault/bootstrap/djopenkb.env.example vault/bootstrap/djopenkb.env
chmod +x vault/bootstrap/generate-secrets.sh
./vault/bootstrap/generate-secrets.sh
nano vault/bootstrap/djopenkb.env

# Optional: copy LDAPS CA certificate to ldap-certs/ad-ca.crt and configure LDAP values.
# Optional: replace the self-signed Nginx certificate with a trusted certificate.

sudo docker compose up -d --build
sudo docker compose ps
sudo docker compose logs --tail=150 vault-init
sudo docker compose logs --tail=150 web

sudo docker compose exec web python manage.py check --deploy
sudo docker compose exec web python manage.py createsuperuser
sudo docker compose exec web python manage.py seed_djopenkb_roles --assign-missing-users
sudo docker compose exec web python manage.py sync_openkb_ai --scope all
sudo docker compose exec web python manage.py check_internal_article_isolation --sync-first

curl -k https://127.0.0.1:8080/robots.txt
curl -k -I https://127.0.0.1:8080/login/
```

For application functionality, security controls, user workflows, and role descriptions, use `documentations/FULL_FEATURE_DOCUMENTATION.md` rather than this deployment guide.
