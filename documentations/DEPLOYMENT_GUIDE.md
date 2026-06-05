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
OPENKB_GEMINI_MODEL=gemini/gemini-2.5-flash
LITELLM_DROP_PARAMS=true

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

If LDAPS is enabled, update the LDAP section based on the Windows Server 2022 AD setup.

Example:

```env
LDAP_ENABLED=true
LDAP_PLACEHOLDER_ENABLED=false
LDAP_PLACEHOLDER_AUTO_CREATE_USERS=false

LDAP_SERVER_URI=ldaps://WIN-VVCA4BIOSK7.openkb.local:636
LDAP_START_TLS=false
LDAP_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/ad-ca.crt
LDAP_TLS_REQUIRE_CERT=demand
LDAP_ALLOW_INSECURE=false

LDAP_AD_DOMAIN=openkb.local
LDAP_NETBIOS_DOMAIN=OPENKB
LDAP_ALLOWED_EMAIL_DOMAINS=openkb.local

LDAP_USER_SEARCH_BASE=DC=openkb,DC=local
LDAP_USER_FILTER=(|(userPrincipalName=%(user)s)(sAMAccountName=%(user)s)(mail=%(user)s))
LDAP_BIND_DN=svc_djopenkb@openkb.local
LDAP_DC_IP=192.168.81.128
```

For full LDAPS setup, refer to:

```text
documentations/LDAP_LDAPS_SETUP.md
documentations/WINDOWS_SERVER_2022_AD_TESTING_SETUP.md
```

---

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

GEMINI_API_KEY=your-gemini-api-key
LLM_API_KEY=your-llm-provider-api-key

LDAP_BIND_PASSWORD="your-ad-service-account-password"
LDAP_PLACEHOLDER_PASSWORD=generated-random-value
```

Important notes:

```text
- Do not commit or share vault/bootstrap/djopenkb.env.
- For a fresh setup, generate POSTGRES_PASSWORD before the first startup.
- For an existing database, do not change POSTGRES_PASSWORD unless you also update it inside Postgres.
- After Vault is seeded and login works, remove vault/bootstrap/djopenkb.env from exported/shared copies.
```

---

## 7. Initialise OpenKB data locally

OpenKB must be initialized in the local project folder because Docker mounts the local `openkb-data/` folder into the Django container.

Initialize OpenKB locally at:

```text
/opt/DjOpenKB/openkb-data
```

Move to the project root.

```bash
cd /opt/DjOpenKB
```

Create a local Python virtual environment for OpenKB.

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
Model (enter for default gpt-5.4-mini):
```

type the model name you want to use.

### Common OpenKB model inputs

| Provider | Example model input |
|---|---|
| OpenAI latest frontier | `gpt-5.5` |
| OpenAI pro/high accuracy | `gpt-5.5-pro` |
| OpenAI standard | `gpt-5.4` |
| OpenAI mini/default-style | `gpt-5.4-mini` |
| OpenAI lower cost | `gpt-5.4-nano` |
| Gemini Flash | `gemini/gemini-2.5-flash` |
| Gemini Pro | `gemini/gemini-2.5-pro` |
| Anthropic Claude Haiku | `anthropic/claude-3-5-haiku-latest` |
| Anthropic Claude Sonnet | `anthropic/claude-3-5-sonnet-latest` |
| Anthropic Claude 4 style | `anthropic/claude-sonnet-4-6` |
| Anthropic Claude Opus style | `anthropic/claude-opus-4-6` |
| Ollama local model | `ollama/llama3.1` |
| Mistral | `mistral/mistral-small-latest` |
| Groq | `groq/llama-3.1-8b-instant` |
| OpenRouter | `openrouter/openai/gpt-4o-mini` |
| Cohere | `cohere/command-r` |

For the current DjOpenKB Gemini setup, enter:

```text
gemini/gemini-2.5-flash
```

If using OpenAI, OpenKB may accept OpenAI model names without the `openai/` prefix, for example:

```text
gpt-5.5
gpt-5.4
gpt-5.4-mini
```

For other providers, use the provider/model format, for example:

```text
anthropic/claude-3-5-sonnet-latest
gemini/gemini-2.5-flash
ollama/llama3.1
```

### API key values

Use the matching API key for the provider selected during `openkb init`.

For Gemini:

```env
GEMINI_API_KEY=your-gemini-api-key
LLM_API_KEY=your-gemini-api-key
```

For OpenAI:

```env
LLM_API_KEY=your-openai-api-key
```

For Anthropic Claude:

```env
LLM_API_KEY=your-anthropic-api-key
```

For OpenRouter, Groq, Mistral, Cohere, or other providers:

```env
LLM_API_KEY=your-provider-api-key
```

Some OpenKB versions may not show many prompts and may silently create the configuration files. That is acceptable.

Check that OpenKB created the local configuration.

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

Do not commit or share these generated OpenKB runtime files:

```text
openkb-data/.env
openkb-data/.openkb/
```

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

If LDAPS is enabled, export the AD CS Root CA certificate from Windows Server as Base-64 encoded X.509.

Place the exported CA certificate here:

```text
ldap-certs/ad-ca.crt
```

Check that the file exists.

```bash
ls -l ldap-certs/ad-ca.crt
```

If the Linux server or Docker container cannot resolve the AD hostname, make sure this field is set in `.env`:

```env
LDAP_DC_IP=192.168.81.128
```

Replace the IP address with the actual Windows Server 2022 Domain Controller IP.

---

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

If everything is successful, the `web`, `db`, `vault`, `cleanup-scheduler`, and `nginx` services should be running.

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
LDAP_BIND_PASSWORD="password-with-symbols"
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

Run migrations.

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

```bash
sudo docker compose exec web sh scripts/test_ldaps.sh
```

Expected successful output:

```text
TLS handshake OK
LDAPS DNS + TLS certificate validation looks good.
```

If it fails, refer to:

```text
documentations/LDAP_LDAPS_SETUP.md
documentations/WINDOWS_SERVER_2022_AD_TESTING_SETUP.md
```

---

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

---

## 16. Normal operation commands

Start services.

```bash
sudo docker compose up -d
```

Stop services.

```bash
sudo docker compose down
```

Restart web only.

```bash
sudo docker compose restart web
```

Restart Nginx only.

```bash
sudo docker compose restart nginx
```

View logs.

```bash
sudo docker compose logs -f web
sudo docker compose logs -f nginx
sudo docker compose logs -f db
sudo docker compose logs -f vault
```

Check status.

```bash
sudo docker compose ps
```

---

## 17. Pull latest updates later

Move into the project folder.

```bash
cd /opt/DjOpenKB
```

Check for local changes.

```bash
git status
```

Pull latest updates.

```bash
git pull
```

If OpenKB needs to be updated, update the local OpenKB virtual environment.

```bash
source .openkb-venv/bin/activate
pip install -e OpenKB-main
deactivate
```

Rebuild and restart containers.

```bash
sudo docker compose up -d --build
```

Run migrations.

```bash
sudo docker compose exec web python manage.py migrate
```

Collect static files.

```bash
sudo docker compose exec web python manage.py collectstatic --noinput
```

Sync OpenKB articles if article/AI logic changed.

```bash
sudo docker compose exec web python manage.py sync_openkb_ai
```

Run the deploy check.

```bash
sudo docker compose exec web python manage.py check --deploy
```

---

## 18. Files not to share

Do not commit or share these files/folders:

```text
.env
vault/bootstrap/djopenkb.env
vault/keys/
vault/file/
openkb-data/.env
openkb-data/.openkb/
nginx/certs/localhost.key
```

The public repository should only contain examples, scripts, and safe default configuration.

---

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

Activate the local OpenKB virtual environment:

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

Initialize OpenKB locally on the Linux host:

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

If the database already exists, changing `POSTGRES_PASSWORD` in Vault alone is not enough. Either recover the old password from Vault or update the password inside Postgres.

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
