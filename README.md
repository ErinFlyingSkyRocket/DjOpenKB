# DjOpenKB

DjOpenKB is a Docker-based internal IT knowledge base built with Django. It provides a secure article website for IT documentation, public/internal article separation, user article suggestions, role-based review workflows, Active Directory / LDAPS login, local Django login, MFA, Vault-backed secrets, PostgreSQL, Nginx HTTPS, activity logging, and an integrated OpenKB AI chatbot.

The project is designed for a local VM, lab, or intranet-style deployment. A paid public domain is not required. The website can be accessed through HTTPS using the Linux server IP address or localhost, while Active Directory can use an internal lab domain such as `openkb.local`.

---

## Main Features

- Internal IT wiki / knowledge base website.
- Login-protected public article browsing, title/keyword search, view tracking, voting, trending articles, most-liked articles, and most-recent articles.
- Separate internal article area for users with internal article access.
- Public/internal article visibility model with separate public and internal writer, approver, and manager roles.
- User article suggestion workflow with approval and pending-update review for published article edits.
- Draft, pending approval, pending failed, and published article states.
- Published article update workflow where user edits are held as pending updates while the current published version remains visible.
- Separate public and internal pending-review queues, including internal-only pending management for internal approvers/managers.
- Article owners can manage their own drafts, failed submissions, and pending updates within the visibility scope they are allowed to create.
- Published article deletion uses an irreversible-action warning and MFA confirmation for users allowed to delete in that article's scope.
- Dislike counts are limited to Article Manager, Internal Article Manager, and Admin Users. Normal users and approvers only see helpful/like counts.
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
- OpenKB AI chatbot integration using `OpenKB-main/`, `openkb-data/`, and `openkb-data-internal/`, including prompt length limits, Redis-backed user rate limiting, cooldown blocking, concurrency limits, timeout controls, related article recommendations, role-scoped AI indexing, and activity logging.
- Markdown article rendering with sanitization.
- Image upload restrictions for article content, including protected image serving based on article visibility and ownership/review access.
- Multilingual UI support through Django translation files.
- Docker cleanup scheduler for routine cleanup tasks.
- Manual keyword suggestion refresh that scans the current title/body against existing manually created article keywords only.
- Admin-configurable article count per page with safe limits from 5 to 100.
- Login page is the only public entry point; normal app/admin paths return 404 to anonymous users.

---

## User Types and Rights

DjOpenKB currently uses a login-only website model. The root URL displays the login page. Anonymous users should not be able to browse normal article, internal article, search, profile, admin, AI, or upload pages; protected paths return 404 instead of exposing normal application pages.

### Main role matrix

| Role / group | Main purpose | Public article access | Internal article access | Create / submit | Review / approve | Edit published | Delete published | Admin tools / Django Admin |
|---|---|---|---|---|---|---|---|---|
| Anonymous visitor | No site access | No | No | No | No | No | No | No |
| Disabled User | Account retained but blocked | No | No | No | No | No | No | No |
| Regular User | Default public viewer | View published public articles and vote | No | No | No | No | No | No |
| Article Writer | Public contributor | View/vote public articles | No, unless also given an internal role | Create public drafts, submit public articles, edit own public drafts/failed submissions, submit pending updates for own public published articles | No | Own published public edits become pending updates | Can delete own public articles when allowed by the owner flow; published deletion requires MFA | No |
| Article Approver | Public review-only approver | View/vote public articles | No, unless also given an internal role | No by default | Review, edit during review, approve, or reject pending public articles/updates | Only during explicit public review flow; cannot freely edit already-published public articles | No | No |
| Article Manager | Public article manager | View/vote public articles and see dislike counts | No, unless also given an internal role | Create public articles | Review/approve/reject public pending articles/updates | Yes, for public articles | Yes, for public articles; published deletion requires MFA | Main-site article management tools only; no Django Admin by default |
| Internal User | Internal viewer add-on | View/vote public articles | View/vote published internal articles | No | No | No | No | No |
| Internal Article Writer | Internal contributor add-on | View/vote public articles | View/vote internal articles | Create internal drafts, submit internal articles, edit own internal drafts/failed submissions, submit pending updates for own internal published articles | No | Own published internal edits become pending updates | Can delete own internal articles when allowed by the owner flow; published deletion requires MFA | No |
| Internal Article Approver | Internal review-only approver | View/vote public articles | View/vote internal articles | No by default | Review, edit during review, approve, or reject pending internal articles/updates | Only during explicit internal review flow; cannot freely edit already-published internal articles | No | No |
| Internal Article Manager | Internal article manager add-on | View/vote public articles and see dislike counts | View/vote internal articles and see dislike counts | Create internal articles | Review/approve/reject internal pending articles/updates | Yes, for internal articles | Yes, for internal articles; published deletion requires MFA | Internal article management tools only; no Django Admin by default |
| Admin Users | Full administrator | Full public access | Full internal access | Can create/publish directly | Can review all scopes | Yes, all scopes | Yes, all scopes; published deletion requires MFA where enforced | Yes; members sync to staff/superuser and must still pass admin MFA/network guards |

### Role interaction rules

| Combination / situation | Result |
|---|---|
| New local or AD user with no elevated role | Automatically receives `Regular User` as fallback public viewer. |
| User receives `Article Writer`, `Article Approver`, or `Article Manager` | `Regular User` is removed because public view access is already included. |
| User receives `Internal User`, `Internal Article Writer`, `Internal Article Approver`, or `Internal Article Manager` | Internal role acts as an add-on and also grants public article viewing. It does not automatically remove unrelated public elevated roles. |
| Public role + internal role | Permissions are additive but scope-separated. Example: `Article Manager` + `Internal Article Approver` can fully manage public articles but only review pending internal articles. |
| Public approver only | Can work from the public pending-review flow but cannot create/delete public articles or freely edit published public articles. |
| Internal approver only | Can work from the internal pending-review flow but cannot create/delete internal articles or freely edit published internal articles. |
| Public manager only | Can manage public articles but cannot access internal articles unless also given an internal role. |
| Internal manager only | Can manage internal articles and view public articles, but does not become public article manager unless also assigned that public role. |
| Admin Users | Full source of truth for administrator access; syncs staff/superuser and covers both public and internal scopes. |
| Disabled User | Highest precedence; removes standard role/admin groups, clears Knowledge Repository direct permissions, unchecks staff/superuser, and blocks site access. |

Group membership is the baseline permission model. Direct user permission checkboxes are add-on permissions only; unticking a direct permission does not remove a permission inherited from a group. Direct permissions do not grant Django Admin access.

Django's built-in `Active` checkbox controls whether the account can sign in at all. `Disabled User` is different: it keeps the account record available but sends an already-authenticated disabled account to the clean disabled-account page with a sign-out button.

Local and AD users are separated by account source metadata, not by email domain. This means a local user can use an email address such as `alice@openkb.local` without being incorrectly treated as an AD user.

## Security Overview

| Area | Security controls |
|---|---|
| Authentication | Login-only site access, local login, AD/LDAPS login, MFA, session timeout, authentication logging |
| Anonymous access | Only the login/language/static support paths are public; normal app pages and hidden admin login return 404 when unauthenticated |
| User separation | Local and AD users are separated by account source; AD-managed password/email changes are blocked locally |
| MFA | Authenticator-app OTP, MFA setup, MFA verification, MFA reset, and sensitive account-change protection |
| Authorization | Group-based roles, public/internal scope checks, enforced role precedence, add-on direct user permissions, admin-only tools, article owner checks, approval workflow, protected image serving, and restricted admin routes |
| Articles | Public/internal visibility, draft/pending/pending failed/published workflow, pending-update review for published edits, duplicate title prevention, scoped approval queues, MFA deletion confirmation, and orphan article management |
| Search and listing | Public search returns public results; internal-capable users can receive public + internal results; internal search is internal-only; title/keyword matching only |
| Keywords | Suggested keywords are manually refreshed and only come from existing manually created article keywords when the exact keyword/phrase appears in the current draft title/body |
| Uploads | Image-only allowlist, file validation, generated filenames, protected serving, and stray upload cleanup |
| Markdown | Sanitized rendered HTML to reduce XSS risk |
| AI chatbot | Login-protected chatbot endpoint, role-scoped public/internal index selection, prompt length limits, 5 questions per 60 seconds, 30-minute cooldown after exceeding the limit, Redis-backed user-ID limiting, concurrency limits, timeout controls, safer error handling, related article recommendations, and activity logging |
| Password/MFA lockout | Progressive lockout policy stored in Site settings, with configurable stages, repeat counts, block durations, and admin reset actions |
| Logging | Separate authentication logs, general activity logs, and admin activity logs with retention cleanup |
| Secrets | Vault-backed Django/database/LDAP/field-encryption/AI secrets |
| Network | Nginx HTTPS reverse proxy, configurable trusted hosts/origins, and optional admin CIDR/VPN restrictions |
| Operations | Cleanup commands, cleanup scheduler, deployment checks, `.dockerignore`, and backup guidance |

## Article Workflow Summary

DjOpenKB keeps approved article content stable while still allowing controlled user updates. The same workflow exists for public and internal articles, but each visibility scope has its own permissions and review access.

```text
New public article by Article Writer:
Draft -> Pending -> Pending failed / Published

New internal article by Internal Article Writer:
Draft -> Pending -> Pending failed / Published

User edits an already published article:
Published article remains visible
Edited version is saved as a pending update
Scope approver/manager/admin approves -> pending update replaces the published article
Scope approver/manager/admin rejects -> published article stays unchanged and feedback is shown to the owner
```

Public and internal scopes are separated:

| Area | Public article | Internal article |
|---|---|---|
| Listing page | `/home/` | `/internal/` |
| Create page | `/suggest/` | `/internal/suggest/` |
| Owner article list | `/profile/articles/` | `/internal/profile/articles/` |
| Pending review | `/profile/admin/pending-articles/` | `/internal/profile/admin/pending-articles/` |
| Search | Public search; internal-capable users may also see internal results from main search | Internal-only search |
| OpenKB storage | Public published files under `openkb-data/` | Internal files kept under `openkb-data-internal/` and kept out of public OpenKB data |
| AI scope | Normal users query public index only | Internal users query internal index containing public + internal published articles |

Draft, pending, pending failed, and unapproved pending-update content is not exposed through article detail traversal. Owners can open their own workflow articles, full admins can open all, and approvers/managers use the explicit review/edit flows for their scope.

## Project Folder Structure

```text
DjOpenKB/
|-- djopenkb/                    # Django project configuration
|-- kb/                          # Main Django application logic
|-- website/                     # Templates and static frontend files
|-- locale/                      # Django translation files
|-- documentations/              # Project setup, deployment, and feature guides
|-- docker/                      # Docker helper scripts
|-- nginx/                       # Nginx HTTPS reverse proxy configuration and cert scripts
|-- vault/                       # Vault configuration, bootstrap files, and local Vault runtime data
|-- redis-data/                  # Optional persistent Redis data if enabled in future deployment tuning
|-- ldap-certs/                  # AD/LDAPS CA certificate mounted into the web container
|-- OpenKB-main/                 # OpenKB source code used by the AI chatbot
|-- openkb-data/                 # Public OpenKB workspace and public article AI data
|-- openkb-data-internal/        # Internal OpenKB workspace for internal article AI data
|-- postgres-data/               # PostgreSQL local persistent data folder
|-- staticfiles/                 # Django collected static files
|-- scripts/                     # Utility and testing scripts
|-- manage.py                    # Django management command entry point
|-- Dockerfile                   # Django web container build file
|-- Dockerfile.postgres-vault    # PostgreSQL image with Vault password support
|-- docker-compose.yml           # Main Docker Compose stack
|-- .env                         # Local non-secret runtime configuration
|-- .env.example                 # Example runtime configuration
`-- requirements.txt             # Python dependencies
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
documentations/WINDOWS_SERVER_2022_AD_LDAPS_SETUP.md
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

Contains the public OpenKB workspace used by the chatbot for public article indexing.

This folder stores the public OpenKB data/index workspace and published public article files used by the AI integration. Internal article data is not stored here.

Before using the chatbot, initialize OpenKB inside this folder:

```bash
docker compose exec web sh -lc "cd /app/openkb-data && PYTHONPATH=/app/OpenKB-main python -m openkb.cli init"
```

Then sync published Django articles into OpenKB. By default this rebuilds both the public index and the internal public+internal index:

```bash
docker compose exec web python manage.py sync_openkb_ai
```

If OpenKB is not initialized, the chatbot may fail or not detect the article data correctly.

---


### `openkb-data-internal/`

Contains the separate internal OpenKB workspace used for internal article indexing.

Internal article Markdown and generated internal AI summaries are kept here instead of being placed into the public `openkb-data/` tree. This prevents the public OpenKB index from accidentally retrieving internal-only content.

The normal sync command rebuilds both public and internal indexes by default:

```bash
docker compose exec web python manage.py sync_openkb_ai
```

You can also sync a specific scope:

```bash
docker compose exec web python manage.py sync_openkb_ai --scope public
docker compose exec web python manage.py sync_openkb_ai --scope internal
docker compose exec web python manage.py sync_openkb_ai --scope all
```

To check that internal articles are not present in the public OpenKB tree:

```bash
docker compose exec web python manage.py check_internal_article_isolation --sync-first
```

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

The chatbot is role-scoped:

| User access | AI data used |
|---|---|
| Public article access only | Public OpenKB index under `openkb-data/` |
| Internal article access | Internal OpenKB index under `openkb-data-internal/`, containing published public + internal articles |

For production, these counters use Redis through `REDIS_URL`, so the limits remain consistent even when Gunicorn runs multiple workers. Local memory cache is only a development/emergency fallback and should not be used for production.

If anonymous chatbot access is ever re-enabled in the future, the fallback rate-limit identity should be the detected client IP address behind the trusted Nginx reverse proxy.

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

Sync OpenKB AI article data. By default this rebuilds both the public index and the internal public+internal index:

```bash
docker compose exec web python manage.py sync_openkb_ai
docker compose exec web python manage.py sync_openkb_ai --scope public
docker compose exec web python manage.py sync_openkb_ai --scope internal
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
openkb-data-internal/
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
openkb-data-internal/.openkb/
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
