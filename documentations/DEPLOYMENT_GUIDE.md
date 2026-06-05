# DjOpenKB Deployment Guide

This guide explains how to deploy DjOpenKB on a Linux server using Docker Compose.

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

## 7. Generate the local Nginx HTTPS certificate

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

## 8. Add the LDAPS CA certificate if using AD

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

## 9. Start the Docker stack

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

## 10. Fix Vault init failure during fresh testing

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

## 11. Run Django setup commands

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

## 12. Initialise OpenKB AI data

The OpenKB AI chatbot requires OpenKB data initialization. If this is skipped, the chatbot may return errors or fail to find article data.

Run the OpenKB init command.

```bash
sudo docker compose exec web sh -lc "cd /app/openkb-data && PYTHONPATH=/app/OpenKB-main python -m openkb.cli init"
```

During OpenKB init, it may prompt for AI/provider configuration. The exact prompt wording can vary depending on the OpenKB version, but the important fields are normally the provider/type, model name, API key, and optional base URL.

Use one of the following examples depending on the provider you want.

### Option A: Google Gemini

Use this if you are using Gemini API.

```text
AI provider / AI type / LLM provider: gemini
Model: gemini/gemini-2.5-flash
API key: same value as GEMINI_API_KEY in Vault
Base URL / API URL: leave blank/default
Embedding provider: leave default if prompted
```

Relevant `.env` values:

```env
OPENKB_AI_PROVIDER=openkb-cli
OPENKB_GEMINI_MODEL=gemini/gemini-2.5-flash
LITELLM_DROP_PARAMS=true
```

Relevant Vault bootstrap values:

```env
GEMINI_API_KEY=your-gemini-api-key
LLM_API_KEY=your-gemini-api-key
```

### Option B: OpenAI

Use this if you are using an OpenAI API key.

```text
AI provider / AI type / LLM provider: openai
Model: gpt-4o-mini
API key: your OpenAI API key
Base URL / API URL: leave blank/default
Embedding provider: leave default if prompted
```

Example model values:

```text
gpt-4o-mini
gpt-4.1-mini
gpt-4.1
```

Vault bootstrap:

```env
LLM_API_KEY=your-openai-api-key
```

If the OpenKB prompt expects a provider-prefixed LiteLLM model, use:

```text
openai/gpt-4o-mini
```

### Option C: Anthropic Claude

Use this if you are using an Anthropic/Claude API key.

```text
AI provider / AI type / LLM provider: anthropic
Model: claude-3-5-haiku-latest
API key: your Anthropic API key
Base URL / API URL: leave blank/default
Embedding provider: leave default if prompted
```

Other possible model value examples:

```text
claude-3-5-sonnet-latest
claude-3-7-sonnet-latest
```

Vault bootstrap:

```env
LLM_API_KEY=your-anthropic-api-key
```

If the OpenKB prompt expects a provider-prefixed LiteLLM model, use:

```text
anthropic/claude-3-5-haiku-latest
```

### Option D: Azure OpenAI

Use this if your organisation uses Azure OpenAI.

```text
AI provider / AI type / LLM provider: azure
Model: your Azure OpenAI deployment name
API key: your Azure OpenAI key
Base URL / API URL: your Azure OpenAI endpoint
API version: use the version configured for your Azure OpenAI resource
Embedding provider: leave default if prompted
```

Vault bootstrap:

```env
LLM_API_KEY=your-azure-openai-key
```

For Azure, the model/deployment field may need the Azure deployment name instead of the public model name.

### Option E: Ollama or local model

Use this if OpenKB is configured to call a local Ollama server.

```text
AI provider / AI type / LLM provider: ollama
Model: llama3.1
API key: leave blank if not required
Base URL / API URL: http://host.docker.internal:11434 or the reachable Ollama URL
Embedding provider: leave default if prompted
```

If Ollama runs on another Linux host, use that host IP instead of `host.docker.internal`.

Example model values:

```text
llama3.1
mistral
qwen2.5
```

### Option F: Other LiteLLM-compatible providers

If OpenKB supports LiteLLM-style provider strings in your installed version, use the provider/model pattern required by that provider.

Common pattern:

```text
provider/model-name
```

Examples:

```text
groq/llama-3.1-8b-instant
mistral/mistral-small-latest
openrouter/openai/gpt-4o-mini
cohere/command-r
```

Use the API key belonging to that provider in:

```env
LLM_API_KEY=your-provider-api-key
```

### General OpenKB init guidance

If the prompt asks for a LiteLLM-style model value, enter the full provider/model string, for example:

```text
gemini/gemini-2.5-flash
openai/gpt-4o-mini
anthropic/claude-3-5-haiku-latest
```

If it asks whether to drop unsupported parameters, choose yes/true if available, matching:

```env
LITELLM_DROP_PARAMS=true
```

If it asks to confirm or overwrite an existing OpenKB config during first setup, confirm yes.

If this is an existing deployment with working AI config, do not overwrite unless you intend to reconfigure it.

After init completes, sync published Django articles into OpenKB data.

```bash
sudo docker compose exec web python manage.py sync_openkb_ai
```

If many articles are added or changed later, run the sync command again.

---

## 13. Test LDAPS connection

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

## 14. Access the website

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

## 15. Normal operation commands

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

## 16. Pull latest updates later

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

## 17. Files not to share

Do not commit or share these files/folders:

```text
.env
vault/bootstrap/djopenkb.env
vault/keys/
vault/file/
openkb-data/.env
nginx/certs/localhost.key
```

The public repository should only contain examples, scripts, and safe default configuration.

---

## 18. Troubleshooting quick notes

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

Run:

```bash
sudo docker compose exec web sh -lc "cd /app/openkb-data && PYTHONPATH=/app/OpenKB-main python -m openkb.cli init"
sudo docker compose exec web python manage.py sync_openkb_ai
```

If the init command prompts again, use the provider section above and enter the provider/model/API key matching the LLM service you want to use.
