# DjOpenKB Full Feature Documentation

This document summarises the implemented features, security controls, deployment-related components, role permissions, logging coverage, and operational behaviours of DjOpenKB. It is intended to give engineers, reviewers, and future administrators a clear overview of what the system provides and how the main surfaces are protected.

## 1. Project Purpose

DjOpenKB is a Django-based internal knowledge base website integrated with OpenKB AI. It allows users to browse, search, vote on, and suggest wiki articles while administrators review and publish content. The platform is designed for a controlled local server or intranet-style deployment using Docker Compose, PostgreSQL, Nginx HTTPS, HashiCorp Vault, and optional Windows Active Directory authentication over LDAPS.

The project focuses on secure article management, controlled user contribution, local/offline UI translation, MFA-protected account actions, audit logging, upload validation, OpenKB-compatible article synchronisation, and AI-assisted article search.

## 2. Main Runtime Services

The Docker Compose stack contains the following main services:

| Service | Purpose |
|---|---|
| `web` | Django application served by Gunicorn. Handles the website, article workflow, authentication, MFA, OpenKB AI endpoint, logging, and admin tools. |
| `nginx` | Reverse proxy that serves HTTPS on port `8080`, forwards requests to Django, and serves collected static files. |
| `db` | PostgreSQL database used by Django. The database password is loaded from Vault. |
| `vault` | HashiCorp Vault used to store runtime secrets such as Django secret key, PostgreSQL password, Gemini API key, and LDAP bind password. |
| `vault-init` | First-time Vault initialisation and secret seeding helper. |
| `vault-auto-unseal` | Automatically unseals Vault using the stored unseal key in the local lab deployment. |
| `cleanup-scheduler` | Runs scheduled cleanup commands, including stray upload cleanup, authentication log cleanup, and general activity log cleanup. |

## 3. User Types and Permission Summary

DjOpenKB separates users by both authentication source and permission level. To keep the permission model clear, the main website roles can be grouped into three practical access levels: anonymous visitors, authenticated users, and main-site administrators. Local and Active Directory accounts may share similar website permissions, but their account management rules are different.

### 3.1 Main Website Access Levels

| Access level | Applies to | Main permissions | Restrictions |
|---|---|---|---|
| Anonymous visitor | Not signed in | Can browse published articles and use the OpenKB AI chatbot. | Cannot vote, suggest articles, edit content, access profile features, or use admin tools. |
| Authenticated user | Local user or AD / LDAP user | Can browse published articles, vote on articles, suggest articles, and edit their own draft or pending failed articles. | Cannot approve/publish other users' articles or access admin tools. |
| Main-site administrator | Local admin or AD / LDAP admin | Can access admin tools, review pending articles, publish/return suggested articles, manage orphan articles, run import/export, and perform maintenance actions. | Django admin access still requires the correct Django staff/superuser/admin permission. |

### 3.2 Account Source Differences

The `UserProfile` model stores the account source, so the system does not guess whether a user is local or AD-managed based on email domain alone. This prevents a local user with an email such as `alice@openkb.local` from being incorrectly treated as an AD account.

| Account source | Password owner | Email owner | Profile password change | Profile email change |
|---|---|---|---|---|
| Local account | Django | Django/local admin | Allowed with fresh MFA/OTP | Allowed with fresh MFA/OTP |
| Active Directory account | Active Directory | Active Directory/domain admin | Blocked in Django | Blocked in Django |

### 3.3 Account Types

The profile layer tracks the main account type for permission and display purposes.

| Account type | Source | Website role | Purpose |
|---|---|---|---|
| `User` | Local Django account | Authenticated user | Normal local website contributor. |
| `Admin` | Local Django account | Main-site administrator | Local administrator with main-site admin privileges. |
| `LDAP user` | Active Directory / LDAPS | Authenticated user | Domain-authenticated contributor. |
| `LDAP admin` | Active Directory / LDAPS | Main-site administrator | Domain-authenticated administrator with main-site admin privileges. |

Admins can also allow or block a user's main-site access through Django admin.

### 3.4 Role Enforcement

Main-site admin tools require explicit admin checks. A normal `staff` flag alone is not treated as sufficient for main-site admin tools unless the user also has the correct main-site admin profile or superuser permissions.

Non-admin users receive a 404 response for protected main-site admin tools to reduce route discovery usefulness.

## 4. Authentication and Account Management

### 4.1 Local Django Accounts

Local Django accounts use Django's built-in authentication framework. Local user passwords are stored using Django password hashing. Plaintext local passwords are not stored in the database.

Local accounts can be managed through Django admin and the main website profile page. The normal user profile page does not allow users to change their own username. Username changes are controlled by administrators through Django admin.

### 4.2 Active Directory / LDAP Accounts

The project supports Windows Active Directory sign-in through `django-auth-ldap`. In the secure configuration, AD authentication uses LDAPS:

```env
LDAP_SERVER_URI=ldaps://WIN-VVCA4BIOSK7.openkb.local:636
LDAP_TLS_REQUIRE_CERT=demand
LDAP_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/ad-ca.crt
```

The Django container validates the Domain Controller certificate using the exported AD CS CA certificate mounted into the container. The LDAP bind password is not stored directly in `.env`; it is stored in Vault.

AD passwords are managed by Active Directory and are not changed inside Django. Django admin displays domain-managed users separately and prevents domain password changes through Django admin.

### 4.3 LDAP Username Handling

LDAP usernames are normalised so common AD login formats map to the same Django account. This prevents the same AD user from being created as multiple Django accounts just because different login formats were used.

Common examples include:

```text
alice
OPENKB\alice
alice@openkb.local
```

These should map to the intended single Django-side AD user identity.

### 4.4 Account Types

The profile layer tracks the main account type:

| Account type | Purpose |
|---|---|
| `User` | Normal local website user. |
| `Admin` | Local admin account with main-site admin privileges. |
| `LDAP user` | Domain-authenticated normal user. |
| `LDAP admin` | Domain-authenticated admin user. |

Admins can also allow or block a user's main-site access through Django admin.

## 5. Multi-Factor Authentication

### 5.1 MFA Requirement

The platform uses TOTP authenticator MFA through `pyotp`. MFA is enforced as part of the login flow for both local and LDAP users.

After the username/password or AD password is accepted, the user is placed into a pending-MFA state. The real authenticated Django session is only completed after the user sets up or verifies MFA.

### 5.2 MFA Setup

When a user first signs in and does not have a confirmed MFA device, the system generates a private TOTP secret and displays a QR code. The user scans the QR code using an authenticator app and confirms setup with a valid OTP code.

The MFA secret is tied to the individual Django user through the `UserMFADevice` model.

### 5.3 MFA Reset

MFA can be reset by the user or by an administrator. When MFA is reset:

- A new random TOTP secret is generated.
- The previous authenticator code becomes invalid.
- Existing sessions for the user are cleared.
- The user must scan a new QR code and complete MFA setup again.
- The new secret is not shown to administrators as a reusable plaintext value.

### 5.4 Sensitive Profile Changes Require MFA

Sensitive profile actions require a fresh MFA/OTP code. For example:

- Changing email for local users.
- Changing password for local users.

Domain-managed email/password values are controlled by Active Directory and are blocked from normal website editing.

## 6. Session and Cookie Security

### 6.1 Session Timeout

Authenticated sessions are controlled by a site setting:

```text
session_timeout_days = 30 by default
```

If the timeout expires, the user is logged out and must sign in again. A value of `0` makes the browser session expire when the browser closes.

### 6.2 Secure Cookies

When `DJANGO_DEBUG=false`, the project enables secure deployment cookie settings:

- `SESSION_COOKIE_SECURE=True`
- `CSRF_COOKIE_SECURE=True`
- `LANGUAGE_COOKIE_SECURE=True`
- `SESSION_COOKIE_HTTPONLY=True`
- `CSRF_COOKIE_HTTPONLY=True`
- `SESSION_COOKIE_SAMESITE=Lax`
- `CSRF_COOKIE_SAMESITE=Lax`

This means session and CSRF cookies are protected for HTTPS deployment.

### 6.3 Cache Control After Logout/MFA

Authentication and MFA pages use strict no-cache headers to reduce the chance of browser back/forward cache showing stale authenticated pages after logout or MFA reset.

## 7. CSRF and Request Protection

Django CSRF middleware is enabled. Normal forms use CSRF tokens, and the OpenKB AI POST endpoint is called from the frontend with a CSRF token.

Important protections include:

- `CsrfViewMiddleware` enabled.
- POST-only endpoints for state-changing actions.
- Safe redirect validation using Django's `url_has_allowed_host_and_scheme`.
- Secure CSRF cookie settings when debug is off.
- `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` are passed into Docker Compose for safer deployment configuration.

## 8. Article Management

### 8.1 Suggested Article Workflow

Users can suggest articles through the website. Suggested articles are stored in the database and mirrored into the OpenKB data folder when needed.

Article states include:

| Status | Meaning |
|---|---|
| `Draft` | User can edit before submitting. |
| `Pending` | Submitted for admin review. |
| `Pending failed` | Returned by admin with review comments. |
| `Published` | Approved and visible in the public article list. |

Normal users cannot self-approve articles. Admins can review and publish articles through the admin workflow. Admin-created or admin-published content can bypass normal user approval flow where appropriate.

For a new normal-user article, the normal workflow is:

```text
Draft → Pending → Pending failed / Published
```

For an already published article edited by a normal user, the live article is not overwritten immediately. The proposed update is stored separately and sent for admin review.

### 8.2 Published Article Update Review

When a normal user edits an already published article, the current published version remains accessible to readers. The edited version is saved as a pending update instead of immediately replacing the live article.

Pending update data is stored separately from the public article content, including:

```text
pending update title
pending update body
pending update keywords
pending update image references
pending update review status
```

The pending update workflow is:

```text
Published article remains visible
Author submits edited version
Edited version becomes Pending update
Admin approves → pending update replaces the live published article
Admin rejects → live published article remains unchanged and update feedback is shown to the author
```

This design prevents unapproved edits from replacing already approved knowledge-base content. It also allows users to continue accessing the last approved article while the update is waiting for review.

### 8.3 Admin Review Notes and History

When an article is returned as pending failed, admins can enter review notes. The current review note is shown to the article owner when the article is in draft or pending failed status.

Review notes are also stored in history, so previous feedback rounds are preserved for audit and review tracking.

### 8.4 Duplicate Article Title Protection

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

### 8.5 Article File Sync

Published article content is written to OpenKB-compatible Markdown files under the OpenKB data structure. Pending updates are not written as the public article version until an admin approves them. Internal generated metadata is removed from public display, search snippets, and AI output so users do not see sync markers.

## 9. Orphan Article Management

Admins have access to an orphan article management tool for articles that have no active owner, no owner, or a deleted/inactive owner.

The tool supports:

- Scanning for orphan articles.
- Searching orphan articles.
- Viewing article details before action.
- Selecting one or multiple orphan articles.
- Assigning selected articles to an active user.
- Deleting selected orphan articles.
- Confirmation before assign/delete actions.
- Safe error handling for wrong usernames, missing selection, invalid target users, stale article IDs, and unexpected failures.

The assign-user field supports typing/searching by username or email so the admin does not need to scroll through a very large user list.

## 10. Article Browsing, Search, Views, and Trending

### 10.1 Article Listing and Search

The main website allows users to browse and search published articles. Draft, pending, pending failed, and unapproved pending-update content are not publicly visible unless the current user owns the article or has admin permission.

Search bars on the home and article pages can show a title-only dropdown of possible published article matches while the user types. The dropdown displays clickable article titles only, while the normal search button and Enter key still perform a full article search.

### 10.2 View Counts

Each article stores a `view_count`. Views are tracked per user session to avoid simply refreshing the same article repeatedly to increase the count.

### 10.3 Trending Articles

Trending articles are based on higher view counts. This allows commonly accessed articles to appear more prominently.

### 10.4 Voting

Signed-in users can vote on published articles:

- Helpful / thumbs up.
- Not helpful / thumbs down.
- One vote per user per article.
- Users can change or remove their vote.

Helpful counts are visible to users. Admins can review vote details through Django admin and through activity logging.

## 11. Upload and Image Security

### 11.1 Allowed Image Types

Article image uploads are restricted to:

```text
.png
.jpg
.jpeg
.gif
.webp
```

### 11.2 Upload Size Limit

Uploaded article images are limited to:

```text
2 MB maximum per image
```

### 11.3 Pillow Image Verification

The project does not trust the browser-provided MIME type alone. Uploaded files are opened and verified using Pillow. This helps reject non-image files renamed with an image extension.

### 11.4 Pixel Limit

The image validation also checks image dimensions and rejects images above the configured pixel limit. This helps reduce the risk of oversized image processing abuse.

### 11.5 Server-Generated Filenames

Uploaded images are stored using generated filenames containing a timestamp and random component. The original filename is not used directly as the storage path.

### 11.6 Path Traversal Protection

Uploaded and imported filenames are normalised. Path traversal patterns such as `../` are rejected or reduced to safe filename-only values.

### 11.7 Protected Image Serving

The project does not expose the whole OpenKB uploads folder as a raw static directory. Images are served through a Django view that checks filenames and article visibility rules.

### 11.8 Upload Audit Log

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

## 12. Stray Upload File Cleanup

### 12.1 Manual Cleanup

Admins have access to a clean stray upload files tool. It finds uploaded files that are no longer referenced by any article or Markdown file.

The admin cleanup page allows review before deletion so admins can avoid removing files that should be kept. The cleanup logic checks both live article content and pending-update content so images used only by a pending update are not incorrectly treated as stray files.

### 12.2 Automatic Cleanup

The `cleanup-scheduler` Docker service runs scheduled cleanup commands. By default, the cleanup interval is 24 hours:

```text
CLEANUP_INTERVAL_SECONDS=86400
```

The default stray upload minimum age is also 24 hours:

```text
stray_upload_cleanup_min_age_minutes = 1440
```

This prevents newly uploaded images from being deleted while a user is still drafting an article.

## 13. Markdown and XSS Protection

Article Markdown is converted into HTML using `markdown`, then sanitised using `bleach` before display.

This protects article pages from unsafe HTML and script injection. Only approved HTML tags/attributes/protocols are allowed through the sanitisation process.

The article display template can safely render the sanitised HTML because the input has already passed through the controlled Markdown and Bleach pipeline.

## 14. OpenKB AI Integration

### 14.1 OpenKB CLI Integration

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

### 14.2 AI Provider

The AI provider is configured through environment settings:

```env
OPENKB_AI_PROVIDER=openkb-cli
OPENKB_GEMINI_MODEL=gemini/gemini-2.5-flash
```

The Gemini API key is stored in Vault, not directly in source code.

### 14.3 AI Endpoint Safety Limits and Rate Limiting

The Ask OpenKB AI endpoint includes limits such as:

- Maximum prompt length.
- Request rate limiting.
- Temporary blocking after too many requests.
- Timeout handling for OpenKB CLI calls.
- Error sanitisation before returning messages to users.
- Prompt preview redaction before storing in activity logs.

Current defaults in settings:

```text
OPENKB_AI_MAX_PROMPT_CHARS = 1000
OPENKB_AI_RATE_LIMIT_MAX_REQUESTS = 5
OPENKB_AI_RATE_LIMIT_WINDOW_SECONDS = 60
OPENKB_AI_RATE_LIMIT_BLOCK_SECONDS = 1800
```

This means each rate-limit identity can send up to 5 AI questions within 60 seconds. If the limit is exceeded, that identity is temporarily blocked from using the chatbot for 1800 seconds, which is 30 minutes.

The rate-limit identity is selected based on authentication state:

| Visitor type | Rate-limit identity | Behaviour |
|---|---|---|
| Logged-in local user | Django user ID | The limit follows the authenticated user account, even if the user refreshes the browser, opens another tab, or logs in again from the same browser. |
| Logged-in AD / LDAP user | Django user ID | The limit follows the Django-side account created for the domain user. |
| Anonymous visitor | IP address | The limit follows the detected client IP address. Incognito mode or a new browser session does not bypass the limit unless the client IP changes. |

The rate limit is intentionally not based on the browser session. This prevents a user from bypassing the limit simply by refreshing the browser, opening another tab, or starting a new private browsing session.

For a company intranet deployment, this design keeps anonymous chatbot access available while still providing basic abuse protection. Logged-in users are limited by their own user ID so that a shared office, VPN, Wi-Fi, or proxy IP address does not accidentally block all authenticated staff members.

If the site is deployed behind Nginx or another reverse proxy, client IP detection should continue to use the trusted reverse proxy header configuration so anonymous IP-based rate limiting and activity logs remain accurate.

### 14.4 Related Article Recommendations

The AI endpoint can recommend relevant published articles from the local database. Related article logic avoids showing random articles for simple greetings or unrelated filler messages.

Only published articles are used for public AI recommendations.

### 14.5 Output Cleanup

OpenKB internal metadata and generated sync markers are removed before display. This prevents implementation details such as generated article metadata from leaking into article snippets or AI responses.

## 15. Internationalisation and Local Translation

The UI uses Django's local translation system through `.po` and `.mo` locale files. Translation is local/offline and does not call an external AI translator.

Supported language choices are configured in `settings.py` and exposed through the language selector. Anonymous users store language preference in a cookie. Logged-in users also save the preference in their user profile.

The locale files have been updated across all supported languages so extracted UI strings have translations and compiled `.mo` files. This includes newer admin-tool labels, orphan article workflow messages, MFA text, activity logging labels, and profile/account-source messages.

This design keeps UI translation independent from the AI chatbot and avoids sending translation content to external AI services.

## 16. Admin Tools and Access Control

### 16.1 Admin Tool Restriction

Admin tools are protected by explicit admin checks. Staff status alone is not enough for main-site admin tools. A user must be a superuser or have an admin-type `UserProfile`.

Non-admin users receive 404 responses for admin-only main-site tools to reduce route discovery usefulness.

### 16.2 Main Admin Tools

Admin tools include:

- Clean stray upload files.
- Bulk import/export articles.
- Manage pending articles.
- Review suggested articles.
- Scan and manage orphan articles.
- View authentication activity logs through Django admin.
- View general activity logs through Django admin.
- View upload audit records through Django admin.

### 16.3 Article Import/Export

Bulk import/export supports article content and referenced upload files. Zip member names are normalised to avoid unsafe paths. Duplicate article titles are detected during import.

The export package is an administrator backup/migration file. It includes the actual article data needed to restore the knowledge base, such as:

```text
article title
article body / Markdown content
keywords
published status and workflow status
pending update title/body/keywords when present
review notes and review history when present
referenced article image files
metadata needed for OpenKB file sync
```

The export process supports both normal export and split export. If the export becomes large, the system can generate an outer split package containing multiple importable part ZIP files. Each inner part ZIP is intended to stay below the import upload limit.

Current size behaviour:

```text
Target export part size: about 95 MB per part
Import upload limit: 100 MB per ZIP file
Import uncompressed safety limit: about 200 MB
Article image upload limit: 2 MB per image
```

When restoring from a split export, the admin should extract the outer package first and import each part ZIP one at a time. Import restores article keywords as well as article body content, and published imports are synced back into the OpenKB-compatible Markdown files.

### 16.4 Django Admin Usability

Django admin pages scroll normally in the browser. For log-heavy pages:

- Log list pages use pagination.
- Activity log and authentication log admin pages can show up to 500 rows per page.
- Wide admin tables support horizontal scrolling to avoid squeezing columns.
- `list_max_show_all` is limited to reduce accidental extremely large admin page loads.

## 17. Logging and Monitoring

### 17.1 Authentication Activity Logs

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

Examples of authentication events:

| Event category | Examples |
|---|---|
| Password login | Success, failure, invalid local credentials, invalid AD credentials |
| MFA | Setup success/failure, verify success/failure, pending MFA created |
| Session/security | Logout, session invalidation, forced MFA reset |
| Admin MFA management | Admin reset for selected user/device |

### 17.2 General Activity Logs

General site and content actions are logged in `ActivityLog`. This is separate from authentication logs so admins can review content and usage behaviour without mixing it with login/MFA activity.

Examples of logged activity include:

| Area | Example activity |
|---|---|
| Articles | Article created, updated, submitted, approved, published, returned as pending failed, deleted |
| Views | Article viewed once per browser session |
| Votes | Vote up, vote down, vote changed, vote removed |
| Uploads | Image uploaded, image deleted, stray upload cleanup |
| AI | OpenKB AI question metadata, rate limit events, redacted prompt preview |
| Imports/exports | Bulk article import, bulk article export |
| Admin tools | Orphan article assigned, orphan article deleted, pending article admin action |
| Django admin | Admin article save/delete/bulk actions where applicable |

### 17.3 Log IP Handling

IP logging prefers trusted reverse proxy headers such as `X-Real-IP` from Nginx instead of blindly trusting the first value from `X-Forwarded-For`. This improves accuracy for internal deployments behind the configured reverse proxy.

### 17.4 Read-Only Admin Log Views

`AuthActivityLog` and general `ActivityLog` are intended to be read-only in Django admin. Admin users can search and filter logs, but should not manually add or edit them from the admin interface.

Retention/deletion is controlled through cleanup commands instead of manual editing.

### 17.5 Log Retention

Authentication activity log retention is controlled by site setting:

```text
auth_activity_log_retention_days = 30 by default
```

General activity log retention is controlled by site setting:

```text
activity_log_retention_days = 30 by default
```

A value of `0` keeps logs forever. If the value is set to `30`, cleanup deletes only logs older than 30 days. If the value is increased later, future cleanup follows the new value, but logs that were already deleted cannot be restored.

### 17.6 Log Cleanup

The scheduled cleanup service can run log cleanup automatically. Cleanup commands can also be run manually:

```bash
docker compose exec web python manage.py cleanup_auth_activity_logs --dry-run
docker compose exec web python manage.py cleanup_auth_activity_logs --noinput

docker compose exec web python manage.py cleanup_activity_logs --dry-run
docker compose exec web python manage.py cleanup_activity_logs
```

## 18. Secrets Management with Vault

HashiCorp Vault is used to store sensitive runtime values, including:

- `DJANGO_SECRET_KEY`
- `POSTGRES_PASSWORD`
- `GEMINI_API_KEY`
- `LDAP_BIND_PASSWORD`

The `.env` file should contain non-secret runtime configuration only. Passwords and API keys should be stored in `vault/bootstrap/djopenkb.env` only for first-time Vault seeding, then removed from shared/exported packages.

Vault encrypts stored secrets at rest and gives the application access through the configured Vault token file. The project does not rely on hardcoded production secrets in source code.

## 19. LDAPS Security

The project supports Active Directory authentication over LDAPS on port 636. LDAPS protects LDAP bind credentials in transit using TLS.

In the current lab configuration, LDAPS testing confirmed:

- DNS resolution from the Docker container to the Domain Controller.
- TLS handshake success.
- TLS 1.3 negotiation.
- Certificate subject matching the Domain Controller hostname.
- Certificate issuer matching the AD CS CA.

The encryption strength depends on the TLS cipher negotiated by the server and client. The important implementation point is that the project validates the server certificate and does not send LDAP bind credentials over plaintext LDAP in secure mode.

## 20. HTTPS and Nginx Security Headers

Nginx serves the application over HTTPS on port `8080`. The project includes security headers such as:

- `Strict-Transport-Security`
- `X-Content-Type-Options`
- `X-Frame-Options`
- `Referrer-Policy`
- `Permissions-Policy`
- `Content-Security-Policy`

The local lab deployment can use a locally generated Nginx certificate. For a real public deployment, a trusted certificate should be used.

## 21. Robots.txt and Sitemap Decision

The project is intended for local server, lab, or internal intranet deployment. It does not require public search engine indexing.

Because of this, `robots.txt` and sitemap generation are not a core requirement. Access control is handled by Django views and authentication checks rather than relying on crawler instructions.

## 22. Dependency Pinning

The project pins exact Python package versions in `requirements.txt` to reduce unexpected breakage from upstream updates.

Current pinned versions include:

```text
Django==6.0.5
gunicorn==26.0.0
Markdown==3.10
bleach==6.3.0
Pillow==12.2.0
python-dotenv==1.2.1
django-auth-ldap==5.2.0
psycopg[binary]==3.3.2
pyotp==2.9.0
qrcode[pil]==8.2
```

This helps ensure the same behaviour across developer machines and deployment servers.

## 23. Database and Storage

### 23.1 PostgreSQL

PostgreSQL is the default database. The database credentials are provided through Docker Compose and Vault.

### 23.2 SQLite Fallback

`USE_SQLITE=true` exists only as a local fallback for quick testing outside the normal Docker/PostgreSQL deployment. The intended deployment uses PostgreSQL.

### 23.3 Article Storage

Article metadata is stored in PostgreSQL. Article Markdown content is also mirrored into OpenKB-compatible folders so OpenKB can index and use it.

## 24. Main Security Controls Summary

| Area | Implemented control |
|---|---|
| Password storage | Django password hashing for local users. AD passwords managed by Active Directory. |
| Account source separation | Local and AD users are separated by stored profile metadata, not email domain guessing. |
| MFA | TOTP MFA required after password/AD authentication. |
| Sensitive profile changes | Fresh MFA/OTP required for sensitive local profile changes. AD-managed values are blocked locally. |
| Sessions | Configurable session timeout and secure cookie settings. |
| CSRF | Django CSRF middleware and token-protected POST forms/endpoints. |
| XSS | Markdown rendered then sanitised with Bleach. |
| Upload safety | Extension allowlist, 2 MB size limit, Pillow image verification, pixel limit, generated filenames. |
| Access control | Article visibility checks, admin-only tools, 404 for non-admin admin-tool access. |
| Orphan content | Admin-only orphan article scan, assign, delete, and confirmation workflow. |
| Secrets | Runtime secrets stored in Vault instead of source code. |
| LDAP | LDAPS with certificate validation for AD integration. |
| HTTPS | Nginx HTTPS and security headers. |
| Auth logs | Read-only auth/MFA logs with IP/user-agent details and retention cleanup. |
| Activity logs | Article, vote, upload, AI, import/export, and admin-tool activity logging with retention cleanup. |
| Admin log display | Admin log pages use pagination and horizontal scrolling for wide tables. |
| AI endpoint | Prompt length limit, 5 questions per 60 seconds, 30-minute cooldown after exceeding the limit, user-ID based limiting for logged-in users, IP-based limiting for anonymous users, timeout handling, output cleanup, and redacted activity previews. |
| Dependencies | Exact package versions pinned in `requirements.txt`. |

## 25. Files That Should Not Be Shared

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

## 26. Useful Verification Commands

Run Django checks:

```bash
docker compose exec web python manage.py check
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

Run cleanup manually:

```bash
docker compose exec web python manage.py cleanup_stray_upload_files --noinput
docker compose exec web python manage.py cleanup_auth_activity_logs --dry-run
docker compose exec web python manage.py cleanup_auth_activity_logs --noinput
docker compose exec web python manage.py cleanup_activity_logs --dry-run
docker compose exec web python manage.py cleanup_activity_logs
```

Build and restart after dependency or Docker Compose changes:

```bash
docker compose build web cleanup-scheduler
docker compose up -d
```

## 27. Operational Notes for Administrators

- Keep `DJANGO_DEBUG=false` for deployment.
- Keep Vault secrets out of shared packages.
- Use LDAPS with certificate validation for AD.
- Use the activity logs and authentication logs to review suspicious behaviour.
- Keep the OpenKB AI rate limit enabled so one user or anonymous IP cannot continuously consume AI resources.
- Keep log retention at 30 days unless longer investigation history is needed.
- Use `--dry-run` before cleanup commands when validating behaviour.
- Admin log pages can show 500 rows per page, but very large logs should still be filtered by date, user, event type, or action.

## 28. Final Notes

DjOpenKB is designed as a secure internal knowledge base and cyber security project. The current implementation covers authentication, MFA, LDAPS, HTTPS, CSRF, upload validation, Markdown sanitisation, audit logging, article review workflow, orphan article management, role separation between local and AD users, and OpenKB AI integration.

For a controlled local or intranet deployment, the implemented controls are suitable as long as secrets are not shared, Vault is seeded correctly, LDAPS certificates are mounted correctly, debug mode remains off, and cleanup/log retention settings are reviewed by administrators.
