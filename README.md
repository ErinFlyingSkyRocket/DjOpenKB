# DjOpenKB

DjOpenKB is a Django-based web wiki project integrated with OpenKB.  
The application runs behind Nginx with HTTPS on port `8080` using Docker Compose.

This version uses PostgreSQL for Django data such as user accounts, admin data, sessions, and Django model records. OpenKB content remains file-based inside `openkb-data/`.

## Project Structure

```text
DjOpenKB/
├── djopenkb/              # Django project settings and URLs
├── kb/                    # Django app
├── website/               # Templates and static files
├── openkb-data/           # OpenKB knowledge base data
│   ├── raw/               # Raw markdown/text documents
│   └── wiki/              # Generated wiki content
├── postgres-data/         # Local PostgreSQL data folder, do not delete if keeping DB data
├── nginx/
│   ├── nginx.conf         # Nginx HTTPS reverse proxy config
│   ├── generate-localhost-cert.bat
│   ├── generate-localhost-cert.ps1
│   ├── generate-localhost-cert.sh
│   └── certs/             # Local SSL certificate files
│       ├── localhost.crt
│       └── localhost.key
├── Dockerfile
├── docker-compose.yml
├── manage.py
└── .env
```

## Requirements

Make sure these are installed:

```text
Docker Desktop
Git
OpenSSL
```

For Windows, OpenSSL can be used through Git Bash or Git for Windows.

Docker Desktop must be running before starting the web application.

---

## 1. Generate Local HTTPS Certificate

This project uses a local self-signed SSL certificate for HTTPS access through Nginx.

The generated certificate files are:

```text
nginx/certs/localhost.crt
nginx/certs/localhost.key
```

These files are mounted into the Nginx Docker container and used for:

```text
https://localhost:8080
```

### Windows Certificate Generation

Go to the `nginx` folder:

```powershell
cd C:\Users\Erinc\Desktop\DjOpenKB\DjOpenKB\nginx
```

Run the certificate generator:

```powershell
.\generate-localhost-cert.bat
```

This should create:

```text
nginx/certs/localhost.crt
nginx/certs/localhost.key
```

### Linux Certificate Generation

Go to the `nginx` folder:

```bash
cd nginx
```

Make the Bash script executable:

```bash
chmod +x generate-localhost-cert.sh
```

Run it:

```bash
./generate-localhost-cert.sh
```

This should create:

```text
nginx/certs/localhost.crt
nginx/certs/localhost.key
```

If OpenSSL is missing, install it first.

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install openssl -y
```

CentOS/RHEL/Fedora:

```bash
sudo dnf install openssl -y
```

### Nginx Docker Certificate Mount

In `docker-compose.yml`, the Nginx service should mount the cert folder like this:

```yaml
volumes:
  - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
  - ./nginx/certs:/etc/nginx/certs:ro
ports:
  - "8080:8080"
```

Inside `nginx/nginx.conf`, the certificate paths should be:

```nginx
ssl_certificate     /etc/nginx/certs/localhost.crt;
ssl_certificate_key /etc/nginx/certs/localhost.key;
```

Because this is a self-signed certificate, the browser may show a warning such as:

```text
Your connection is not private
```

This is normal for local development. Continue to:

```text
https://localhost:8080
```

after accepting the browser warning.

---

## 2. Initialize OpenKB Data

OpenKB should be initialized inside the `openkb-data` folder.

Do **not** initialize OpenKB inside `OpenKB-main`.

Correct folder:

```text
DjOpenKB/openkb-data
```

Wrong folder:

```text
DjOpenKB/OpenKB-main
```

From the project root:

```powershell
cd C:\Users\Erinc\Desktop\DjOpenKB\DjOpenKB
```

Go into `openkb-data`:

```powershell
cd openkb-data
```

Initialize OpenKB:

```powershell
openkb init
```

Add your knowledge base files into:

```text
openkb-data/raw/
```

Example:

```text
openkb-data/raw/test.md
```

Then add the raw documents into OpenKB:

```powershell
openkb add raw
```

Test OpenKB manually:

```powershell
openkb query "What is this knowledge base about?"
```

If this works, the web application should also be able to query OpenKB.

For Linux, the same commands apply:

```bash
cd openkb-data
openkb init
openkb add raw
openkb query "What is this knowledge base about?"
```

---

## 3. Environment File

Create or update `.env` in the project root:

```env
OPENKB_BASE_DIR=OpenKB-main
OPENKB_DATA_DIR=openkb-data
OPENKB_AI_PROVIDER=openkb-cli
OPENKB_GEMINI_MODEL=gemini/gemini-2.5-flash
LITELLM_DROP_PARAMS=true

DJANGO_DEBUG=true
DJANGO_SECRET_KEY=change-this-to-your-own-secret-key

POSTGRES_DB=djopenkb
POSTGRES_USER=djopenkb
POSTGRES_PASSWORD=change-this-password
POSTGRES_HOST=db
POSTGRES_PORT=5432
USE_SQLITE=false

GEMINI_API_KEY=your_gemini_api_key_here
LLM_API_KEY=your_gemini_api_key_here

LDAP_ENABLED=false
LDAP_SERVER_URI=ldap://your-ad-server.nextlabs.com:389
LDAP_BIND_DN=CN=ldap-reader,OU=Service Accounts,DC=nextlabs,DC=com
LDAP_BIND_PASSWORD=your_ldap_password_here
LDAP_USER_SEARCH_BASE=DC=nextlabs,DC=com
LDAP_USER_FILTER=(userPrincipalName=%(user)s)
```

For local testing without LDAP, keep:

```env
LDAP_ENABLED=false
```

When LDAP is ready, change it to:

```env
LDAP_ENABLED=true
```

Do not commit `.env` to GitHub because it may contain API keys, LDAP passwords, and Django secrets.

---

## 4. PostgreSQL Data Storage

Django uses PostgreSQL for application database records.

This includes:

```text
Django admin accounts
Normal user accounts
Groups and permissions
Login sessions
Django app model data
Suggested articles or article records stored as Django models
```

The database files are stored locally in the project folder:

```text
postgres-data/
```

This folder is mounted into the PostgreSQL Docker container:

```yaml
./postgres-data:/var/lib/postgresql/data
```

This means your database can survive even if you delete Docker containers and images, as long as you keep the local `postgres-data/` folder.

Important:

```text
Do not delete postgres-data/ if you want to keep PostgreSQL data.
Do not commit postgres-data/ to GitHub.
```

Your OpenKB files are separate and remain stored in:

```text
openkb-data/raw/
openkb-data/wiki/
```

---

## 5. Start the Website

From the project root:

```powershell
cd C:\Users\Erinc\Desktop\DjOpenKB\DjOpenKB
```

Build and start the containers:

```powershell
docker compose up --build
```

For older Docker Compose:

```powershell
docker-compose up --build
```

For Linux:

```bash
docker compose up --build
```

After it starts, open:

```text
https://localhost:8080
```

The web container is configured to run these automatically during startup:

```sh
python manage.py migrate --noinput
python manage.py collectstatic --noinput
gunicorn djopenkb.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 300
```

So normally, migrations will run automatically when the container starts.

---

## 6. Run Django Migrations Manually

If you want to run migrations manually, use this after the containers are running:

```powershell
docker compose exec web python manage.py migrate
```

For older Docker Compose:

```powershell
docker-compose exec web python manage.py migrate
```

For Linux:

```bash
docker compose exec web python manage.py migrate
```

To check migration status:

```powershell
docker compose exec web python manage.py showmigrations
```

To create new migration files after changing Django models:

```powershell
docker compose exec web python manage.py makemigrations
```

Then apply them:

```powershell
docker compose exec web python manage.py migrate
```

---

## 7. Create Django Admin Account

After PostgreSQL is running and migrations have completed, create a new admin account:

```powershell
docker compose exec web python manage.py createsuperuser
```

For older Docker Compose:

```powershell
docker-compose exec web python manage.py createsuperuser
```

For Linux:

```bash
docker compose exec web python manage.py createsuperuser
```

Follow the prompts:

```text
Username:
Email address:
Password:
Password again:
```

Then log in to Django admin at:

```text
https://localhost:8080/admin/
```

If you delete `postgres-data/`, the admin account will be deleted too and you must run `createsuperuser` again.

---

## 8. Stop the Website

Press:

```text
Ctrl + C
```

Then run:

```powershell
docker compose down
```

For older Docker Compose:

```powershell
docker-compose down
```

For Linux:

```bash
docker compose down
```

This stops and removes the containers but keeps your local PostgreSQL data because it is stored in:

```text
postgres-data/
```

---

## 9. Rebuild After Code Changes

If Python packages, `Dockerfile`, or Docker Compose settings are changed:

```powershell
docker compose down
docker compose up --build
```

For older Docker Compose:

```powershell
docker-compose down
docker-compose up --build
```

If only HTML, CSS, or Python code is changed, usually this is enough:

```powershell
docker compose restart
```

For Linux:

```bash
docker compose down
docker compose up --build
```

---

## 10. Reset the PostgreSQL Database

Only do this if you intentionally want to delete all Django database data.

Stop containers:

```powershell
docker compose down
```

Then delete this folder from the project:

```text
postgres-data/
```

Start again:

```powershell
docker compose up --build
```

Then create a new admin account:

```powershell
docker compose exec web python manage.py createsuperuser
```

Important: deleting Docker images does not delete `postgres-data/`. Deleting the `postgres-data/` folder does delete your database.

---

## 11. SQLite Notes

This project now uses PostgreSQL by default.

Old SQLite file:

```text
db.sqlite3
```

can be deleted after confirming the website works with PostgreSQL.

To temporarily use SQLite again, set this in `.env`:

```env
USE_SQLITE=true
```

For normal PostgreSQL usage, keep:

```env
USE_SQLITE=false
```

---

## 12. OpenKB Notes

Important:

```text
OpenKB-main = OpenKB source/package folder
openkb-data = actual knowledge base folder
```

Always run OpenKB commands from:

```text
DjOpenKB/openkb-data
```

Correct:

```powershell
cd openkb-data
openkb init
openkb add raw
openkb query "your question"
```

Wrong:

```powershell
cd OpenKB-main
openkb init
```

The Django app should use `openkb-data` as the working directory when running OpenKB queries.

Inside Docker, this folder is available as:

```text
/app/openkb-data
```

---

## 13. Testing Inside Docker

Enter the Django container:

```powershell
docker compose exec web sh
```

For older Docker Compose:

```powershell
docker-compose exec web sh
```

Check OpenKB:

```sh
cd /app/openkb-data
openkb query "What is this knowledge base about?"
```

Check Django:

```sh
python manage.py check
```

Check the database connection:

```sh
python manage.py dbshell
```

Exit `dbshell` with:

```sql
\q
```

Check LDAP package installation:

```sh
python -c "import ldap; import django_auth_ldap; print('LDAP packages OK')"
```

---

## 14. LDAP / Active Directory Notes

LDAP is optional.

For normal local testing, use:

```env
LDAP_ENABLED=false
```

When LDAP is enabled, the application will use Active Directory login.

Example LDAP-related `.env` settings:

```env
LDAP_ENABLED=true
LDAP_SERVER_URI=ldap://your-ad-server.nextlabs.com:389
LDAP_BIND_DN=CN=ldap-reader,OU=Service Accounts,DC=nextlabs,DC=com
LDAP_BIND_PASSWORD=your_ldap_password_here
LDAP_USER_SEARCH_BASE=DC=nextlabs,DC=com
LDAP_USER_FILTER=(userPrincipalName=%(user)s)
```

To test LDAP inside Docker:

```powershell
docker compose exec web sh
```

Then run:

```sh
ldapsearch -x \
  -H "$LDAP_SERVER_URI" \
  -D "$LDAP_BIND_DN" \
  -w "$LDAP_BIND_PASSWORD" \
  -b "$LDAP_USER_SEARCH_BASE" \
  "(userPrincipalName=user@nextlabs.com)"
```

Replace:

```text
user@nextlabs.com
```

with a real Active Directory email account.

---

## 15. Common Issues

### No knowledge base found

Error:

```text
No knowledge base found. Run `openkb init` first.
```

Fix:

```powershell
cd openkb-data
openkb init
openkb add raw
```

Also make sure the Django code runs OpenKB using `openkb-data` as the current working directory.

Inside Docker, test with:

```sh
cd /app/openkb-data
openkb query "What is this knowledge base about?"
```

### 502 Bad Gateway

This usually means the Django container is not running correctly.

Check logs:

```powershell
docker compose logs web
```

For older Docker Compose:

```powershell
docker-compose logs web
```

### PostgreSQL connection refused

This means Django cannot connect to the database container yet.

Check that the database container is running:

```powershell
docker compose ps
```

Check database logs:

```powershell
docker compose logs db
```

Make sure `.env` contains:

```env
POSTGRES_HOST=db
POSTGRES_PORT=5432
USE_SQLITE=false
```

Then restart:

```powershell
docker compose down
docker compose up --build
```

### Admin login does not work

If this is a fresh PostgreSQL setup, old SQLite admin accounts are not automatically available.

Create a new admin account:

```powershell
docker compose exec web python manage.py createsuperuser
```

### Old SQLite data missing

This is expected if you switched to PostgreSQL without migrating old SQLite data.

Django is now reading from PostgreSQL, not from:

```text
db.sqlite3
```

### HTTPS certificate error

This is normal because the certificate is self-signed.

Open:

```text
https://localhost:8080
```

Then accept the browser warning.

### Nginx cannot find certificate

Make sure these files exist:

```text
nginx/certs/localhost.crt
nginx/certs/localhost.key
```

Then restart:

```powershell
docker compose restart nginx
```

### LDAP login not working

Check `.env` values:

```env
LDAP_ENABLED=true
LDAP_SERVER_URI=...
LDAP_BIND_DN=...
LDAP_BIND_PASSWORD=...
LDAP_USER_SEARCH_BASE=...
LDAP_USER_FILTER=...
```

Then test inside Docker:

```sh
ldapsearch -x \
  -H "$LDAP_SERVER_URI" \
  -D "$LDAP_BIND_DN" \
  -w "$LDAP_BIND_PASSWORD" \
  -b "$LDAP_USER_SEARCH_BASE" \
  "(userPrincipalName=user@nextlabs.com)"
```

---

## 16. Useful Docker Commands

View all logs:

```powershell
docker compose logs
```

View web logs only:

```powershell
docker compose logs web
```

View database logs only:

```powershell
docker compose logs db
```

View Nginx logs only:

```powershell
docker compose logs nginx
```

Restart containers:

```powershell
docker compose restart
```

Stop and remove containers:

```powershell
docker compose down
```

Rebuild everything:

```powershell
docker compose up --build
```

For older Docker Compose, replace `docker compose` with `docker-compose`.

---

## Access URL

```text
https://localhost:8080
```
