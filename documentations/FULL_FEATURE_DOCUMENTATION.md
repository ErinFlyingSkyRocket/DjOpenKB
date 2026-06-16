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
| `redis` | Shared production cache used for authentication lockout counters, AI rate limiting, AI cooldowns, and AI concurrency controls across Gunicorn workers. |
| `vault` | HashiCorp Vault used to store runtime secrets such as Django secret key, field-encryption key, PostgreSQL password, AI provider API keys, and LDAP bind password. |
| `vault-init` | First-time Vault initialisation and secret seeding helper. |
| `vault-auto-unseal` | Automatically unseals Vault using the stored unseal key in the local lab deployment. |
| `cleanup-scheduler` | Runs scheduled cleanup commands, including stray upload cleanup, authentication log cleanup, and general activity log cleanup. |

## 3. User Types and Permission Summary

DjOpenKB now uses a login-only main website model. Anonymous visitors are not allowed to browse the wiki, search articles, use the AI chatbot, vote, upload files, or access admin tools. The root URL shows the login page. Other protected paths return 404 for anonymous users to reduce route discovery value.

### 3.1 Main Website Access Levels

| Access level | Applies to | Main permissions | Restrictions |
|---|---|---|---|
| Anonymous visitor | Not signed in | Can access only the login page, language endpoint, and required static/login support assets. | Cannot browse articles, search, vote, suggest content, use AI, access profiles, or use admin tools. Protected paths return 404. |
| Disabled User | Local or AD / LDAP account in `Disabled User` group | Account record remains available for audit/history and later reassignment. Valid password/MFA does not complete login; the user receives a disabled-account message. | Cannot access the wiki, articles, voting, AI assistant, admin tools, or Django Admin through DjOpenKB permissions. |
| Regular User | Logged-in local or AD / LDAP user in `Regular User` group | Can view published articles and vote on articles. This is the fallback viewer role. | Cannot create articles, manage approvals, or use admin tools unless direct add-on permissions are granted. Automatically removed when Writer/Approver/Manager is assigned. |
| Article Writer | Logged-in user in `Article Writer` group | Can view published articles, create drafts, submit articles for approval, and edit/resubmit own drafts or pending failed articles. | Cannot approve/publish other users' articles by group default. Does not need Regular User because view access is included. |
| Article Approver | Logged-in user in `Article Approver` group | Can view published articles and manage pending articles/pending updates, including review-stage editing and approve/reject actions. | Cannot create new articles or delete articles by group default. |
| Article Manager | Logged-in user in `Article Manager` group | Can view published articles, create articles, edit/manage articles, manage pending articles/pending updates, approve/reject submissions, and delete articles. | Does not grant Django Admin access unless the user is also in `Admin Users`. |
| Admin Users | Trusted local or AD / LDAP admin in `Admin Users` group | Full administrator role. Members are automatically synced to `is_staff=True` and `is_superuser=True`, can use admin tools, and can access Django Admin when network restrictions pass. | Should be assigned only to trusted administrators. |

### 3.2 Group Baseline and Direct User Permission Add-ons

Groups provide the baseline role. Django Admin also shows direct user permission checkboxes for one-off content-access exceptions:

```text
Can view articles
Can create articles
Can approve/manage articles
```

These direct user permissions are additive only. Ticking a checkbox grants that permission directly to the user. Unticking it removes only the direct user permission; it does not remove a permission inherited from a group. Direct permissions do not grant Django Admin access. Django Admin access is controlled by the `Admin Users` group, superuser/staff sync, and the admin network guard. The `Disabled User` role is the exception and overrides direct add-on permissions.

### 3.3 Default Group Assignment

Newly created non-admin users are automatically placed in the `Regular User` group. This applies to normal local accounts and AD/LDAP accounts created during first login. `Regular User` is only the fallback viewer role: if the account is assigned `Article Writer`, `Article Approver`, or `Article Manager`, the redundant `Regular User` group is automatically removed because those elevated content roles already include view access. Admins can assign `Disabled User` to retain an account while preventing login completion and website access. The `Admin Users` group is the source of truth for full Django Admin access.

### 3.4 Account Source Differences

The `UserProfile` model stores the account source, so the system does not guess whether a user is local or AD-managed based on email domain alone. This prevents a local user with an email such as `alice@openkb.local` from being incorrectly treated as an AD account.

| Account source | Password owner | Email owner | Profile password change | Profile email change |
|---|---|---|---|---|
| Local account | Django | Django/local admin | Allowed with fresh MFA/OTP | Allowed with fresh MFA/OTP |
| Active Directory account | Active Directory | Active Directory/domain admin | Blocked in Django | Blocked in Django |

### 3.5 Role Enforcement and Precedence

Role precedence is enforced automatically when accounts are saved or synchronised. This keeps admin settings predictable and prevents conflicting combinations.

```text
1. Disabled User has the highest precedence.
2. Admin Users is the source of truth for full administrator access.
3. Regular User is the fallback viewer role. Article Writer, Article Approver, and Article Manager are elevated content roles and do not require Regular User.
4. Direct user permissions are add-ons for content features only.
5. Django's Active checkbox controls whether the account can sign in at all.
```

`Disabled User` is treated as a deliberate no-access role. A disabled account may still exist in Django for ownership, audit, and historical records, but it cannot use the website. When assigned, it removes the user's standard role groups, removes `Admin Users`, clears direct Knowledge Repository permission add-ons, and unchecks `is_staff` and `is_superuser`. If a disabled user already has an authenticated browser session, the next server request redirects the user to the disabled-account page. The sign-out button on that page clears the restricted session.

`Admin Users` is treated as the full administrator role. When assigned, it automatically sets `is_staff=True` and `is_superuser=True`, removes normal standard role groups such as `Regular User`, `Article Writer`, `Article Approver`, and `Article Manager`, and clears direct Knowledge Repository permission overrides because the account already has full access. Custom non-role groups, such as future notification groups, are preserved.

`Regular User` is treated as a fallback viewer role only. It is auto-added when an active, non-admin, non-disabled account has no standard Knowledge Repository role. If the account is assigned `Article Writer`, `Article Approver`, or `Article Manager`, `Regular User` is removed automatically because those roles already include article viewing and voting access. Writer, Approver, and Manager may still be combined with each other if a user needs multiple elevated content capabilities.

Account source is preserved during role sync:

```text
Local user + Admin Users  → Local admin
LDAP user + Admin Users   → LDAP admin
Local admin removed from Admin Users → Local user
LDAP admin removed from Admin Users  → LDAP user
```

Admins can edit the profile `Account Type` and `Source` fields in Django Admin for recovery cases, such as converting an AD/LDAP account to a local user after the AD account has been removed but the Knowledge Repository account still owns important articles. Local account types must use the local source, and LDAP account types must use the Active Directory source.

The Django `Active` checkbox is separate from `Disabled User`. Unticking `Active` prevents login entirely. Assigning `Disabled User` gives a cleaner disabled-account page flow for accounts that should remain visible for history but should not access the website.

Main-site admin tools and Django Admin access require superuser access after role synchronisation. Non-admin users receive 404 responses for protected main-site admin tools. Anonymous users also receive 404 for normal protected routes instead of being shown application content.

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

Admins can edit account type/source in Django Admin for controlled recovery or conversion cases. Use Django's built-in `Active` checkbox when the account should be unable to sign in at all. Use the `Disabled User` group when the account should be retained and redirected to the disabled-account page after authentication.

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

## 6. Progressive Password and MFA Lockout

DjOpenKB includes a progressive lockout system for wrong password attempts and wrong MFA attempts. The policy is configurable from Django Admin instead of being permanently hardcoded in `.env`.

Password failures and MFA failures are tracked separately:

```text
Wrong password attempts -> password lockout counter
Wrong MFA setup/verification/profile confirmation attempts -> MFA lockout counter
```

The default simplified policy seeded by migration is:

| Stage | Failed attempts | Block duration | Repeat count |
|---|---:|---:|---:|
| 1 | 10 | 5 minutes | 2 |
| 2 | 5 | 15 minutes | 2 |
| 3 | 3 | 1 hour | repeat forever |

A `repeat_count` of `0` means the stage repeats forever. The final stage uses `repeat_count=0`, so repeated attacks continue receiving the final 1-hour block until the user successfully logs in/verifies MFA or an admin resets the lockout state.

The policy no longer uses a separate failure time window. Failed counters stay active until successful password login/MFA verification, an administrator reset, or the lockout escalation memory expiry (`AUTH_LOCKOUT_STRIKE_TTL_SECONDS`, default 7 days).

Successful password authentication resets the password lockout state for that user. Successful MFA verification resets the MFA lockout state for that user. Administrators can also reset lockout state manually through Django Admin user/profile actions.

The policy stages are configured here:

```text
Django Admin -> Site settings -> Authentication lockout policy stages
```

When the database/site setting is unavailable during early startup or migration, the `.env` value `AUTH_LOCKOUT_POLICY_STAGES` acts only as the fallback policy. The fallback format is `failed_attempts:block_seconds:repeat_count`, for example `10:300:2,5:900:2,3:3600:0`. Production deployments should rely on the admin-managed policy after migration.

## 7. Session and Cookie Security

### 7.1 Session Timeout

Authenticated sessions are controlled by a site setting:

```text
session_timeout_days = 30 by default
```

If the timeout expires, the user is logged out and must sign in again. A value of `0` makes the browser session expire when the browser closes.

### 7.2 Secure Cookies

When `DJANGO_DEBUG=false`, the project enables secure deployment cookie settings:

- `SESSION_COOKIE_SECURE=True`
- `CSRF_COOKIE_SECURE=True`
- `LANGUAGE_COOKIE_SECURE=True`
- `SESSION_COOKIE_HTTPONLY=True`
- `CSRF_COOKIE_HTTPONLY=True`
- `SESSION_COOKIE_SAMESITE=Lax`
- `CSRF_COOKIE_SAMESITE=Lax`

This means session and CSRF cookies are protected for HTTPS deployment.

### 7.3 Cache Control After Logout/MFA

Authentication and MFA pages use strict no-cache headers to reduce the chance of browser back/forward cache showing stale authenticated pages after logout or MFA reset.

## 8. CSRF and Request Protection

Django CSRF middleware is enabled. Normal forms use CSRF tokens, and the OpenKB AI POST endpoint is called from the frontend with a CSRF token.

Important protections include:

- `CsrfViewMiddleware` enabled.
- POST-only endpoints for state-changing actions.
- Safe redirect validation using Django's `url_has_allowed_host_and_scheme`.
- Secure CSRF cookie settings when debug is off.
- `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` are passed into Docker Compose for safer deployment configuration.

## 9. Article Management

### 9.1 Suggested Article Workflow

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

### 9.2 Published Article Update Review

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

### 9.3 Admin Review Notes and History

When an article is returned as pending failed, admins can enter review notes. The current review note is shown to the article owner when the article is in draft or pending failed status.

Review notes are also stored in history, so previous feedback rounds are preserved for audit and review tracking.

### 9.4 Duplicate Article Title Protection

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

### 9.5 Article File Sync

Published article content is written to OpenKB-compatible Markdown files under the OpenKB data structure. Pending updates are not written as the public article version until an admin approves them. Internal generated metadata is removed from public display, search snippets, and AI output so users do not see sync markers.

## 10. Orphan Article Management

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

## 11. Article Browsing, Search, Views, Voting, and Homepage Tabs

### 11.1 Login-Protected Article Listing

The main website is login protected. Published article listing, article search, article details, voting, and the AI chatbot are available only after authentication and MFA completion where applicable.

Draft, pending, pending failed, and unapproved pending-update content are not publicly visible unless the current user owns the article or has the required manager/admin permission.

### 11.2 Simple Title and Keyword Search

The main search intentionally stays simple and predictable. It matches only:

```text
published article title
published article keywords manually entered by users/admins
```

It does not search article body content, Markdown files, author names, OpenKB paths, internal metadata, or relevance scores. This reduces unnecessary scanning and makes search behaviour easier for users to understand.

### 11.3 Search Suggestions

The search bar suggestion dropdown uses the same title/keyword-only search logic. It returns clickable published article titles only and does not expose internal paths or article body excerpts.

### 11.4 Homepage Article Tabs

The homepage article panel uses one container with tabs:

```text
Trending Topics
Most Liked
Most Recent Articles
```

Each tab has pagination and shows the current page, total pages, and total article count. The article count per page is controlled through the admin site setting `Articles per page`.

Sorting behaviour:

| Tab | Sort order |
|---|---|
| Trending Topics | Highest views first, then likes, then latest update |
| Most Liked | Highest likes first, then views, then latest update |
| Most Recent Articles | Latest updated/published articles first |

### 11.5 Article Count Site Setting

Admins can configure how many articles appear per page from Django Admin → KB → Site settings.

```text
Articles per page
Minimum: 5
Maximum: 100
Default: 10
```

The setting is validated in admin and also clamped at runtime for safety.

### 11.6 View Counts

Each article stores a `view_count`. Views are tracked per user session to avoid simply refreshing the same article repeatedly to increase the count.

### 11.7 Voting

Signed-in users can vote on published articles:

- Helpful / thumbs up.
- Not helpful / thumbs down.
- One vote per user per article.
- Users can change or remove their vote.

Helpful counts are visible to users. Admins can review vote details through Django admin and through activity logging.

### 11.8 Manual Existing-Keyword Suggestions

When users add or edit an article, the suggested keyword panel uses a manual refresh button. It scans the current draft title/body in the browser and compares it against keywords that already exist on published articles.

Keyword suggestion behaviour:

```text
Only existing manually-created article keywords are considered.
No built-in keyword list is used.
No filler-word filter is used.
No similarity score is shown.
No usage count is shown.
A keyword appears only when the exact keyword or phrase exists in the current title/body.
Suggested keyword chips scroll horizontally.
```

This keeps keyword sharing predictable. If users repeatedly choose the same manual keyword across articles, that keyword naturally becomes more useful for search and related article discovery.

## 12. Upload and Image Security

### 12.1 Allowed Image Types

Article image uploads are restricted to:

```text
.png
.jpg
.jpeg
.gif
.webp
```

### 12.2 Upload Size Limit

Uploaded article images are limited to:

```text
2 MB maximum per image
```

### 12.3 Pillow Image Verification

The project does not trust the browser-provided MIME type alone. Uploaded files are opened and verified using Pillow. This helps reject non-image files renamed with an image extension.

### 12.4 Pixel Limit

The image validation also checks image dimensions and rejects images above the configured pixel limit. This helps reduce the risk of oversized image processing abuse.

### 12.5 Server-Generated Filenames

Uploaded images are stored using generated filenames containing a timestamp and random component. The original filename is not used directly as the storage path.

### 12.6 Path Traversal Protection

Uploaded and imported filenames are normalised. Path traversal patterns such as `../` are rejected or reduced to safe filename-only values.

### 12.7 Protected Image Serving

The project does not expose the whole OpenKB uploads folder as a raw static directory. Images are served through a Django view that checks filenames and article visibility rules.

### 12.8 Upload Audit Log

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

## 13. Stray Upload File Cleanup

### 13.1 Manual Cleanup

Admins have access to a clean stray upload files tool. It finds uploaded files that are no longer referenced by any article or Markdown file.

The admin cleanup page allows review before deletion so admins can avoid removing files that should be kept. The cleanup logic checks both live article content and pending-update content so images used only by a pending update are not incorrectly treated as stray files.

### 13.2 Automatic Cleanup

The `cleanup-scheduler` Docker service runs scheduled cleanup commands. By default, the cleanup interval is 24 hours:

```text
CLEANUP_INTERVAL_SECONDS=86400
```

The default stray upload minimum age is also 24 hours:

```text
stray_upload_cleanup_min_age_minutes = 1440
```

This prevents newly uploaded images from being deleted while a user is still drafting an article.

## 14. Markdown and XSS Protection

Article Markdown is converted into HTML using `markdown`, then sanitised using `bleach` before display.

This protects article pages from unsafe HTML and script injection. Only approved HTML tags/attributes/protocols are allowed through the sanitisation process.

The article display template can safely render the sanitised HTML because the input has already passed through the controlled Markdown and Bleach pipeline.

## 15. OpenKB AI Integration

### 15.1 OpenKB CLI Integration

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

### 15.2 AI Provider

The AI provider is configured through environment settings:

```env
OPENKB_AI_PROVIDER=openkb-cli
OPENKB_AI_MODEL=gemini/gemini-2.5-flash
```

AI API keys are stored in Vault, not directly in source code. The compatibility key `AI_API_KEY` can be kept, and provider-specific keys such as `GEMINI_API_KEY`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY` are supported so the model/provider can be changed later without changing source code.

### 15.3 AI Endpoint Safety Limits and Rate Limiting

The Ask OpenKB AI endpoint is available only after login in the current deployment. It includes limits such as:

- Maximum prompt length.
- Redis-backed request rate limiting.
- Temporary blocking after too many requests.
- Concurrency limiting so slow AI requests cannot occupy all Gunicorn workers.
- Timeout handling for OpenKB CLI calls.
- Error sanitisation before returning messages to users.
- Prompt preview redaction before storing in activity logs.

Current defaults in settings:

```text
OPENKB_AI_MAX_PROMPT_CHARS = 1000
OPENKB_AI_RATE_LIMIT_MAX_REQUESTS = 5
OPENKB_AI_RATE_LIMIT_WINDOW_SECONDS = 60
OPENKB_AI_RATE_LIMIT_BLOCK_SECONDS = 1800
OPENKB_AI_TIMEOUT_SECONDS = 90
OPENKB_AI_CONCURRENCY_LIMIT = 2
OPENKB_AI_CONCURRENCY_LOCK_SECONDS = 120
```

This means each logged-in user can send up to 5 AI questions within 60 seconds. If the limit is exceeded, that user is temporarily blocked from using the chatbot for 1800 seconds, which is 30 minutes. The OpenKB command is also limited to 90 seconds by default, and only 2 AI requests are allowed to run concurrently by default.

The rate-limit identity for logged-in local and AD/LDAP users is the Django user ID. The limit follows the authenticated account even if the user refreshes the browser, opens another tab, or logs in again from the same browser. In production, Redis is required so these counters are shared across all Gunicorn workers. Local-memory cache is only a development/emergency fallback.

If anonymous chatbot access is ever re-enabled in the future, IP-based limiting should be treated as a fallback and should rely on trusted Nginx reverse proxy headers.

### 15.4 Related Article Recommendations

The AI endpoint can recommend relevant published articles from the local database. Related article logic avoids showing random articles for simple greetings or unrelated filler messages.

Only published articles are used for public AI recommendations.

### 15.5 Output Cleanup

OpenKB internal metadata and generated sync markers are removed before display. This prevents implementation details such as generated article metadata from leaking into article snippets or AI responses.

## 16. Internationalisation and Local Translation

The UI uses Django's local translation system through `.po` and `.mo` locale files. Translation is local/offline and does not call an external AI translator.

Supported language choices are configured in `settings.py` and exposed through the language selector. Anonymous users store language preference in a cookie. Logged-in users also save the preference in their user profile.

The locale files have been updated across all supported languages so extracted UI strings have translations and compiled `.mo` files. This includes newer admin-tool labels, orphan article workflow messages, MFA text, activity logging labels, and profile/account-source messages.

This design keeps UI translation independent from the AI chatbot and avoids sending translation content to external AI services.

## 17. Admin Tools, Django Admin, and Access Control

### 17.1 Admin Tool Restriction

Admin tools are protected by superuser/admin-role checks. `Admin Users` is the source of truth for full administrator access and automatically syncs members to `is_staff=True` and `is_superuser=True`. Staff status alone is not treated as a separate admin role. Direct user permission checkboxes do not grant Django Admin access.

Non-admin users receive 404 responses for admin-only main-site tools to reduce route discovery usefulness. The Django admin login path is hidden; admins should sign in through the normal login flow and then open `/admin/`.

### 17.2 Admin Network Restriction

The deployment can restrict Django Admin access by source IP/CIDR, such as a VPN or internal subnet. A correct username/password is not enough if the request source is outside the allowed admin CIDR range.

### 17.3 Main Admin Tools

Admin tools include:

- Clean stray upload files.
- Bulk import/export articles.
- Manage pending articles and pending updates.
- Review suggested articles.
- Scan and manage orphan articles.
- Configure site settings such as article count per page, log retention, session timeout, and authentication lockout policy stages.
- Manage user roles, account type/source recovery, and direct content-permission add-ons.
- View authentication activity logs through Django admin.
- View general activity logs through Django admin.
- View upload audit records through Django admin.

### 17.4 Group and User Permission Management

Django Admin Groups represent the main role groups:

```text
Disabled User
Regular User
Article Writer
Article Approver
Article Manager
Admin Users
```

The Groups admin page shows current users in each group and provides a searchable left/right selector to add or remove users from the group.

The Users admin page provides direct Knowledge Repository permission checkboxes for one-off content-access exceptions:

```text
Can view articles
Can create articles
Can approve/manage articles
```

These direct user permissions are add-on only and do not grant Django Admin access. The final content permission result is:

```text
final content access = group permissions + direct user permissions
```

### 17.5 Article Import/Export

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

### 17.6 Authentication Lockout Administration

Administrators can manage password/MFA lockout policy stages through the Site settings page. Stages are shown inline and can be added, removed, reordered, enabled, or disabled. The simplified default is 10 failures -> 5 minutes twice, then 5 failures -> 15 minutes twice, then 3 failures -> 1 hour repeatedly.

Administrators can reset a user's lockout state from:

```text
Django Admin -> Users -> open user -> Authentication lockout -> Reset password/MFA lockout
```

Bulk reset actions are also available on selected Users and User Profiles. Reset actions are recorded in authentication activity logs.

### 17.7 Django Admin Usability

Django admin pages scroll normally in the browser. For log-heavy pages:

- Log list pages use pagination.
- Activity log and authentication log admin pages can show up to 500 rows per page.
- Wide admin tables support horizontal scrolling to avoid squeezing columns.
- `list_max_show_all` is limited to reduce accidental extremely large admin page loads.

## 18. Logging and Monitoring

### 18.1 Authentication Activity Logs

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
| Password/MFA lockout | Lockout applied, blocked attempt, lockout reset |
| Admin MFA/lockout management | Admin MFA reset and admin password/MFA lockout reset |

### 18.2 General Activity Logs

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

### 18.3 Admin Activity Logs

Django Admin actions are recorded separately in `AdminActivityLog`. This log is intended to show administrator activity in readable wording instead of only object IDs. It records the acting admin, target object name where available, action category, request path, method, status outcome, IP address, and safe details about the change.

Examples of admin activity entries include:

| Admin action | Example log wording |
|---|---|
| User role change | Changed user `alice`: Groups added `Article Writer`; removed `Regular User` |
| Admin promotion | Changed user `alice`: added `Admin Users`; synced staff/superuser; account type changed to `Local admin` or `LDAP admin` based on source |
| Disabled account | Assigned `Disabled User` to `alice`; removed admin/role groups; cleared staff/superuser |
| MFA reset | Reset MFA for user `alice` |
| Lockout reset | Reset password/MFA lockout for user `alice` |
| Site setting change | Changed site settings, including lockout policy or retention values |
| Article admin action | Created, changed, approved, rejected, or deleted an article from Django Admin |
| Group management | Changed group membership or attempted to modify a protected default group |

Sensitive values such as passwords, MFA secrets, tokens, API keys, and private keys are not written to the admin activity log. Status is displayed in friendly terms such as success, redirected, permission denied, not found, or server error.

Admin activity logs are append-only from Django Admin. They are not manually editable or deletable through the admin interface. Retention cleanup follows the same general activity-log retention setting so old entries can still be removed automatically according to site policy.

### 18.4 Log IP Handling

IP logging prefers trusted reverse proxy headers such as `X-Real-IP` from Nginx instead of blindly trusting the first value from `X-Forwarded-For`. This improves accuracy for internal deployments behind the configured reverse proxy.

### 18.5 Read-Only Admin Log Views

`AuthActivityLog`, general `ActivityLog`, and `AdminActivityLog` are intended to be read-only in Django admin. Admin users can search and filter logs, but should not manually add or edit them from the admin interface.

Retention/deletion is controlled through cleanup commands instead of manual editing. Manual deletion is not part of the normal admin workflow.

### 18.6 Log Retention

Authentication activity log retention is controlled by site setting:

```text
auth_activity_log_retention_days = 30 by default
```

General activity log retention is controlled by site setting:

```text
activity_log_retention_days = 30 by default
```

A value of `0` keeps logs forever. If the value is set to `30`, cleanup deletes only logs older than 30 days. If the value is increased later, future cleanup follows the new value, but logs that were already deleted cannot be restored.

### 18.7 Log Cleanup

The scheduled cleanup service can run log cleanup automatically. Cleanup commands can also be run manually:

```bash
docker compose exec web python manage.py cleanup_auth_activity_logs --dry-run
docker compose exec web python manage.py cleanup_auth_activity_logs --noinput

docker compose exec web python manage.py cleanup_activity_logs --dry-run
docker compose exec web python manage.py cleanup_activity_logs
```

## 19. Secrets Management with Vault

HashiCorp Vault is used to store sensitive runtime values, including:

- `DJANGO_SECRET_KEY`
- `POSTGRES_PASSWORD`
- `DJANGO_FIELD_ENCRYPTION_KEY`
- `AI_API_KEY`
- `GEMINI_API_KEY`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `LDAP_BIND_PASSWORD`

The `.env` file should contain non-secret runtime configuration only. Passwords and API keys should be stored in `vault/bootstrap/djopenkb.env` only for first-time Vault seeding, then removed from shared/exported packages.

Vault encrypts stored secrets at rest and gives the application access through the configured Vault token file. The project does not rely on hardcoded production secrets in source code. The Django PostgreSQL password fallback is disabled in production, so missing production database secrets fail startup instead of silently using a weak default.

## 20. LDAPS Security

The project supports Active Directory authentication over LDAPS on port 636. LDAPS protects LDAP bind credentials in transit using TLS.

In the current lab configuration, LDAPS testing confirmed:

- DNS resolution from the Docker container to the Domain Controller.
- TLS handshake success.
- TLS 1.3 negotiation.
- Certificate subject matching the Domain Controller hostname.
- Certificate issuer matching the AD CS CA.

The encryption strength depends on the TLS cipher negotiated by the server and client. The important implementation point is that the project validates the server certificate and does not send LDAP bind credentials over plaintext LDAP in secure mode.

## 21. HTTPS and Nginx Security Headers

Nginx serves the application over HTTPS on port `8080`. The project includes security headers such as:

- `Strict-Transport-Security`
- `X-Content-Type-Options`
- `X-Frame-Options`
- `Referrer-Policy`
- `Permissions-Policy`
- `Content-Security-Policy`

The local lab deployment can use a locally generated Nginx certificate. For a real public deployment, a trusted certificate should be used.

## 22. Robots.txt and Sitemap Decision

The project is intended for local server, lab, or internal intranet deployment. It does not require public search engine indexing.

Because of this, `robots.txt` and sitemap generation are not a core requirement. Access control is handled by Django views and authentication checks rather than relying on crawler instructions.

## 23. Dependency Pinning

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
django-redis==6.0.0
redis==6.4.0
```

This helps ensure the same behaviour across developer machines and deployment servers.

## 24. Database and Storage

### 24.1 PostgreSQL

PostgreSQL is the default database. The database credentials are provided through Docker Compose and Vault.

### 24.2 SQLite Fallback

The intended deployment uses PostgreSQL through Docker Compose. SQLite fallback is not part of the supported deployment path and should not be relied on for normal testing or production use.

### 24.3 Article Storage

Article metadata is stored in PostgreSQL. Article Markdown content is also mirrored into OpenKB-compatible folders so OpenKB can index and use it.

### 24.4 Redis Cache and Counters

Redis is used as the shared Django cache backend in production. It stores temporary counters and lock flags for authentication lockout, AI rate limiting, AI cooldowns, and AI concurrency control. It is not the primary database and should not be used as the only backup source for article data.

## 25. Main Security Controls Summary

| Area | Implemented control |
|---|---|
| Password storage | Django password hashing for local users. AD passwords managed by Active Directory. |
| Account source separation | Local and AD users are separated by stored profile metadata, not email domain guessing. |
| MFA | TOTP MFA required after password/AD authentication. |
| Password/MFA lockout | Progressive admin-configurable lockout stages with repeat counts, Redis-backed counters, automatic reset after successful login/MFA, and admin reset actions. |
| Sensitive profile changes | Fresh MFA/OTP required for sensitive local profile changes. AD-managed values are blocked locally. |
| Sessions | Configurable session timeout and secure cookie settings. |
| CSRF | Django CSRF middleware and token-protected POST forms/endpoints. |
| XSS | Markdown rendered then sanitised with Bleach. |
| Upload safety | Extension allowlist, 2 MB size limit, Pillow image verification, pixel limit, generated filenames. |
| Access control | Article visibility checks, admin-only tools, 404 for non-admin admin-tool access. |
| Orphan content | Admin-only orphan article scan, assign, delete, and confirmation workflow. |
| Secrets | Runtime secrets stored in Vault instead of source code, with production database fallback disabled. |
| LDAP | LDAPS with certificate validation for AD integration. |
| HTTPS | Nginx HTTPS and security headers. |
| Auth logs | Read-only auth/MFA logs with IP/user-agent details and retention cleanup. |
| Activity logs | Article, vote, upload, AI, import/export, and admin-tool activity logging with retention cleanup. |
| Admin log display | Admin log pages use pagination and horizontal scrolling for wide tables. |
| AI endpoint | Prompt length limit, 5 questions per 60 seconds, 30-minute cooldown after exceeding the limit, Redis-backed user-ID limiting for logged-in users, timeout handling, concurrency limiting, output cleanup, and redacted activity previews. |
| Dependencies | Exact package versions pinned in `requirements.txt`. |

## 26. Files That Should Not Be Shared

The following files/folders may contain secrets, tokens, generated keys, or local runtime data and should not be included in public repositories or submission packages:

```text
.env
.env.*
!.env.example
vault/bootstrap/djopenkb.env
vault/keys/*
vault/file/*
openkb-data/.openkb/
.openkb-venv/
ldap-certs/
nginx/certs/*.key
postgres-data/*
exported article ZIP backups
```

The `.gitignore` should continue to exclude these sensitive/generated files.

## 27. Useful Verification Commands

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

## 28. Operational Notes for Administrators

- Keep `DJANGO_DEBUG=false` for deployment.
- Keep Vault secrets out of shared packages.
- Use LDAPS with certificate validation for AD.
- Use the activity logs and authentication logs to review suspicious behaviour.
- Keep the OpenKB AI rate limit enabled so one user or anonymous IP cannot continuously consume AI resources.
- Keep log retention at 30 days unless longer investigation history is needed.
- Use `--dry-run` before cleanup commands when validating behaviour.
- Admin log pages can show 500 rows per page, but very large logs should still be filtered by date, user, event type, or action.

- Keep the site login-only unless there is a clear business requirement for anonymous article browsing.
- Keep the group model simple: `Disabled User`, fallback `Regular User`, elevated `Article Writer`, `Article Approver`, `Article Manager`, and `Admin Users`.
- Use direct user permission checkboxes only for one-off exceptions because they add permissions on top of group permissions.
- Review the full-project Docker bind mount `.:/app` before final production-style deployment. It is convenient during development but should be removed where possible for hardened deployment.
- Keep `.dockerignore` updated so secrets and runtime folders are not copied into Docker images.

## 29. Final Notes

DjOpenKB is designed as a secure internal knowledge base and cyber security project. The current implementation covers authentication, MFA, LDAPS, HTTPS, CSRF, upload validation, Markdown sanitisation, audit logging, article review workflow, orphan article management, role separation between local and AD users, and OpenKB AI integration.

For a controlled local or intranet deployment, the implemented controls are suitable as long as secrets are not shared, Vault is seeded correctly, LDAPS certificates are mounted correctly, debug mode remains off, the login-only route policy is maintained, role groups are reviewed, and cleanup/log retention settings are reviewed by administrators.
