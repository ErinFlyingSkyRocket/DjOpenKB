# DjOpenKB

DjOpenKB is a Docker-based internal IT knowledge base built with Django. It provides a secure article website for IT documentation, user article suggestions, admin review, Active Directory login, MFA, Vault-backed secrets, PostgreSQL, Nginx HTTPS, and an integrated OpenKB AI chatbot.

The project is designed for a local VM, lab, or intranet-style deployment. A paid public domain is not required. The website can be accessed through HTTPS using the Linux server IP address or localhost, while Active Directory can use an internal lab domain such as `openkb.local`.

---

## Main Features

- Internal IT wiki / knowledge base website.
- Article browsing, searching, view tracking, voting, and trending articles.
- User article suggestion workflow with admin approval.
- Admin review area for pending articles and rejected/pending failed feedback.
- Admin tools for import/export and cleaning stray upload files.
- Local Django login support.
- Active Directory login over LDAPS for domain users.
- MFA support using authenticator-app one-time passwords.
- Vault integration for sensitive secrets such as Django, database, LDAP, and AI credentials.
- PostgreSQL database through Docker Compose.
- Nginx HTTPS reverse proxy on port `8080`.
- OpenKB AI chatbot integration using `OpenKB-main/` and `openkb-data/`.
- Markdown article rendering with sanitization.
- Image upload restrictions for article content.
- Multilingual UI support through Django translation files.

---

## Main Documentation

| File | Purpose |
|---|---|
| `README.md` | Overall project overview and folder structure. |
| `documentations/DEPLOYMENT_GUIDE.md` | Linux server deployment, Git pull/update flow, Docker startup, Vault setup, Nginx certificate setup, and OpenKB initialization. |
| `documentations/LDAP_LDAPS_SETUP.md` | Django LDAPS configuration, CA certificate placement, and LDAPS connection testing. |
| `documentations/WINDOWS_SERVER_2022_AD_TESTING_SETUP.md` | Windows Server 2022 AD DS, AD CS, LDAPS certificate, and lab testing setup. |

For deployment, start with:

```text
 documentations/DEPLOYMENT_GUIDE.md
```

---

## Project Folder Structure

```text
DjOpenKB/
├── djopenkb/                    # Django project configuration
├── kb/                          # Main Django application logic
├── website/                     # Templates and static frontend files
├── locale/                      # Django translation files
├── documentations/              # Project setup and deployment guides
├── docker/                      # Docker helper scripts
├── nginx/                       # Nginx HTTPS reverse proxy configuration and cert scripts
├── vault/                       # Vault configuration, bootstrap files, and local Vault runtime data
├── ldap-certs/                  # AD/LDAPS CA certificate mounted into the web container
├── OpenKB-main/                 # OpenKB source code used by the AI chatbot
├── openkb-data/                 # OpenKB workspace and article data used by the AI chatbot
├── postgres-data/               # PostgreSQL local persistent data folder
├── staticfiles/                 # Django collected static files
├── scripts/                     # Utility scripts such as LDAPS testing
├── manage.py                    # Django management command entry point
├── Dockerfile                   # Django web container build file
├── Dockerfile.postgres-vault    # PostgreSQL image with Vault password support
├── docker-compose.yml           # Main Docker Compose stack
├── .env                         # Local non-secret runtime configuration
├── .env.example                 # Example runtime configuration
└── requirements.txt             # Python dependencies
```

---

## Folder Overview

### `djopenkb/`

Contains the main Django project configuration.

This folder controls project-level settings such as installed apps, middleware, database configuration, session settings, security settings, static files, Vault integration, LDAP/LDAPS integration, and root URL routing.

Main files:

```text
djopenkb/settings.py
djopenkb/urls.py
djopenkb/asgi.py
djopenkb/wsgi.py
```

---

### `kb/`

Contains the main Django app for the knowledge base.

This is where most application features are implemented, including article management, article suggestions, approval workflow, authentication handling, MFA, admin tools, uploads, OpenKB AI integration, and management commands.

Important areas:

```text
kb/models.py                 # Database models for articles, votes, logs, MFA, and related data
kb/forms.py                  # Django forms used by articles, profiles, MFA, and admin workflows
kb/backends.py               # Authentication backend, including AD/LDAP login handling
kb/admin.py                  # Django admin registration
kb/urls.py                   # App-level URL routes
kb/views/                    # Main page, article, admin, auth, MFA, AI, and service views
kb/management/commands/      # Custom Django management commands
kb/migrations/               # Database migrations
kb/templatetags/             # Custom template filters/tags
```

General view grouping:

```text
kb/views/main.py             # Article listing, search, detail pages, voting, and normal browsing
kb/views/suggestions.py      # User article suggestions, drafts, edits, and article image uploads
kb/views/admin.py            # Admin-only article review and maintenance tools
kb/views/auth.py             # Login/profile/account-related pages
kb/views/mfa.py              # MFA setup, verification, and reset views
kb/views/ai.py               # OpenKB AI chatbot endpoint
kb/views/services.py         # Shared helper logic used by other views
```

---

### `website/`

Contains the frontend files used by the Django app.

```text
website/templates/           # HTML templates
website/static/              # CSS, JavaScript, images, icons, and other static assets
```

The templates cover the base layout, login pages, article pages, profile pages, admin tools, MFA pages, and AI chat UI.

---

### `locale/`

Contains Django translation files for the multilingual interface.

Each language has its own folder with `.po` and `.mo` files:

```text
locale/<language>/LC_MESSAGES/django.po
locale/<language>/LC_MESSAGES/django.mo
```

---

### `documentations/`

Contains the main documentation files for setup and testing.

```text
documentations/DEPLOYMENT_GUIDE.md
documentations/LDAP_LDAPS_SETUP.md
documentations/WINDOWS_SERVER_2022_AD_TESTING_SETUP.md
```

Use the deployment guide for Linux server setup. Use the LDAPS guide and Windows Server guide when configuring Active Directory login.

---

### `nginx/`

Contains the Nginx reverse proxy configuration.

Nginx provides HTTPS access to the Django web container. The project uses port `8080` by default.

Important paths:

```text
nginx/nginx.conf
nginx/certs/
```

The `nginx/certs/` folder is used for local HTTPS certificates. Do not share private key files such as:

```text
nginx/certs/localhost.key
```

---

### `vault/`

Contains HashiCorp Vault configuration and local Vault runtime folders.

Vault is used to store sensitive values such as Django secret key, PostgreSQL password, LDAP bind password, and AI API key.

Important paths:

```text
vault/config/                # Vault configuration
vault/bootstrap/             # First-time bootstrap secret file and helper scripts
vault/keys/                  # Vault tokens and unseal keys, do not share
vault/file/                  # Vault runtime data, do not share
vault/logs/                  # Vault logs
vault/scripts/               # Vault setup/helper scripts
```

The bootstrap file is only for first-time setup:

```text
vault/bootstrap/djopenkb.env
```

After Vault is seeded, do not share this file.

---

### `ldap-certs/`

Contains the exported CA certificate used by Django to verify the Windows Server LDAPS certificate.

Expected file:

```text
ldap-certs/ad-ca.crt
```

This folder is mounted into the web container so Django can validate LDAPS properly.

---

### `OpenKB-main/`

Contains the OpenKB source code used by the AI chatbot integration.

The Django project calls OpenKB from this folder when answering AI chat queries and working with article data.

The `.env` setting normally points to this folder:

```env
OPENKB_BASE_DIR=OpenKB-main
```

---

### `openkb-data/`

Contains the OpenKB workspace used by the chatbot.

This folder stores the OpenKB data/index workspace and article files used by the AI integration.

Before using the chatbot, initialize OpenKB inside this folder:

```bash
docker compose exec web sh -lc "cd /app/openkb-data && PYTHONPATH=/app/OpenKB-main python -m openkb.cli init"
```

Then sync published Django articles into OpenKB:

```bash
docker compose exec web python manage.py sync_openkb_ai
```

If OpenKB is not initialized, the chatbot may fail or not detect the article data correctly.

---

### `postgres-data/`

Stores PostgreSQL local persistent data when using a bind-mounted database folder.

Do not delete this folder unless you intentionally want to reset the local database.

---

### `staticfiles/`

Contains static files collected by Django using:

```bash
docker compose exec web python manage.py collectstatic --noinput
```

Nginx serves these collected static files.

---

### `scripts/`

Contains utility scripts used for testing or maintenance.

Example:

```text
scripts/test_ldaps.sh
scripts/test_ldaps_tls.py
```

These scripts help confirm whether the Django container can resolve and connect to the LDAPS server.

---

## Quick Deployment Summary

Full deployment steps are in:

```text
documentations/DEPLOYMENT_GUIDE.md
```

Basic flow:

```bash
git clone https://github.com/ErinFlyingSkyRocket/DjOpenKB.git
cd DjOpenKB
cp .env.example .env
nano .env
sh vault/bootstrap/generate-secrets.sh
chmod +x nginx/certs/generate-localhost-cert.sh
./nginx/certs/generate-localhost-cert.sh
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py collectstatic --noinput
docker compose exec web python manage.py createsuperuser
docker compose exec web sh -lc "cd /app/openkb-data && PYTHONPATH=/app/OpenKB-main python -m openkb.cli init"
docker compose exec web python manage.py sync_openkb_ai
docker compose exec web python manage.py check --deploy
```

Access the website using:

```text
https://<server-ip>:8080
```

---

## Updating Later

For future updates:

```bash
cd /path/to/DjOpenKB
git pull https://github.com/ErinFlyingSkyRocket/DjOpenKB.git main
docker compose down
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py collectstatic --noinput
docker compose exec web python manage.py sync_openkb_ai
docker compose exec web python manage.py check --deploy
```

---

## Files Not to Share

Do not commit or share these files/folders:

```text
.env
vault/bootstrap/djopenkb.env
vault/keys/*
vault/file/*
openkb-data/.env
nginx/certs/localhost.key
postgres-data/
```

These may contain secrets, tokens, private keys, or local runtime state.
