# DjOpenKB Full Feature Documentation

This document summarises the implemented features, security controls, deployment-related components, and operational behaviours of DjOpenKB. It is intended to give engineers and reviewers a clear overview of what the system provides and how the main surfaces are protected.

## 1. Project Purpose

DjOpenKB is a Django-based internal knowledge base website integrated with OpenKB AI. It allows users to browse, search, vote on, and suggest wiki articles while admins review and publish content. The platform is designed for controlled local server or intranet-style deployment using Docker Compose, PostgreSQL, Nginx HTTPS, HashiCorp Vault, and optional Windows Active Directory authentication over LDAPS.

The project focuses on secure article management, controlled user contribution, local/offline UI translation, MFA-protected account actions, audit logging, upload validation, and AI-assisted article search.

## 2. Main Runtime Services

The Docker Compose stack contains the following main services:

| Service | Purpose |
|---|---|
| `web` | Django application served by Gunicorn. Handles the website, article workflow, authentication, MFA, OpenKB AI endpoint, and admin tools. |
| `nginx` | Reverse proxy that serves HTTPS on port `8080`, forwards requests to Django, and serves collected static files. |
| `db` | PostgreSQL database used by Django. The database password is loaded from Vault. |
| `vault` | HashiCorp Vault used to store runtime secrets such as Django secret key, PostgreSQL password, Gemini API key, and LDAP bind password. |
| `vault-init` | First-time Vault initialisation and secret seeding helper. |
| `vault-auto-unseal` | Automatically unseals Vault using the stored unseal key in the local lab deployment. |
| `cleanup-scheduler` | Runs scheduled cleanup commands, including stray upload cleanup and authentication log retention cleanup. |

## 3. Authentication and Account Management

### 3.1 Local Django Accounts

Local Django accounts use Django's built-in authentication framework. Local user passwords are stored using Django password hashing. By default, Django uses PBKDF2 password hashing unless explicitly changed in settings. Plaintext local passwords are not stored in the database.

Local accounts can be managed through Django admin and the main website profile page. The normal user profile page does not allow users to change their own username. Username changes are controlled by administrators through Django admin.

### 3.2 Active Directory / LDAP Accounts

The project supports Windows Active Directory sign-in through `django-auth-ldap`. In the current secure configuration, AD authentication uses LDAPS:

```env
LDAP_SERVER_URI=ldaps://WIN-VVCA4BIOSK7.openkb.local:636
LDAP_TLS_REQUIRE_CERT=demand
LDAP_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/ad-ca.crt
```

The Django container validates the Domain Controller certificate using the exported AD CS CA certificate mounted into the container. The LDAP bind password is not stored directly in `.env`; it is stored in Vault.

AD passwords are managed by Active Directory and are not changed inside Django. Django admin displays domain-managed users separately and prevents domain password changes through Django admin.

### 3.3 LDAP Username Handling

LDAP usernames are normalised so common AD login formats map to the same Django account. This prevents the same AD user from being created as multiple Django accounts just because different login formats were used.

### 3.4 Account Types

The `UserProfile` model extends Django users with main-site account metadata:

| Account type | Purpose |
|---|---|
| `User` | Normal local website user. |
| `Admin` | Local admin account with main-site admin privileges. |
| `LDAP user` | Domain-authenticated normal user. |
| `LDAP admin` | Domain-authenticated admin user. |

Admins can also allow or block a user's main-site access through Django admin.

## 4. Multi-Factor Authentication

### 4.1 MFA Requirement

The platform uses TOTP authenticator MFA through `pyotp`. MFA is enforced as part of the login flow for both local and LDAP users.

After the username/password or AD password is accepted, the user is placed into a pending-MFA state. The real authenticated Django session is only completed after the user sets up or verifies MFA.

### 4.2 MFA Setup

When a user first signs in and does not have a confirmed MFA device, the system generates a private TOTP secret and displays a QR code. The user scans the QR code using an authenticator app and confirms setup with a valid OTP code.

The MFA secret is tied to the individual Django user through the `UserMFADevice` model.

### 4.3 MFA Reset

MFA can be reset by the user or by an administrator. When MFA is reset:

- A new random TOTP secret is generated.
- The previous authenticator code becomes invalid.
- Existing sessions for the user are cleared.
- The user must scan a new QR code and complete MFA setup again.
- The new secret is not shown to administrators as a reusable plaintext value.

### 4.4 Sensitive Profile Changes Require MFA

Sensitive profile actions require a fresh MFA/OTP code. For example:

- Changing email for local users.
- Changing password for local users.

Domain-managed email/password values are controlled by Active Directory and are blocked from normal website editing.

## 5. Session and Cookie Security

### 5.1 Session Timeout

Authenticated sessions are controlled by a site setting:

```text
session_timeout_days = 30 by default
```

If the timeout expires, the user is logged out and must sign in again. A value of `0` makes the browser session expire when the browser closes.

### 5.2 Secure Cookies

When `DJANGO_DEBUG=false`, the project enables secure deployment cookie settings:

- `SESSION_COOKIE_SECURE=True`
- `CSRF_COOKIE_SECURE=True`
- `LANGUAGE_COOKIE_SECURE=True`
- `SESSION_COOKIE_HTTPONLY=True`
- `CSRF_COOKIE_HTTPONLY=True`
- `SESSION_COOKIE_SAMESITE=Lax`
- `CSRF_COOKIE_SAMESITE=Lax`

This means session and CSRF cookies are protected for HTTPS deployment.

### 5.3 Cache Control After Logout/MFA

Authentication and MFA pages use strict no-cache headers to reduce the chance of browser back/forward cache showing stale authenticated pages after logout or MFA reset.

## 6. CSRF and Request Protection

Django CSRF middleware is enabled. Normal forms use CSRF tokens, and the OpenKB AI POST endpoint is called from the frontend with a CSRF token.

Important protections include:

- `CsrfViewMiddleware` enabled.
- POST-only endpoints for state-changing actions.
- Safe redirect validation using Django's `url_has_allowed_host_and_scheme`.
- Secure CSRF cookie settings when debug is off.

## 7. Article Management

### 7.1 Suggested Article Workflow

Users can suggest articles through the website. Suggested articles are stored in the database and mirrored into the OpenKB data folder when needed.

Article states include:

| Status | Meaning |
|---|---|
| `Draft` | User can edit before submitting. |
| `Pending` | Submitted for admin review. |
| `Pending failed` | Returned by admin with review comments. |
| `Published` | Approved and visible in the public article list. |

Normal users cannot self-approve articles. Admins can review and publish articles through the admin workflow.

### 7.2 Admin Review Notes and History

When an article is returned as pending failed, admins can enter review notes. The current review note is shown to the article owner when the article is in draft or pending failed status.

Review notes are also stored in history, so previous feedback rounds are preserved for audit and review tracking.

### 7.3 Duplicate Article Title Protection

Article titles are checked using normalised comparison:

- Case-insensitive.
- Leading/trailing spaces ignored.
- Repeated internal whitespace treated as the same.

This prevents duplicate titles such as:

```text
Password Reset Guide
password reset guide
 Password   Reset   Guide
```

from being created as separate articles.

### 7.4 Article File Sync

Published/suggested article content is written to OpenKB-compatible Markdown files under the OpenKB data structure. Internal generated metadata is removed from public display, search snippets, and AI output so users do not see sync markers.

## 8. Article Browsing, Search, Views, and Trending

### 8.1 Article Listing and Search

The main website allows users to browse and search published articles. Draft, pending, and failed articles are not publicly visible unless the current user owns the article or has admin permission.

### 8.2 View Counts

Each article stores a `view_count`. Views are tracked per user session to avoid simply refreshing the same article repeatedly to increase the count.

### 8.3 Trending Articles

Trending articles are based on higher view counts. This allows commonly accessed articles to appear more prominently.

### 8.4 Voting

Signed-in users can vote on published articles:

- Helpful / thumbs up.
- Not helpful / thumbs down.
- One vote per user per article.
- Users can change or remove their vote.

Helpful counts are visible to users. Admins can review vote details through Django admin.

## 9. Upload and Image Security

### 9.1 Allowed Image Types

Article image uploads are restricted to:

```text
.png
.jpg
.jpeg
.gif
.webp
```

### 9.2 Upload Size Limit

Uploaded article images are limited to:

```text
2 MB maximum per image
```

### 9.3 Pillow Image Verification

The project does not trust the browser-provided MIME type alone. Uploaded files are opened and verified using Pillow. This helps reject non-image files renamed with an image extension.

### 9.4 Pixel Limit

The image validation also checks image dimensions and rejects images above the configured pixel limit. This helps reduce the risk of oversized image processing abuse.

### 9.5 Server-Generated Filenames

Uploaded images are stored using generated filenames containing a timestamp and random component. The original filename is not used directly as the storage path.

### 9.6 Path Traversal Protection

Uploaded and imported filenames are normalised. Path traversal patterns such as `../` are rejected or reduced to safe filename-only values.

### 9.7 Protected Image Serving

The project does not expose the whole OpenKB uploads folder as a raw static directory. Images are served through a Django view that checks filenames and article visibility rules.

## 10. Stray Upload File Cleanup

### 10.1 Manual Cleanup

Admins have access to a clean stray upload files tool. It finds uploaded files that are no longer referenced by any article or Markdown file.

The admin cleanup page allows review before deletion so admins can avoid removing files that should be kept.

### 10.2 Automatic Cleanup

The `cleanup-scheduler` Docker service runs scheduled cleanup commands. By default, the cleanup interval is 24 hours:

```text
CLEANUP_INTERVAL_SECONDS=86400
```

The default stray upload minimum age is also 24 hours:

```text
stray_upload_cleanup_min_age_minutes = 1440
```

This prevents newly uploaded images from being deleted while a user is still drafting an article.

### 10.3 Upload Audit Log

Article image uploads are logged in `ArticleImageUploadLog`. The log records details such as:

- Generated filename.
- Original filename.
- Content type.
- Size.
- Uploader snapshot.
- Upload time.
- Upload IP address.
- User agent.
- Deletion reason when deleted.

## 11. Markdown and XSS Protection

Article Markdown is converted into HTML using `markdown`, then sanitised using `bleach` before display.

This protects article pages from unsafe HTML and script injection. Only approved HTML tags/attributes/protocols are allowed through the sanitisation process.

The article display template can safely render the sanitised HTML because the input has already passed through the controlled Markdown and Bleach pipeline.

## 12. OpenKB AI Integration

### 12.1 OpenKB CLI Integration

The project integrates with OpenKB through the local `OpenKB-main` folder and the `openkb-data` data folder.

The OpenKB data folder must be initialised before the chatbot is used:

```bash
docker compose exec web sh -lc "cd /app/openkb-data && PYTHONPATH=/app/OpenKB-main python -m openkb.cli init"
```

Then articles can be synced for AI usage:

```bash
docker compose exec web python manage.py sync_openkb_ai
```

If OpenKB is not initialised, the chatbot may return errors because the expected OpenKB data structure is missing.

### 12.2 AI Provider

The AI provider is configured through environment settings:

```env
OPENKB_AI_PROVIDER=openkb-cli
OPENKB_GEMINI_MODEL=gemini/gemini-2.5-flash
```

The Gemini API key is stored in Vault, not directly in source code.

### 12.3 AI Endpoint Safety Limits

The Ask OpenKB AI endpoint includes limits such as:

- Maximum prompt length.
- Request rate limiting.
- Temporary blocking after too many requests.
- Timeout handling for OpenKB CLI calls.
- Error sanitisation before returning messages to users.

Current defaults in settings:

```text
OPENKB_AI_MAX_PROMPT_CHARS = 1000
OPENKB_AI_RATE_LIMIT_MAX_REQUESTS = 5
OPENKB_AI_RATE_LIMIT_WINDOW_SECONDS = 60
OPENKB_AI_RATE_LIMIT_BLOCK_SECONDS = 300
```

### 12.4 Related Article Recommendations

The AI endpoint can recommend relevant published articles from the local database. Related article logic avoids showing random articles for simple greetings or unrelated filler messages.

Only published articles are used for public AI recommendations.

### 12.5 Output Cleanup

OpenKB internal metadata and generated sync markers are removed before display. This prevents implementation details such as generated article metadata from leaking into article snippets or AI responses.

## 13. Internationalisation and Local Translation

The UI uses Django's local translation system through `.po` and `.mo` locale files. Translation is local/offline and does not call an external AI translator.

Supported language choices are configured in `settings.py` and exposed through the language selector. Anonymous users store language preference in a cookie. Logged-in users also save the preference in their user profile.

This design keeps UI translation independent from the AI chatbot and avoids sending translation content to external AI services.

## 14. Admin Tools and Access Control

### 14.1 Admin Tool Restriction

Admin tools are protected by explicit admin checks. Staff status alone is not enough for main-site admin tools. A user must be a superuser or have an admin-type `UserProfile`.

Non-admin users receive 404 responses for admin-only main-site tools to reduce route discovery usefulness.

### 14.2 Main Admin Tools

Admin tools include:

- Clean stray upload files.
- Bulk import/export articles.
- Manage pending articles.
- Review suggested articles.
- View audit records through Django admin.

### 14.3 Article Import/Export

Bulk import/export supports article content and referenced upload files. Zip member names are normalised to avoid unsafe paths. Duplicate article titles are detected during import.

## 15. Logging and Monitoring

### 15.1 Authentication Activity Logs

Authentication and MFA events are logged in `AuthActivityLog`. The log captures:

- Event type.
- Success/failure.
- Username.
- Login mode.
- User reference when available.
- IP address.
- User agent.
- Request path and method.
- Extra event details.

These logs help admins review suspicious login patterns, repeated failures, MFA resets, and unusual source IPs.

### 15.2 Read-Only Admin Log View

`AuthActivityLog` is read-only in Django admin. Admin users can search and filter logs, but cannot manually add, change, or delete them from the admin interface.

Retention/deletion is controlled through the configured cleanup command instead of manual editing.

### 15.3 Log Retention

Authentication activity log retention is controlled by site setting:

```text
auth_activity_log_retention_days = 30 by default
```

The scheduled cleanup service runs the cleanup command automatically.

## 16. Secrets Management with Vault

HashiCorp Vault is used to store sensitive runtime values, including:

- `DJANGO_SECRET_KEY`
- `POSTGRES_PASSWORD`
- `GEMINI_API_KEY`
- `LDAP_BIND_PASSWORD`

The `.env` file should contain non-secret runtime configuration only. Passwords and API keys should be stored in `vault/bootstrap/djopenkb.env` only for first-time Vault seeding, then removed from shared/exported packages.

Vault encrypts stored secrets at rest and gives the application access through the configured Vault token file. The project does not rely on hardcoded production secrets in source code.

## 17. LDAPS Security

The project supports Active Directory authentication over LDAPS on port 636. LDAPS protects LDAP bind credentials in transit using TLS.

In the current lab configuration, LDAPS testing confirmed:

- DNS resolution from the Docker container to the Domain Controller.
- TLS handshake success.
- TLS 1.3 negotiation.
- Certificate subject matching the Domain Controller hostname.
- Certificate issuer matching the AD CS CA.

The encryption strength depends on the TLS cipher negotiated by the server and client. The important implementation point is that the project validates the server certificate and does not send LDAP bind credentials over plaintext LDAP in secure mode.

## 18. HTTPS and Nginx Security Headers

Nginx serves the application over HTTPS on port `8080`. The project includes security headers such as:

- `Strict-Transport-Security`
- `X-Content-Type-Options`
- `X-Frame-Options`
- `Referrer-Policy`
- `Permissions-Policy`
- `Content-Security-Policy`

The local lab deployment can use a locally generated Nginx certificate. For a real public deployment, a trusted certificate should be used.

## 19. Robot.txt and Sitemap Decision

The project is intended for local server, lab, or internal intranet deployment. It does not require public search engine indexing.

Because of this, `robots.txt` and sitemap generation are not a core requirement. Access control is handled by Django views and authentication checks rather than relying on crawler instructions.

## 20. Dependency Pinning

The project pins exact Python package versions in `requirements.txt` to reduce unexpected breakage from upstream updates.

Current pinned versions:

```text
Django==6.0.5
gunicorn==26.0.0
Markdown==3.10
bleach==6.3.0
Pillow==12.0.0
python-dotenv==1.2.1
django-auth-ldap==5.2.0
psycopg[binary]==3.3.2
pyotp==2.9.0
qrcode[pil]==8.2
```

This helps ensure the same behaviour across developer machines and deployment servers.

## 21. Database and Storage

### 21.1 PostgreSQL

PostgreSQL is the default database. The database credentials are provided through Docker Compose and Vault.

### 21.2 SQLite Fallback

`USE_SQLITE=true` exists only as a local fallback for quick testing outside the normal Docker/PostgreSQL deployment. The intended deployment uses PostgreSQL.

### 21.3 Article Storage

Article metadata is stored in PostgreSQL. Article Markdown content is also mirrored into OpenKB-compatible folders so OpenKB can index and use it.

## 22. Main Security Controls Summary

| Area | Implemented control |
|---|---|
| Password storage | Django password hashing for local users. AD passwords managed by Active Directory. |
| MFA | TOTP MFA required after password/AD authentication. |
| Sensitive profile changes | Fresh MFA/OTP required for sensitive local profile changes. |
| Sessions | Configurable session timeout and secure cookie settings. |
| CSRF | Django CSRF middleware and token-protected POST forms/endpoints. |
| XSS | Markdown rendered then sanitised with Bleach. |
| Upload safety | Extension allowlist, 2 MB size limit, Pillow image verification, pixel limit, generated filenames. |
| Access control | Article visibility checks, admin-only tools, 404 for non-admin admin-tool access. |
| Secrets | Runtime secrets stored in Vault instead of source code. |
| LDAP | LDAPS with certificate validation for AD integration. |
| HTTPS | Nginx HTTPS and security headers. |
| Logs | Read-only auth/MFA logs with IP/user-agent details and retention cleanup. |
| AI endpoint | Prompt length limit, rate limiting, timeout handling, output cleanup, local related article fallback. |
| Dependencies | Exact package versions pinned in `requirements.txt`. |

## 23. Files That Should Not Be Shared

The following files/folders may contain secrets, tokens, generated keys, or local runtime data and should not be included in public repositories or submission packages:

```text
.env
vault/bootstrap/djopenkb.env
vault/keys/*
vault/file/*
openkb-data/.env
nginx/certs/*.key
postgres-data/*
```

The `.gitignore` should continue to exclude these sensitive/generated files.

## 24. Useful Verification Commands

Run Django deployment checks:

```bash
docker compose exec web python manage.py check --deploy
```

Test LDAPS from the Django container:

```bash
docker compose exec web sh scripts/test_ldaps.sh
```

Initialise OpenKB data:

```bash
docker compose exec web sh -lc "cd /app/openkb-data && PYTHONPATH=/app/OpenKB-main python -m openkb.cli init"
```

Sync articles to OpenKB AI:

```bash
docker compose exec web python manage.py sync_openkb_ai
```

Run automatic cleanup manually:

```bash
docker compose exec web python manage.py cleanup_stray_upload_files --noinput
docker compose exec web python manage.py cleanup_auth_activity_logs --noinput
```

## 25. Final Notes

DjOpenKB is designed as a secure internal knowledge base and cyber security project. The current implementation covers authentication, MFA, LDAPS, HTTPS, CSRF, upload validation, Markdown sanitisation, audit logging, article review workflow, and OpenKB AI integration.

For a controlled local or intranet deployment, the implemented controls are suitable as long as secrets are not shared, Vault is seeded correctly, LDAPS certificates are mounted correctly, and debug mode remains off.
