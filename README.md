# DjOpenKB

DjOpenKB is a Docker-based internal IT knowledge base built with Django. It provides a secure article website for IT documentation, user article suggestions, admin review, Active Directory / LDAPS login, local Django login, MFA, Vault-backed secrets, PostgreSQL, Nginx HTTPS, activity logging, and an integrated OpenKB AI chatbot.

The project is designed for a local VM, lab, or intranet-style deployment. A paid public domain is not required. The website can be accessed through HTTPS using the Linux server IP address or localhost, while Active Directory can use an internal lab domain such as `openkb.local`.

---

## Main Features

- Internal IT wiki / knowledge base website.
- Article browsing, searching, view tracking, voting, and trending articles.
- User article suggestion workflow with admin approval.
- Draft, pending approval, pending failed, and published article states.
- Published article update workflow where normal-user edits are held as pending updates while the current published version remains visible.
- Admin review area for new pending articles, pending updates, rejected / pending failed feedback, and update rejection feedback.
- Admin tools for bulk article backup import/export, split exports, stray upload cleanup, orphan article management, and site settings.
- Local Django login support.
- Active Directory login over LDAPS for domain users.
- Clear separation between local users and AD users.
- MFA support using authenticator-app one-time passwords.
- Authentication and activity logging for important user, article, admin, AI, and maintenance actions.
- Configurable log retention and admin log display settings.
- Vault integration for sensitive secrets such as Django, database, LDAP, and AI credentials.
- PostgreSQL database through Docker Compose.
- Nginx HTTPS reverse proxy on port `8080`.
- OpenKB AI chatbot integration using `OpenKB-main/` and `openkb-data/`, including prompt length limits, rate limiting, cooldown blocking, related article recommendations, and activity logging.
- Markdown article rendering with sanitization.
- Image upload restrictions for article content.
- Multilingual UI support through Django translation files.
- Docker cleanup scheduler for routine cleanup tasks.

---

## User Types and Rights

| User type | Authentication source | Main purpose | Article browsing | Vote on articles | Suggest articles | Edit own drafts / pending failed / published updates | Change own email/password | Admin tools | Django Admin access |
|---|---|---|---|---|---|---|---|---|---|
| Anonymous visitor | None | Read-only public access if allowed by deployment | Published articles only | No | No | No | No | No | No |
| Local user | Django local account | Normal internal contributor | Published articles | Yes, after login | Yes | Yes; published edits go to pending update for admin approval | Yes, with MFA/OTP | No | No |
| Local admin | Django local account | Main-site administrator | All relevant article workflow views | Yes | Yes | Yes | Yes, with MFA/OTP | Yes | Only if staff/superuser/Django Admin permission is granted |
| AD user | Active Directory / LDAPS | Domain-authenticated contributor | Published articles | Yes, after login | Yes | Yes; published edits go to pending update for admin approval | No; AD-managed values are blocked in Django | No | No |
| AD admin / LDAP admin | Active Directory / LDAPS | Domain-authenticated administrator | All relevant article workflow views | Yes | Yes | Yes | No; AD-managed values are blocked in Django | Yes | Only if staff/superuser/Django Admin permission is granted |

Local and AD users are separated by account source metadata, not by email domain. This means a local user can use an email address such as `alice@openkb.local` without being incorrectly treated as an AD user.

---

## Security Overview

| Area | Security controls |
|---|---|
| Authentication | Local login, AD/LDAPS login, MFA, session timeout, authentication logging |
| User separation | Local and AD users are separated by account source; AD-managed password/email changes are blocked locally |
| MFA | Authenticator-app OTP, MFA setup, MFA verification, MFA reset, and sensitive account-change protection |
| Authorization | Admin-only tools, article owner checks, approval workflow, and restricted admin routes |
| Articles | Draft/pending/pending failed/published workflow, pending-update review for published edits, duplicate title prevention, admin approval, and orphan article management |
| Uploads | Image-only allowlist, file validation, generated filenames, protected serving, and stray upload cleanup |
| Markdown | Sanitized rendered HTML to reduce XSS risk |
| AI chatbot | Prompt length limits, 5 questions per 60 seconds, 30-minute cooldown after exceeding the limit, per-user limiting for logged-in users, per-IP limiting for anonymous users, safer error handling, related article recommendations, and activity logging |
| Logging | Separate authentication logs and general activity logs |
| Secrets | Vault-backed Django/database/LDAP/AI secrets |
| Network | Nginx HTTPS reverse proxy and configurable trusted hosts/origins |
| Operations | Cleanup commands, cleanup scheduler, deployment checks, and backup guidance |

---

## Article Workflow Summary

DjOpenKB keeps public article content stable while still allowing controlled user updates.

```text
Normal user creates a new article:
Draft → Pending → Pending failed / Published

Normal user edits an already published article:
Published article remains visible
Edited version is saved as a pending update
Admin approves the update → pending update replaces the public article
Admin rejects the update → public article stays unchanged and feedback is shown to the owner

Admin-created or admin-edited article:
Admin can publish directly when appropriate
```

Pending updates store the proposed title, body, keywords, and image references separately from the current public article. This means users can continue reading the approved version while the new version waits for review.

## Bulk Import and Export Summary

The admin bulk import/export tool is intended for article backup, migration, and controlled restore. Exported packages include article content, keywords, referenced images, pending-update data, and review-related metadata needed to restore the article workflow.

```text
Normal export:
- Creates one article backup ZIP when the result is small enough.
- Automatically creates a split package when the export becomes large.

Split export:
- Creates an outer ZIP package containing multiple importable part ZIP files.
- Each part is targeted below the import upload limit.

Import:
- Import each ZIP part one by one if the export was split.
- Each uploaded ZIP should be 100 MB or below.
```

Export does not remove or unpublish live articles. It only creates a downloadable backup copy for administrators.

---

## Main Documentation

| File | Purpose |
|---|---|
| `README.md` | Overall project overview, folder structure, feature summary, security summary, and operational notes. |
| `documentations/DEPLOYMENT_GUIDE.md` | Linux server deployment, Git pull/update flow, Docker startup, Vault setup, Nginx certificate setup, OpenKB initialization, and troubleshooting. |
| `documentations/FULL_FEATURE_DOCUMENTATION.md` | Full feature documentation, user rights, workflow details, security controls, and logging details. |
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
├── documentations/              # Project setup, deployment, and feature guides
├── docker/                      # Docker helper scripts
├── nginx/                       # Nginx HTTPS reverse proxy configuration and cert scripts
├── vault/                       # Vault configuration, bootstrap files, and local Vault runtime data
├── ldap-certs/                  # AD/LDAPS CA certificate mounted into the web container
├── OpenKB-main/                 # OpenKB source code used by the AI chatbot
├── openkb-data/                 # OpenKB workspace and article data used by the AI chatbot
├── postgres-data/               # PostgreSQL local persistent data folder
├── staticfiles/                 # Django collected static files
├── scripts/                     # Utility and testing scripts
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

This is where most application features are implemented, including article management, article suggestions, approval workflow, authentication handling, MFA, admin tools, uploads, OpenKB AI integration, activity logging, and management commands.

Important areas:

```text
kb/models.py                 # Database models for articles, votes, logs, MFA, site settings, and related data
kb/forms.py                  # Django forms used by articles, profiles, MFA, and admin workflows
kb/backends.py               # Authentication backend, including AD/LDAP login handling
kb/admin.py                  # Django admin registration and admin display settings
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
kb/views/services.py         # Shared helper logic and compatibility re-exports
kb/views/services_bulk.py    # Bulk import/export, ZIP splitting, and restore helpers
kb/views/services_search.py  # Search ranking, related articles, and suggestions
kb/views/services_ai.py      # OpenKB AI helper logic, rate limiting, and AI recommendations
```

---

### `website/`

Contains the frontend files used by the Django app.

```text
website/templates/           # HTML templates
website/templates/admin/     # Django Admin template overrides
website/static/              # CSS, JavaScript, images, icons, and other static assets
```

The templates cover the base layout, login pages, article pages, profile pages, admin tools, MFA pages, AI chat UI, and Django Admin display improvements.

---

### `locale/`

Contains Django translation files for the multilingual interface.

Each language has its own folder with `.po` and `.mo` files:

```text
locale/<language>/LC_MESSAGES/django.po
locale/<language>/LC_MESSAGES/django.mo
```

After editing `.po` files, compile the `.mo` files:

```bash
docker compose exec web python manage.py compilemessages
```

---

### `documentations/`

Contains the main documentation files for setup, testing, and feature explanation.

```text
documentations/DEPLOYMENT_GUIDE.md
documentations/FULL_FEATURE_DOCUMENTATION.md
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

## OpenKB AI Chatbot Rate Limiting

The AI chatbot is protected with rate limiting so users cannot continuously consume AI resources.

Current default behaviour:

```text
Maximum questions: 5
Time window: 60 seconds
Cooldown after exceeding limit: 30 minutes
```

Rate limiting is based on the visitor type:

| Visitor type | Rate-limit identity | Behaviour |
|---|---|---|
| Logged-in local user | Django user ID | The limit follows the authenticated local account. |
| Logged-in AD / LDAP user | Django user ID | The limit follows the Django-side account created for the domain user. |
| Anonymous visitor | IP address | The limit follows the detected client IP address. |

This means refreshing the browser, opening a new tab, or using incognito mode does not bypass the limit for anonymous users unless the IP address changes. Logged-in users are limited by their own account ID, so a shared company network, VPN, Wi-Fi, or proxy IP does not accidentally block all authenticated staff.

The related settings are:

```text
OPENKB_AI_RATE_LIMIT_MAX_REQUESTS = 5
OPENKB_AI_RATE_LIMIT_WINDOW_SECONDS = 60
OPENKB_AI_RATE_LIMIT_BLOCK_SECONDS = 1800
```

The rate limit is intentionally not based on the browser session.

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
scripts/sync_locales.py
```

These scripts help confirm whether the Django container can resolve and connect to the LDAPS server, or help keep translation files synchronized after new UI strings are added.

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

If local files block `git pull`, check first:

```bash
git status
```

Then either commit your changes, stash them, or restore unwanted local changes before pulling.

---

## Useful Maintenance Commands

Check container status:

```bash
docker compose ps
```

View logs:

```bash
docker compose logs --tail=100 web
docker compose logs --tail=100 nginx
docker compose logs --tail=100 vault
docker compose logs --tail=100 cleanup-scheduler
```

Run Django checks:

```bash
docker compose exec web python manage.py check
docker compose exec web python manage.py check --deploy
```

Sync OpenKB AI article data:

```bash
docker compose exec web python manage.py sync_openkb_ai
```

Clean activity logs:

```bash
docker compose exec web python manage.py cleanup_activity_logs --dry-run
docker compose exec web python manage.py cleanup_activity_logs
```

Compile translations after editing `.po` files:

```bash
docker compose exec web python manage.py compilemessages
```

---

## Backup and Restore Notes

For a real intranet or production-style deployment, back up these areas regularly:

```text
PostgreSQL database
Vault data and recovery material
openkb-data/
article uploaded images
generated OpenKB Markdown files
.env and deployment configuration, stored securely
```

Do not only back up the source code. The running system also depends on local database state, Vault state, uploaded files, and OpenKB data.

Recommended operational practice:

```text
1. Create backups regularly.
2. Store backups securely.
3. Test restore steps before relying on them.
4. Do not store Vault keys, private keys, or database backups in public Git repositories.
```

---


### Bulk article export/import backup

Use the admin **Bulk import/export articles** tool when you need an application-level article backup or migration package. The export includes article body content, titles, keywords, published status, pending-update data, review notes/history where applicable, and referenced image files.

The import upload limit is 100 MB per ZIP file. Split exports are packaged so that each inner part ZIP can be imported one by one instead of uploading one very large file.

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

These may contain secrets, tokens, private keys, database content, or local runtime state.

---

## Final Security Reminder

Before deploying outside a local lab, confirm:

```text
DEBUG=False
ALLOWED_HOSTS is set correctly
CSRF_TRUSTED_ORIGINS is set correctly
HTTPS is working through Nginx
LDAPS certificate validation is working
Vault is initialized and sealed/unsealed correctly
Django check --deploy has been reviewed
Backups and restore steps are documented
OpenKB AI rate limiting and activity logging are enabled
```
