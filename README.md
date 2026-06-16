# DjOpenKB

DjOpenKB is a Docker-based internal IT knowledge base built with Django. It provides a secure article website for IT documentation, user article suggestions, admin review, Active Directory / LDAPS login, local Django login, MFA, Vault-backed secrets, PostgreSQL, Nginx HTTPS, activity logging, and an integrated OpenKB AI chatbot.

The project is designed for a local VM, lab, or intranet-style deployment. A paid public domain is not required. The website can be accessed through HTTPS using the Linux server IP address or localhost, while Active Directory can use an internal lab domain such as `openkb.local`.

---

## Main Features

- Internal IT wiki / knowledge base website.
- Login-protected article browsing, title/keyword search, view tracking, voting, trending articles, most-liked articles, and most-recent articles.
- User article suggestion workflow with admin approval and pending-update review for published article edits.
- Draft, pending approval, pending failed, and published article states.
- Published article update workflow where normal-user edits are held as pending updates while the current published version remains visible.
- Admin review area for new pending articles, pending updates, rejected / pending failed feedback, and update rejection feedback.
- Admin tools for bulk article backup import/export, split exports, stray upload cleanup, orphan article management, group/user role management, and site settings.
- Local Django login support.
- Active Directory login over LDAPS for domain users.
- Clear separation between local users and AD users.
- MFA support using authenticator-app one-time passwords.
- Authentication and activity logging for important user, article, admin, AI, and maintenance actions.
- Configurable log retention and admin log display settings.
- Admin-configurable progressive password/MFA lockout policy with reset actions for administrators.
- Vault integration for sensitive secrets such as Django, database, LDAP, field-encryption, and AI credentials.
- PostgreSQL database through Docker Compose.
- Redis-backed production cache for rate limiting, authentication lockout counters, and AI concurrency controls.
- Nginx HTTPS reverse proxy on port `8080`.
- OpenKB AI chatbot integration using `OpenKB-main/` and `openkb-data/`, including prompt length limits, Redis-backed user rate limiting, cooldown blocking, concurrency limits, timeout controls, related article recommendations, and activity logging.
- Markdown article rendering with sanitization.
- Image upload restrictions for article content.
- Multilingual UI support through Django translation files.
- Docker cleanup scheduler for routine cleanup tasks.
- Manual keyword suggestion refresh that scans the current title/body against existing manually created article keywords only.
- Admin-configurable article count per page with safe limits from 5 to 100.
- Login page is the only public entry point; normal app/admin paths return 404 to anonymous users.

---

## User Types and Rights

DjOpenKB currently uses a login-only website model. The root URL displays the login page. Anonymous users should not be able to browse normal article, search, profile, admin, AI, or upload pages; protected paths return 404 instead of exposing normal application pages.

| Role / group | Authentication source | Main purpose | Article browsing | Vote on articles | Add / submit articles | Manage approvals | Admin tools | Django Admin access |
|---|---|---|---|---|---|---|---|---|
| Anonymous visitor | None | Not allowed into the main site | No; only the login page and static/login support paths are public | No | No | No | No | No |
| Disabled User | Local or AD / LDAPS | Account retained but blocked | No; stopped after valid password/MFA with a disabled-account message | No | No | No | No | No |
| Regular User | Local or AD / LDAPS | Default internal reader | Yes, published articles after login | Yes | No | No | No | No |
| Article Writer | Local or AD / LDAPS | Contributor | Yes | Yes | Yes; drafts and submissions go through approval | No | No | No |
| Article Approver | Local or AD / LDAPS | Approval reviewer | Yes | Yes | No | Yes, can review pending articles and pending updates | No | No by default |
| Article Manager | Local or AD / LDAPS | Full article manager | Yes | Yes | Yes | Yes, can review pending articles and pending updates | Yes, can delete articles | No by default |
| Admin Users | Local or AD / LDAPS | Trusted administrator | Yes | Yes | Yes | Yes | Yes | Yes; members are automatically synced to Django superuser/staff and must still pass network/admin guards |

Newly created non-admin local or AD users are automatically placed in the `Regular User` group. Admins can move an account to `Disabled User` when the account should remain in the database for audit/history but must not complete login or access the website.

Group membership is the baseline permission model. Normal non-admin groups can be combined where appropriate, and future non-role groups such as email notification groups can be added without changing the core role model. Direct user permission checkboxes are add-on permissions only; unticking a direct permission does not remove a permission inherited from a group.

Role precedence is enforced as follows:

```text
Disabled User
→ highest precedence
→ removes Admin Users / Regular User / Article Writer / Article Approver / Article Manager
→ clears direct Knowledge Repository permission add-ons
→ unchecks staff and superuser status
→ redirects authenticated sessions to the disabled-account page

Admin Users
→ full administrator source of truth
→ removes Regular User / Article Writer / Article Approver / Article Manager
→ sets staff=True and superuser=True
→ local accounts become Local admin; AD/LDAP accounts become LDAP admin
→ keeps custom non-role groups, such as future notification groups

Regular User / Article Writer / Article Approver / Article Manager
→ normal role groups
→ may be combined when needed
→ local accounts stay Local user; AD/LDAP accounts stay LDAP user unless promoted to Admin Users
```

Django's built-in `Active` checkbox controls whether the account can sign in at all. `Disabled User` is different: it keeps the account record available but sends an already-authenticated disabled account to the clean disabled-account page with a sign-out button.

Local and AD users are separated by account source metadata, not by email domain. This means a local user can use an email address such as `alice@openkb.local` without being incorrectly treated as an AD user.

## Security Overview

| Area | Security controls |
|---|---|
| Authentication | Login-only site access, local login, AD/LDAPS login, MFA, session timeout, authentication logging |
| Anonymous access | Only the login/language/static support paths are public; normal app pages and hidden admin login return 404 when unauthenticated |
| User separation | Local and AD users are separated by account source; AD-managed password/email changes are blocked locally |
| MFA | Authenticator-app OTP, MFA setup, MFA verification, MFA reset, and sensitive account-change protection |
| Authorization | Group-based roles, enforced role precedence, add-on direct user permissions, admin-only tools, article owner checks, approval workflow, and restricted admin routes |
| Articles | Draft/pending/pending failed/published workflow, pending-update review for published edits, duplicate title prevention, admin approval, and orphan article management |
| Search and listing | Main search matches published article title and manually entered keywords only; homepage uses paginated tabs for trending, most liked, and most recent articles |
| Keywords | Suggested keywords are manually refreshed and only come from existing manually created article keywords when the exact keyword/phrase appears in the current draft title/body |
| Uploads | Image-only allowlist, file validation, generated filenames, protected serving, and stray upload cleanup |
| Markdown | Sanitized rendered HTML to reduce XSS risk |
| AI chatbot | Login-protected chatbot endpoint, prompt length limits, 5 questions per 60 seconds, 30-minute cooldown after exceeding the limit, Redis-backed user-ID limiting, concurrency limits, timeout controls, safer error handling, related article recommendations, and activity logging |
| Password/MFA lockout | Progressive lockout policy stored in Site settings, with configurable stages, repeat counts, block durations, and admin reset actions |
| Logging | Separate authentication logs, general activity logs, and admin activity logs with retention cleanup |
| Secrets | Vault-backed Django/database/LDAP/field-encryption/AI secrets |
| Network | Nginx HTTPS reverse proxy, configurable trusted hosts/origins, and optional admin CIDR/VPN restrictions |
| Operations | Cleanup commands, cleanup scheduler, deployment checks, `.dockerignore`, and backup guidance |

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

## Architecture Overview

The reference architecture diagram is stored under:

```text
documentations/Djopenkb Architecture Layout Diagram.png
```

It shows the intended internal deployment path: users connect to Nginx over HTTPS on port `8080`, Nginx reverse-proxies to the Django web application, Django uses PostgreSQL for data, Vault for secrets, OpenKB for local AI knowledge-base integration, and optional LDAPS to Active Directory. Only one configured AI provider/API key is required at runtime.

![DjOpenKB Architecture Layout Diagram](documentations/Djopenkb%20Architecture%20Layout%20Diagram.png)

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
├── redis-data/                  # Optional persistent Redis data if enabled in future deployment tuning
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

Vault is used to store sensitive values such as Django secret key, PostgreSQL password, field-encryption key, LDAP bind password, and AI API keys.

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

## OpenKB AI Chatbot Rate Limiting and Production Controls

The AI chatbot is protected with production controls so users cannot continuously consume AI resources or occupy all Gunicorn workers with slow AI requests.

Current default behaviour:

```text
Maximum questions per user: 5
Time window: 60 seconds
Cooldown after exceeding limit: 30 minutes
Prompt length limit: 1000 characters
OpenKB timeout: 90 seconds
Concurrent AI requests: 2
Concurrency lock expiry: 120 seconds
```

In the current login-only deployment, the chatbot is a protected signed-in feature. Logged-in users are rate-limited by their Django user ID, so refreshing the browser, opening a new tab, or starting a new private session does not reset the counter for that account.

For production, these counters use Redis through `REDIS_URL`, so the limits remain consistent even when Gunicorn runs multiple workers. Local memory cache is only a development/emergency fallback and should not be used for production.

If anonymous chatbot access is ever re-enabled in the future, the fallback rate-limit identity should be the detected client IP address behind the trusted Nginx reverse proxy.

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
# After first login, review Site settings → Authentication lockout policy stages.
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

## Development vs Production Runtime Notes

During active development, the Docker Compose bind mount below is useful because code edits on the host appear inside the container immediately:

```yaml
- .:/app
```

For a production or final demonstration deployment, remove the full project bind mount where possible and rebuild the image instead. This reduces the chance that host-side files such as `.env`, Vault material, local certificates, Git metadata, or temporary files are visible inside the running web container.

Production deployments should keep `REDIS_URL=redis://redis:6379/1` and `DJANGO_ALLOW_LOCAL_CACHE_FALLBACK=false` so authentication lockout, AI rate limits, and AI concurrency controls are shared across all Gunicorn workers.

A `.dockerignore` file should remain in place so local secrets and runtime data are not copied into the Docker image during `COPY . /app/`.

## Backup and Restore Notes

For a real intranet or production-style deployment, back up these areas regularly:

```text
PostgreSQL database
Vault data and recovery material
Redis data if persistence is enabled
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
.env.*
!.env.example
vault/bootstrap/djopenkb.env
vault/keys/*
vault/file/*
openkb-data/.openkb/
.openkb-venv/
redis-data/
ldap-certs/
nginx/certs/*.key
postgres-data/
exported article ZIP backups
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
Redis-backed rate limiting/cache is enabled for production
OpenKB AI rate limiting, concurrency control, and activity logging are enabled
Admin-configurable password/MFA lockout stages have been reviewed
```
