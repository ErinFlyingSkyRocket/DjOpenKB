# DjOpenKB Project Structure

This document explains the main folders and files in DjOpenKB and what each part is responsible for. Deployment and day-to-day run commands are kept in `README.md`.

## Overview

DjOpenKB is a Dockerized Django knowledge-base web application integrated with OpenKB. It includes:

- Django web interface for viewing, searching, creating, and managing wiki articles.
- OpenKB-backed AI chat/search integration using `openkb-data/`.
- NextLabs AD/LDAP login as the main user login path.
- Local Django login as a fallback path.
- PostgreSQL for Django application data.
- HashiCorp Vault for sensitive runtime secrets.
- Nginx HTTPS reverse proxy.
- Multilingual UI through Django locale files.
- Admin tools for pending article review, import/export, and stray upload cleanup.

## Top-Level Structure

```text
DjOpenKB/
├── README.md                    # Deployment, usage, update, backup, and troubleshooting guide
├── PROJECT_STRUCTURE.md         # Project structure and component overview
├── docker-compose.yml           # Main Docker Compose stack definition
├── Dockerfile                   # Django web image build file
├── Dockerfile.postgres-vault    # PostgreSQL image with Vault secret-loading entrypoint
├── manage.py                    # Django management entry point
├── requirements.txt             # Python dependencies for the Django web container
├── .env                         # Non-secret runtime configuration; do not commit real values
├── .env.example                 # Safe example environment file
├── .gitignore                   # Git ignore rules for secrets, data folders, and generated files
├── djopenkb/                    # Django project package
├── kb/                          # Main Django application
├── website/                     # HTML templates and static frontend assets
├── locale/                      # Translation files for the multilingual interface
├── OpenKB-main/                 # Bundled OpenKB source/tooling
├── openkb-data/                 # Active OpenKB knowledge-base content and generated wiki data
├── vault/                       # Vault configuration, scripts, keys, and persisted local Vault data
├── docker/                      # Helper scripts used by Docker containers
├── nginx/                       # Nginx reverse proxy and local HTTPS certificate files
├── postgres-data/               # Local PostgreSQL data folder, if bind-mounted locally
└── staticfiles/                 # Collected Django static files
```

## Django Project: `djopenkb/`

```text
djopenkb/
├── settings.py                  # Main Django settings and environment/Vault integration
├── urls.py                      # Root URL routing
├── wsgi.py                      # Gunicorn WSGI entry point
└── asgi.py                      # ASGI entry point, if needed later
```

Important responsibilities:

- Reads non-secret settings from `.env`.
- Loads sensitive settings from Vault where configured.
- Configures PostgreSQL database access.
- Configures LDAP/AD authentication settings.
- Registers installed Django apps, middleware, templates, static files, and locale settings.

## Main App: `kb/`

```text
kb/
├── admin.py                     # Django admin registrations
├── apps.py                      # Django app configuration
├── backends.py                  # Custom authentication backends for AD/local login
├── forms.py                     # Django forms used by article and auth flows
├── models.py                    # Main database models
├── urls.py                      # App-level routes
├── management/commands/         # Custom management commands
├── migrations/                  # Database migrations
├── templatetags/                # Custom template filters/tags
└── views/                       # Split view modules for cleaner routing
```

### `kb/views/`

```text
kb/views/
├── auth.py                      # Login/logout and AD/local authentication flow
├── articles.py                  # Article display and article-related pages
├── suggestions.py               # User article suggestion and review flow
├── admin_tools.py               # Admin tools pages
├── openkb_ai.py                 # OpenKB AI/chatbox endpoints
├── services.py                  # Shared service/helper functions
└── ...                          # Other feature-specific views
```

Key features handled by `kb/`:

- Article listing, article viewing, and markdown rendering.
- Search and search snippet cleanup.
- User-submitted article suggestions.
- Pending article approval workflow.
- Pending failed review comments and resubmission flow.
- Admin-only tools and access control.
- OpenKB AI chatbox integration.
- AD/LDAP authentication with local-login fallback.
- Test/debug commands such as LDAP connectivity checks.

## Templates and Static Files: `website/`

```text
website/
├── templates/
│   ├── base.html                # Shared layout
│   ├── login.html               # Login page with AD-first flow and local access link
│   ├── profile.html             # User profile page
│   ├── pending_articles.html    # Admin pending article management page
│   ├── article_*.html           # Article display/edit/review templates
│   └── ...
└── static/
    ├── css/                     # Project CSS
    ├── javascripts/             # Project JavaScript
    ├── images/                  # Static image assets
    └── ...
```

Important UI behavior:

- AD login is presented as the default login path.
- Local Django login is available through a secondary local account access link.
- Admin tools are hidden and enforced server-side for non-admin users.
- Pending article review pages support review/edit workflows and localized UI text.

## Translations: `locale/`

```text
locale/
├── en/LC_MESSAGES/django.po
├── en/LC_MESSAGES/django.mo
├── zh_Hans/LC_MESSAGES/django.po
├── zh_Hans/LC_MESSAGES/django.mo
├── sv/LC_MESSAGES/django.po
├── sv/LC_MESSAGES/django.mo
└── ...
```

The project uses Django internationalization for multilingual UI text.

Each language folder contains:

```text
django.po                       # Editable translation source file
django.mo                       # Compiled translation file used by Django at runtime
```

When updating translated text:

1. Update the relevant `django.po` files.
2. Compile them into `django.mo` files.
3. Restart/recreate the `web` container if needed.

## OpenKB Integration

### `OpenKB-main/`

```text
OpenKB-main/
├── openkb/                      # OpenKB Python package/source
├── tests/                       # OpenKB tests from the bundled source
├── examples/                    # Example OpenKB documents
├── pyproject.toml               # OpenKB package metadata
└── README.md                    # Upstream OpenKB documentation
```

This folder contains the bundled OpenKB source used by the project. In general, avoid modifying upstream OpenKB source unless a change is intentionally required.

### `openkb-data/`

```text
openkb-data/
├── .openkb/                     # OpenKB state/index/config data
├── raw/                         # Raw markdown/text documents imported into OpenKB
└── wiki/                        # Generated/managed wiki markdown content
```

This is the active knowledge-base content folder used by DjOpenKB.

Responsibilities:

- Stores article markdown files.
- Stores OpenKB-generated wiki data.
- Provides the content source for OpenKB AI/chatbox responses.
- Receives approved user-submitted articles after admin review.

Do not confuse this with `OpenKB-main/`, which is the OpenKB source/tooling folder.

## Vault: `vault/`

```text
vault/
├── config/
│   ├── vault.hcl                # Vault server configuration
│   └── djopenkb-policy.hcl      # Vault policy for DjOpenKB app token access
├── scripts/
│   ├── init.sh                  # Initializes/unseals/seeds Vault for DjOpenKB
│   └── auto-unseal.sh           # Local auto-unseal helper for VM/lab deployment
├── bootstrap/
│   ├── djopenkb.env.example     # Safe template for first-time secret seeding
│   └── djopenkb.env             # Temporary real secret seed file; do not commit
├── file/                        # Persisted Vault storage data; do not commit/delete casually
├── keys/                        # Local Vault key/token material for lab auto-unseal; protect carefully
└── logs/                        # Vault-related logs, if enabled
```

Vault stores sensitive secrets such as:

```text
DJANGO_SECRET_KEY
POSTGRES_PASSWORD
GEMINI_API_KEY
LLM_API_KEY
LDAP_BIND_PASSWORD
LDAP_PLACEHOLDER_PASSWORD
```

`.env` should hold mostly non-secret configuration, while Vault holds the sensitive values.

Important behavior:

- `vault/bootstrap/djopenkb.env` is a temporary plaintext seed file used for first-time setup or intentional secret rotation.
- During `docker compose up`, the `vault-init` container reads `vault/bootstrap/djopenkb.env` and stores the values in Vault under the DjOpenKB secret path.
- After seeding, the running containers do not need the plaintext `djopenkb.env` file for normal startup because secrets are read from Vault.
- `web`, `db`, and `cleanup-scheduler` receive non-secret settings from `.env`/Compose and read sensitive values from Vault at runtime.
- Examples of Vault-backed values include `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, `GEMINI_API_KEY`, `LLM_API_KEY`, and `LDAP_BIND_PASSWORD`.
- After secrets are seeded into Vault, remove the plaintext bootstrap file from `vault/bootstrap/`. Keep only `djopenkb.env.example` in the repository.
- Vault persistence is stored in the mounted `vault/file/` directory, and local unseal/app token material is stored in `vault/keys/`. Protect both folders.
- PostgreSQL persistence is stored in `postgres-data/`. This contains Django users, article workflow records, sessions, permissions, and other database state.
- Do not delete `vault/file/`, `vault/keys/`, or `postgres-data/` unless you intentionally want to reset the environment.
- Do not run `docker compose down -v` unless you intentionally want to wipe Docker-managed volumes. In this project, major state is bind-mounted to local folders, but `down -v` can still remove named volumes such as static assets and may reset other volume-backed state if Compose is changed later.
- If Vault data is deleted or reset, recreate `vault/bootstrap/djopenkb.env`, seed Vault again, and remove the plaintext file after startup.
- If PostgreSQL data is preserved, keep `POSTGRES_PASSWORD` in Vault the same as the password used when PostgreSQL was first initialized. Changing the Vault value alone will not update the existing PostgreSQL user password.

## Docker Helper Scripts: `docker/`

```text
docker/
└── postgres-vault-entrypoint.sh # Reads POSTGRES_PASSWORD from Vault before PostgreSQL starts
```

This script allows the PostgreSQL container to read its password from Vault instead of a plaintext `.env` file.

At startup, the PostgreSQL container reads `POSTGRES_PASSWORD` from Vault before launching PostgreSQL. The Django web container and cleanup scheduler similarly read application secrets from Vault through the configured Vault address, app token file, KV mount, and secret path. This keeps passwords and API keys out of the normal `.env` file during day-to-day operation.

## PostgreSQL Data

Depending on the Compose configuration, PostgreSQL data may be stored in a local folder such as:

```text
postgres-data/
```

or in a Docker-managed volume.

This data contains:

- Django users and local admin accounts.
- Sessions.
- Permissions and groups.
- Suggested article records.
- Review status and related Django model data.

Do not delete the PostgreSQL data folder or volume unless you intentionally want a fresh database.

## Nginx Reverse Proxy: `nginx/`

```text
nginx/
├── nginx.conf                   # HTTPS reverse proxy configuration
├── certs/
│   ├── localhost.crt            # Local development certificate
│   └── localhost.key            # Local development private key
├── generate-localhost-cert.bat  # Windows certificate helper
├── generate-localhost-cert.ps1  # PowerShell certificate helper
└── generate-localhost-cert.sh   # Linux/macOS certificate helper
```

Nginx sits in front of Django/Gunicorn and exposes the site over HTTPS, commonly at:

```text
https://127.0.0.1:8080
```

The local certificate is self-signed, so browsers may show a certificate warning during local development.

## Docker Compose Services

The main stack is defined in `docker-compose.yml`.

Typical services:

```text
vault                       # HashiCorp Vault secret store
vault-init                  # Initializes/unseals/seeds Vault
vault-auto-unseal           # Local auto-unseal helper
postgres / db               # PostgreSQL database container
web                         # Django + Gunicorn app container
nginx                       # HTTPS reverse proxy
cleanup-scheduler           # Scheduled cleanup for stray uploads
```

Service dependency flow:

```text
Vault starts
→ vault-init prepares secrets
→ PostgreSQL reads DB password from Vault and starts
→ Django web starts, runs migrations, collects static files, and starts Gunicorn
→ Nginx proxies browser traffic to Django
→ cleanup-scheduler runs periodic cleanup jobs
```

## Authentication Components

DjOpenKB currently supports:

```text
NextLabs AD / LDAP login     # Main login path for normal users
Local Django login           # Secondary fallback path
```

Related files:

```text
kb/backends.py               # Custom AD/local auth backends
kb/views/auth.py             # Login/logout view logic
website/templates/login.html # Login UI
.env                         # Non-secret LDAP settings
Vault secret/djopenkb        # LDAP_BIND_PASSWORD and other secrets
```

Current AD-related settings include:

```text
LDAP_ENABLED
LDAP_SERVER_URI
LDAP_BIND_DN
LDAP_AD_DOMAIN
LDAP_NETBIOS_DOMAIN
LDAP_USER_SEARCH_BASE
LDAP_USER_FILTER
LDAP_ALLOWED_EMAIL_DOMAINS
```

Sensitive AD bind password is stored in Vault as:

```text
LDAP_BIND_PASSWORD
```

## Article Workflow Features

DjOpenKB includes article contribution and review features:

```text
Draft article editing
User article suggestion
Pending article review by admin
Pending failed status with admin review comments
Approved article publishing into OpenKB/wiki data
Admin manage pending articles page
```

Related areas:

```text
kb/models.py
kb/views/suggestions.py
kb/views/admin_tools.py
website/templates/*pending*
website/templates/*suggestion*
locale/*/LC_MESSAGES/django.po
```

## Admin Tools

Admin-only functionality includes:

```text
Manage pending articles
Bulk import/export articles
Clean stray upload files
OpenKB AI/data synchronization helpers
Django admin site
```

Access to these tools is enforced server-side. Non-admin users should not be able to access admin-only routes even if they guess the URL.

## Cleanup Scheduler

The cleanup scheduler service periodically removes stray uploaded files that are no longer referenced by valid content.

Typical service name:

```text
cleanup-scheduler
```

Related behavior:

- Helps keep the upload folder clean.
- Avoids leaving unused files from failed/draft article uploads.
- Logs scan results in Docker logs.

## Generated and Runtime Folders

These folders are generated or runtime-specific and should generally not be committed:

```text
postgres-data/
vault/file/
vault/keys/
vault/logs/
vault/bootstrap/djopenkb.env
nginx/certs/*.key
staticfiles/
__pycache__/
*.pyc
```

Safe template/example files may be committed:

```text
.env.example
vault/bootstrap/djopenkb.env.example
```

## Files to Treat Carefully

```text
.env                         # May contain environment-specific non-secret config
vault/bootstrap/djopenkb.env # Temporary plaintext secrets; remove after seeding
vault/file/                  # Persistent Vault data
vault/keys/                  # Vault key/token material for local lab unseal
postgres-data/               # Persistent database data
nginx/certs/localhost.key    # Local TLS private key
```
