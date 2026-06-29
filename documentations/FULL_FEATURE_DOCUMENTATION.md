# DjOpenKB Full Feature Documentation

This document summarises the implemented features, security controls, deployment-related components, role permissions, logging coverage, and operational behaviours of DjOpenKB. It is intended to give engineers, reviewers, and future administrators a clear overview of what the system provides and how the main surfaces are protected.

## 1. Project Purpose

DjOpenKB is a Django-based internal knowledge base website integrated with OpenKB AI. It allows users to browse, search, vote on, and suggest public wiki articles, while a separate internal article area is available only to users with internal article roles. The platform is designed for a controlled local server or intranet-style deployment using Docker Compose, PostgreSQL, Nginx HTTPS, HashiCorp Vault, Redis, and optional Windows Active Directory authentication over LDAPS.

The project focuses on secure article management, public/internal content isolation, controlled user contribution, local/offline UI translation, MFA-protected account actions, audit logging, upload validation, OpenKB-compatible article synchronisation, and role-scoped AI-assisted article search.

## 2. Main Runtime Services

The Docker Compose stack contains the following main services:

| Service | Purpose |
|---|---|
| `web` | Django application served by Gunicorn. Handles website requests, article workflow, authentication, MFA, queue submission/status endpoints, logging, and admin tools. |
| `ai-worker` | Dedicated Celery worker for OpenKB AI jobs. Runs OpenKB/provider work outside Gunicorn so an AI response can continue while the user navigates the normal site. |
| `nginx` | Reverse proxy that serves HTTPS on port `8080`, forwards requests to Django, and serves collected static files. |
| `db` | PostgreSQL database used by Django. The database password is loaded from Vault. |
| `redis` | Shared production cache for authentication lockouts, AI burst limits/cooldowns, fixed 24-hour per-user quotas, encrypted temporary AI job records, and query concurrency controls. Redis DB 2 is used as the Celery broker by default. |
| `vault` | HashiCorp Vault used to store runtime secrets such as Django secret key, field-encryption key, PostgreSQL password, AI provider API keys, and LDAP bind password. |
| `vault-init` | First-time Vault initialisation and secret seeding helper. |
| `vault-auto-unseal` | Automatically unseals Vault using the stored unseal key in the local lab deployment and recreates the app token if it is missing. |
| `app-permissions-init` | Short-lived, network-isolated root helper that prepares the static and OpenKB bind mounts for the unprivileged application UID/GID before application services start. It must exit successfully. |
| `cleanup-scheduler` | Runs scheduled cleanup commands, including stray upload cleanup, published-article deletion-queue purge, authentication log cleanup, and general/admin activity log cleanup. |

## 3. User Types and Permission Summary

DjOpenKB uses a login-only main website model. Anonymous visitors are not allowed to browse the wiki, search articles, use the AI chatbot, vote, upload files, or access admin tools. The root URL shows the login page. Other protected paths return 404 for anonymous users to reduce route discovery value.

### 3.1 Main Website Access Levels

DjOpenKB is a login-only main website. Anonymous users receive the login page at the root URL; other protected paths return 404. `Disabled User` is a highest-precedence blocked-account role. Internal roles are additive and include public article viewing.

| Role | Core purpose |
|---|---|
| Anonymous visitor | No website access beyond the login/language/static support paths and crawler-only `/robots.txt`. |
| Disabled User | Retained account record with all Knowledge Repository access blocked. |
| Regular User | View and vote on published public articles. |
| Article Writer | Create and maintain own public submissions. |
| Article Approver | Review public pending articles and updates. |
| Article Manager | Manage public content and public review work. |
| Internal User | View and vote on published internal articles, plus public articles. |
| Internal Article Writer | Create and maintain own internal submissions. |
| Internal Article Approver | Review internal pending articles and updates. |
| Internal Article Manager | Manage internal content and internal review work. |
| Admin Users | Full public/internal access, main-site admin tools, and Django Admin after its network and MFA gates. |

### 3.2 Role Interaction Matrix

| Rule | Effective result |
|---|---|
| New active local/AD user without an elevated public role | Receives `Regular User` as the fallback public-viewer role. |
| Public writer, approver, or manager assigned | `Regular User` is removed because public viewing is already included. |
| Internal role assigned | The role is additive: it grants its internal scope and public article viewing, but not unrelated public management. |
| Public and internal roles combined | Permissions remain scope-specific. A public manager plus internal approver manages public content and reviews only internal pending work. |
| Writer combined with matching approver or manager | Permissions are additive. The deliberately combined role can approve its own matching-scope submission or pending update; separation of duties is not enforced for that assignment. |
| Disabled User assigned | Overrides other roles and direct Knowledge Repository permissions, clears staff/superuser flags, and blocks the site. |

### 3.3 Permission-by-Function Matrix

**Legend:** ✓ = included in the role; ✗ = not included. “Manage published” means direct edit/delete capability in that scope.

| Function | Regular User | Public Writer | Public Approver | Public Manager | Internal User | Internal Writer | Internal Approver | Internal Manager | Admin Users |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| View published public articles | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| View published internal articles | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Vote on accessible published articles | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| See dislike counts in the matching scope | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | ✓ | ✓ |
| Create own public article | ✗ | ✓ | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ | ✓ |
| Create own internal article | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✓ | ✓ |
| Review public pending queue | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✓ |
| Review internal pending queue | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |
| Manage published public articles | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ | ✓ |
| Manage published internal articles | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ |
| Use matching-scope review or management tools | ✗ | ✗ | ✓ | ✓ | ✗ | ✗ | ✓ | ✓ | ✓ |
| Access Django Admin `/admin/` | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ |

Notes:

- Writers can edit their own draft/returned submissions. An edit to their own published article becomes a pending update instead of replacing the published copy directly.
- Published deletion requires MFA confirmation. Owner deletion follows the owner workflow; manager/admin deletion follows the matching scope’s management flow.
- Approvers can edit content only while using the pending-review flow. They do not receive free edit/delete rights for published content.
- Django Admin also requires normal login/MFA, the administrator network allowlist, and the separate Admin MFA gate.

### 3.4 Group Baseline and Direct User Permission Add-ons

Groups provide the baseline role. Django Admin also shows direct user permission checkboxes for one-off content-access exceptions:

```text
Can view published public articles
Can add/submit public articles for approval
Can approve/manage pending public article reviews
Can delete public articles
Can view internal articles
Can add/submit internal articles for approval
Can approve/manage pending internal article reviews
Can delete internal articles
Can use Knowledge Repository admin tools
```

These direct user permissions are additive only. Ticking a checkbox grants that permission directly to the user. Unticking it removes only the direct user permission; it does not remove a permission inherited from a group. Direct permissions do not grant Django Admin access unless the account also becomes an administrator through the intended admin path. The `Disabled User` role is the exception and overrides direct add-on permissions.

### 3.5 Default Group Assignment

Newly created non-admin users are automatically placed in the `Regular User` group. This applies to normal local accounts and AD/LDAP accounts created during first login. `Regular User` is only the fallback public viewer role: if the account is assigned `Article Writer`, `Article Approver`, or `Article Manager`, the redundant `Regular User` group is automatically removed because those elevated public content roles already include public article view access.

Internal roles are add-on roles. A user can hold a public role and an internal role at the same time, and permissions are evaluated by article visibility. Admins can assign `Disabled User` to retain an account while preventing login completion and website access. The `Admin Users` group is the source of truth for full Django Admin access.

### 3.6 Account Source Differences

The `UserProfile` model stores the account source, so the system does not guess whether a user is local or AD-managed based on email domain alone. This prevents a local user with an email such as `alice@openkb.local` from being incorrectly treated as an AD account.

| Account source | Password owner | Email owner | Profile password change | Profile email change |
|---|---|---|---|---|
| Local account | Django | Django/local admin | Allowed with fresh MFA/OTP | Allowed with fresh MFA/OTP |
| Active Directory account | Active Directory | Active Directory/domain admin | Blocked in Django | Blocked in Django |

### 3.7 Role Enforcement and Precedence

Role precedence is enforced automatically when accounts are saved or synchronised. This keeps admin settings predictable and prevents conflicting combinations.

```text
1. Disabled User has the highest precedence.
2. Admin Users is the source of truth for full administrator access.
3. Regular User is the fallback public viewer role.
4. Public roles control public article creation/review/management.
5. Internal roles are additive and control internal article creation/review/management.
6. Direct user permissions are add-ons for content features only.
7. Django's Active checkbox controls whether the account can sign in at all.
```

`Disabled User` is treated as a deliberate no-access role. A disabled account may still exist in Django for ownership, audit, and historical records, but it cannot use the website. When assigned, it removes the user's standard role groups, removes `Admin Users`, clears direct Knowledge Repository permission add-ons, and unchecks `is_staff` and `is_superuser`. If a disabled user already has an authenticated browser session, the next server request redirects the user to the disabled-account page. The sign-out button on that page clears the restricted session.

`Admin Users` is treated as the full administrator role. When assigned, it automatically sets `is_staff=True` and `is_superuser=True`, removes normal standard role groups where required, and covers both public and internal scopes. Custom non-role groups, such as future notification groups, are preserved.

`Regular User` is treated as a fallback public viewer role only. It is auto-added when an active, non-admin, non-disabled account has no standard Knowledge Repository role. If the account is assigned a public elevated role, `Regular User` is removed automatically because those roles already include public article viewing and voting access.

Account source is preserved during role sync:

```text
Local user + Admin Users  -> Local admin
LDAP user + Admin Users   -> LDAP admin
Local admin removed from Admin Users -> Local user
LDAP admin removed from Admin Users  -> LDAP user
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
session_timeout_hours = 8 by default
```

If the timeout expires, the user is logged out and must sign in again. The deadline begins at the original sign-in attempt and browser activity, refreshes, or cookie renewal do not extend it. The middleware aligns the browser cookie with the remaining fixed lifetime while preserving the server-side timestamp as the authoritative guard. The administrator setting accepts **1 to 168 hours**; browser-close-only sessions are no longer offered because the public-facing policy uses a fixed maximum lifetime.

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
- Nginx applies POST-only per-IP edge rate limits to login, MFA, Admin MFA, AI, upload, and bulk-import submissions before they reach Django or Active Directory. Normal GET page loads are not counted.
- Nginx rejects ordinary request bodies above 3 MB. The authorised bulk-import route alone has a 100 MB limit, matching the application ZIP validation limit.
- Per-IP connection caps and request timeouts reduce slow or repeated request pressure at the reverse-proxy layer.

## 9. Article Management

### 9.1 Public and Internal Article Visibility

Each article has a visibility value:

| Visibility | Meaning | Main access rule |
|---|---|---|
| `Public article` | General knowledge-base article visible to normal authenticated users. | Requires public article view access. Most logged-in active users receive this through `Regular User` or any elevated/internal role. |
| `Internal article` | Restricted article for more sensitive internal IT documentation. | Requires `Internal User`, `Internal Article Writer`, `Internal Article Approver`, `Internal Article Manager`, direct internal permission, or `Admin Users`. |

Public and internal articles share the same database model and workflow fields, but every view checks the article visibility before allowing list/detail/edit/review/delete/image access.

### 9.2 Suggested Article Workflow

Users can suggest articles through the website. Suggested articles are stored in the database and mirrored into the correct OpenKB data folder when published and eligible for AI indexing.

Article states include:

| Status | Meaning |
|---|---|
| `Draft` | User can edit before submitting. |
| `Pending` | Submitted for review in the matching public/internal pending queue. |
| `Pending failed` | Returned by reviewer/manager/admin with review comments. |
| `Published` | Approved and visible to users who can access that article visibility. |
| `Deletion queued` | A previously published article is hidden while it remains recoverable in the admin deletion queue. |

A user with only a writer role cannot approve. Roles are additive, however: a user deliberately assigned the matching approver or manager role may approve their own matching-scope submission or pending update. This is an intentional project policy. It should not be described as a separation-of-duties control.

For a new article, the normal workflow is:

```text
Draft -> Pending -> Pending failed / Published
```

For an already published article edited by an owner/writer flow, the live article is not overwritten immediately. The proposed update is stored separately and sent for review.

### 9.3 Published Article Update Review

When a user edits an already published article through the normal owner/writer workflow, the current published version remains accessible to readers. The edited version is saved as a pending update instead of immediately replacing the live article.

Pending update data is stored separately from the published article content, including:

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
Scope approver/manager/admin approves -> pending update replaces the live published article
Scope approver/manager/admin rejects -> live published article remains unchanged and update feedback is shown to the author
```

This design prevents unapproved edits from replacing already approved knowledge-base content. It also allows users to continue accessing the last approved article while the update is waiting for review.

### 9.4 Scope-Specific Review Queues

Public and internal review queues are separated:

| Queue | Route area | Allowed reviewers |
|---|---|---|
| Public pending articles/updates | `/profile/admin/pending-articles/` | `Article Approver`, `Article Manager`, `Admin Users` |
| Internal pending articles/updates | `/internal/profile/admin/pending-articles/` | `Internal Article Approver`, `Internal Article Manager`, `Admin Users` |

Approver-only users may edit content only while using the explicit review flow and only while the article/update is pending in their scope. They do not receive delete access and cannot freely edit already-published articles outside the review context. Managers have broader edit/manage/delete access in their own scope.

### 9.5 Owner Drafts, Failed Articles, and Direct Traversal Protection

Owners can open and edit their own drafts, pending failed articles, and pending-update drafts when they have create permission for that article's visibility. Direct traversal to drafts, pending articles, pending failed articles, or unapproved updates is intentionally stricter than normal article viewing:

```text
Published article detail -> allowed according to public/internal visibility
Draft/pending/failed detail -> owner or full admin only
Review workflow -> approver/manager/admin through explicit review URLs
```

This prevents users from guessing `/articles/<id>/` values to view another user's unapproved work.

### 9.6 Delete Behaviour

Delete access is scope-based:

**Legend:** ✓ = included; ✗ = not included; **Own** = article-owner workflow only.

| User type | Delete public article | Delete internal article |
|---|:---:|:---:|
| Public writer/owner | ✓ Own | ✗ |
| Public approver | ✗ | ✗ |
| Public manager | ✓ | ✗ |
| Internal writer/owner | ✗ | ✓ Own |
| Internal approver | ✗ | ✗ |
| Internal manager | ✗ | ✓ |
| Admin Users | ✓ | ✓ |

The deletion confirmation intentionally uses a strong warning. It does not expose backend implementation or storage-path details to the end user.

### Published Article Deletion Queue

Draft, pending, and pending-failed articles are permanently deleted immediately when an authorised user confirms deletion. Published articles have an additional MFA-protected path:

```text
Retention setting greater than 0:
Published -> MFA confirmation -> Deletion queued -> hidden immediately
Admin can restore or permanently purge while the article is queued
Cleanup scheduler auto-purges after the configured retention period

Retention setting equals 0:
Published -> MFA confirmation -> permanent deletion immediately
```

The setting is in:

```text
Django Admin -> KB -> Site settings -> Article deletion queue retention (days)
```

The default is 7 days. Setting the value to `0` deliberately disables the recovery period for future published-article deletions and also makes existing queued rows due for purge on the next cleanup run.

Queued articles are removed from normal article lists, search results, normal detail access, and AI sync. The admin-only **Article deletion queue** provides restore and permanent-purge actions. Recovery actions are not available to ordinary users.

The queue lifecycle is recorded in general activity logs:

```text
ARTICLE_DELETE_QUEUED
ARTICLE_DELETE_RESTORED
ARTICLE_DELETE_PURGED
ARTICLE_DELETE_AUTO_PURGED
ARTICLE_DELETED  (immediate deletion, including retention = 0)
```

### 9.7 Admin Review Notes and History

When an article or pending update is returned as pending failed, reviewers can enter review notes. The current review note is shown to the article owner while the article/update is in a failed state.

Review notes are also stored in history, so previous feedback rounds are preserved for audit and review tracking.

### 9.8 Duplicate Article Title Protection

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

### 9.9 Article File Sync and Storage Isolation

Published public article content is written to OpenKB-compatible Markdown files under the public OpenKB data structure:

```text
openkb-data/raw/
openkb-data/wiki/sources/
```

Internal article Markdown is deliberately kept out of the public OpenKB tree and written under the internal workspace:

```text
openkb-data-internal/raw/internal/
openkb-data-internal/wiki/sources/
```

Pending updates are not written as the live published article version until approved. Internal generated metadata is removed from public display, search snippets, and AI output so users do not see sync markers.

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

When an orphan article is published, the same published-article deletion setting is used: retention greater than `0` moves it into the recoverable deletion queue, while retention `0` permanently deletes it immediately. Draft, pending, and pending-failed orphan articles delete immediately. Orphan assignment/deletion actions are recorded in activity logs.

## 11. Article Browsing, Search, Views, Voting, and Homepage Tabs

### 11.1 Login-Protected Article Listing

The main website is login protected. Published article listing, article search, article details, voting, and the AI chatbot are available only after authentication and MFA completion where applicable.

The public article area is available from `/home/`. The internal article area is available from `/internal/` only to users with internal article access.

Draft, pending, pending failed, and unapproved pending-update content are not visible unless the current user owns the article, is a full admin, or is using the correct explicit review workflow for that scope.

### 11.2 Public/Internal Listing Behaviour

| Area | Public listing | Internal listing |
|---|---|---|
| Route | `/home/` | `/internal/` |
| Required access | Public article view access | Internal article view access |
| Content shown | Published public articles | Published internal articles |
| Tabs | Trending Topics, Most Liked, Most Recent Articles | Simple internal list with pagination |
| Visibility labels | Internal tags shown only to users with internal access | Internal article context shown |

Users with internal article access may see internal article tags and can use internal routes. Users without internal access cannot open internal article pages or internal uploaded images by guessing URLs.

### 11.3 Simple Title and Keyword Search

The main search intentionally stays simple and predictable. It matches only:

```text
published article title
published article keywords manually entered by users/admins
```

It does not search article body content, Markdown files, author names, OpenKB paths, internal metadata, or relevance scores. This reduces unnecessary scanning and makes search behaviour easier for users to understand. Normal matching results are ordered by most recently updated article first, not by view count or AI-derived relevance.

Search scope is role-aware:

| Search entry point | Scope |
|---|---|
| Main public search by normal public users | Published public articles only |
| Main public search by internal-capable users | Published public + internal articles |
| Internal search | Published internal articles only |

### 11.4 Search Suggestions and Clickable Keywords

The search bar suggestion dropdown uses the same title/keyword-only search logic. It returns clickable published article titles only and does not expose article body excerpts or raw OpenKB paths. Selecting a suggestion opens the accessible matching article directly. Internal article suggestions are only returned to users who already have internal visibility access.

Displayed article keywords on homepage/listing cards and article details are also clickable. Selecting one submits that keyword to the normal title/keyword search. This is a convenience shortcut only; it does not turn on full-body indexing.

Management filters use immediate form submission for the visibility/status selector on **Manage my articles**, **Manage pending articles**, and **Scan orphan articles**. Text search fields remain explicit and use the Search button.

### 11.5 Homepage Article Tabs

The public homepage article panel uses one container with tabs:

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

The internal article page intentionally uses a simpler list rather than public trending/most-liked panels.

### 11.6 Article Count Site Setting

Admins can configure how many articles appear per page from Django Admin -> KB -> Site settings.

```text
Articles per page
Minimum: 5
Maximum: 100
Default: 10
```

The setting is validated in admin and also clamped at runtime for safety.

### 11.7 View Counts

Each article stores a `view_count`. Views are tracked per user session to avoid simply refreshing the same article repeatedly to increase the count. View access is still checked against the article visibility.

### 11.8 Voting and Dislike Count Visibility

Signed-in users can vote on accessible published articles:

- Helpful / thumbs up.
- Not helpful / thumbs down.
- One vote per user per article.
- Users can change or remove their vote.

Helpful counts are visible to users. Dislike/not-helpful counts are more restricted:

| User type | Can see dislike counts |
|---|:---:|
| Regular User | ✗ |
| Article Writer | ✗ |
| Article Approver | ✗ |
| Article Manager | ✓ |
| Internal User | ✗ |
| Internal Article Writer | ✗ |
| Internal Article Approver | ✗ |
| Internal Article Manager | ✓ |
| Admin Users | ✓ |

Admins can review vote details through Django Admin and through activity logging.

### 11.9 Manual Existing-Keyword Suggestions

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

The project integrates with OpenKB through the local `OpenKB-main` folder and two separate data folders:

| Folder | Purpose |
|---|---|
| `openkb-data/` | Public OpenKB workspace. Contains published public articles only. |
| `openkb-data-internal/` | Internal OpenKB workspace. Contains a separate index for users with internal article access; it includes published public + published internal articles. |

The public folder must be initialised before the chatbot is used:

```bash
docker compose exec web sh -lc "cd /app/openkb-data && PYTHONPATH=/app/OpenKB-main python -m openkb.cli init"
```

Synchronise article data with:

```bash
docker compose exec web python manage.py sync_openkb_ai --scope all
```

The supported scopes are `public`, `internal`, and `all` (the default). Public sync rebuilds only the public index; internal sync rebuilds the separate public-plus-internal index.

### 15.2 Internal Article AI Isolation

Internal article source files are not written into the public OpenKB tree. Query scope is selected from the current user’s article permission:

| User permission | AI index used |
|---|---|
| Public article access only | Public index under `openkb-data/` |
| Internal article access | Internal index under `openkb-data-internal/`, containing published public + internal articles |

The verification command is:

```bash
docker compose exec web python manage.py check_internal_article_isolation --sync-first
```

### 15.3 Background Job Architecture

The Ask OpenKB AI browser request does not wait for OpenKB/provider work. After the prompt passes input and rate-limit checks, Django creates an opaque UUID job ID and returns `202 Accepted`. The dedicated `ai-worker` Celery service retrieves that job from Redis and runs the OpenKB query independently of the web/Gunicorn request.

This gives the following behaviour:

- A user can move between normal signed-in pages while the AI task continues.
- The global chat widget keeps its open state, completed messages, pending job IDs, and unfinished draft in the current browser tab’s `sessionStorage`.
- The widget polls the owner-only job-status endpoint and displays a completed response on whichever normal page is open.
- Chat history is not stored in PostgreSQL. It is cleared when the browser tab session ends, or when the user chooses **Clear chat**.
- **Clear chat** marks pending jobs cancelled and discards late results. A currently running subprocess may finish safely, but the cancelled result is never returned to the widget.

Temporary job records use the Django shared cache (Redis in production). Prompt and result text are Fernet-encrypted before storage. Celery receives only the opaque job ID, not prompt text. Records expire automatically after `OPENKB_AI_JOB_TTL_SECONDS` (1800 seconds by default).

The worker confirms the job owner is still active and still has the required public/internal article permission before executing. The polling endpoint checks job ownership and current permission again before returning a result, preventing a user from retrieving another user’s job or a result after internal access is removed.

### 15.4 AI Provider

The provider and model are configured through environment values:

```env
OPENKB_AI_PROVIDER=openkb-cli
OPENKB_AI_MODEL=gemini/gemini-2.5-flash
```

API keys belong in Vault, not source code or `.env`. The project supports `AI_API_KEY` and provider-specific secret names such as `GEMINI_API_KEY`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY` when Vault is configured to provide them.

### 15.5 Rate Limits, Fixed 24-Hour Quota, and Resource Controls

The chatbot is login-protected. It uses two separate per-user controls:

| Control | Default | Behaviour |
|---|---:|---|
| Short-burst limit | 5 prompts / 60 seconds | After the sixth prompt in the window, the account is blocked from Ask OpenKB AI for 1800 seconds (30 minutes). |
| Fixed 24-hour quota | 20 prompts | The first accepted prompt creates the user counter and starts its 24-hour expiry. Later prompts increment the counter but do not extend the expiry. After expiry, Redis removes the key and the next accepted prompt starts a new window. |
| Prompt length | 1000 characters | Longer prompts are rejected before a quota slot is consumed. |
| OpenKB timeout | 90 seconds | Limits one worker query. |
| Worker concurrency | 1 | The default `ai-worker` process handles one task at a time. |
| Shared query cap | 2 | Redis-backed upper guard across worker processes. With one worker, only one task runs at a time. |

The fixed quota is configured in **Django Admin → Site settings → OpenKB AI rate limits → OpenKB AI prompts per 24 hours**. The administrator may set a value from 1 to 1000; the default is 20. The site setting is cached briefly to avoid a database read on every prompt and is invalidated immediately after an Admin save.

Quota consumption uses an atomic Redis operation. It stores one small counter per active user and an expiry of 86,400 seconds. There is no database counter, scheduled reset job, timezone calculation, or expiry extension from later prompts. Prompts that pass validation and are accepted for AI processing consume quota even if queue submission later fails or the user clears the chat; this avoids retry-based resource-abuse bypasses. Invalid/empty/overlong prompts and burst-blocked requests do not consume quota.

Keep `OPENKB_AI_WORKER_CONCURRENCY=1` for the current single-VM deployment unless capacity, provider limits, and OpenKB behaviour have been evaluated for higher parallelism. Redis is required in production so limits, jobs, and query controls remain shared across services.

### 15.6 Related Article Recommendations and Output Cleanup

The worker can attach related articles from the local database. The same visibility checks used by normal search apply: public users receive public articles only; internal-capable users may receive accessible public and internal articles.

OpenKB internal metadata and generated sync markers are removed before display. The browser renders model text with text content rather than injecting it as HTML.

### 15.7 AI Logging and Privacy

Long-lived activity logs record only operational metadata: authenticated account reference, source identifier, scope, question length, execution type, quota usage, rate-limit events, and outcome. The question and answer text remain only in the encrypted, temporary job record and expire automatically.

## 16. Internationalisation and Local Translation

The UI uses Django's local translation system through `.po` and `.mo` locale files. Translation is local/offline and does not call an external AI translator.

Supported language choices are configured in `settings.py` and exposed through the language selector. Anonymous users store language preference in a cookie. Logged-in users also save the preference in their user profile.

The locale files have been updated across all supported languages so extracted UI strings have translations and compiled `.mo` files. This includes newer admin-tool labels, orphan article workflow messages, MFA text, activity logging labels, and profile/account-source messages.

This design keeps UI translation independent from the AI chatbot and avoids sending translation content to external AI services.

## 17. Admin Tools, Django Admin, and Access Control

### 17.1 Admin Tool Restriction

High-risk maintenance tools and Django Admin are protected by `Admin Users`/superuser checks. This includes the bulk import/export, orphan-management, stray-upload cleanup, deletion-queue, Site settings, user/group administration, and Django Admin surfaces. `Admin Users` is the source of truth for full administrator access and automatically syncs members to `is_staff=True` and `is_superuser=True`. Staff status alone is not treated as a separate admin role. Direct user permission checkboxes do not grant Django Admin access by themselves.

Visibility-specific review pages are narrower: matching Article Approver/Manager roles can use their scope's pending-review workflow without gaining Django Admin or the high-risk maintenance tools. Those scope rules are listed in Section 17.5.

Non-admin users receive 404 responses for admin-only main-site maintenance tools to reduce route discovery usefulness. The Django admin login path is hidden; admins should sign in through the normal login flow and then open `/admin/`.

### 17.2 Admin Network and Step-Up MFA Restriction

The deployment can restrict Django Admin access by source IP/CIDR, such as a VPN or internal subnet. A correct username/password is not enough if the request source is outside the allowed admin CIDR range.

Django Admin also uses an admin step-up MFA gate. A user must first complete normal login/MFA, then pass the admin gate before entering `/admin/`. The admin gate has its own configurable idle timeout so returning to admin after the timeout requires MFA again. The default is 600 seconds (10 minutes); code enforces a minimum of 60 seconds and a maximum of 86400 seconds.

### 17.3 Main Admin and Management Tools

Admin/management tools include:

- Clean stray upload files.
- Bulk import/export articles, including public/internal article data.
- Manage public pending articles and pending updates.
- Manage internal pending articles and pending updates.
- Review suggested articles in the correct visibility scope.
- Scan and manage orphan articles.
- View the published-article deletion queue, restore queued articles, or permanently purge selected queued articles.
- Configure site settings such as article count per page, log retention, session timeout, and authentication lockout policy stages.
- Manage user roles, account type/source recovery, and direct content-permission add-ons.
- View authentication activity logs through Django Admin.
- View general activity logs through Django Admin.
- View upload audit records through Django Admin.

#### Site Settings Reference

The singleton **Site settings** record controls the following operational limits and retention values:

| Setting | Default | Important behaviour |
|---|---:|---|
| Stray upload cleanup minimum age | 1440 minutes | Files newer than the threshold are not treated as stray. `0` allows immediate stray-file cleanup. |
| Article deletion queue retention | 7 days | Published articles remain recoverable in the admin queue for this many days. `0` makes published deletion immediate after MFA confirmation. |
| Article image upload limit | 50 images | Maximum images across an article's draft/pending/published/pending-update versions. `0` disables article image uploads. |
| Articles per page | 10 | Used by article lists/search and homepage tabs. Runtime range is clamped to 5-100. |
| Authentication activity-log retention | 30 days | `0` retains authentication/MFA logs indefinitely. |
| User session timeout | 8 hours | Fixed authenticated and pending-MFA expiry. Administrators may set 1 to 168 hours. |
| General activity/admin-log retention | 30 days | `0` retains general and Django Admin activity logs indefinitely. |
| Admin log rows per page | 200 | Recommended range is 50-500. |
| Admin allowed CIDRs | `<ADMIN_ALLOWED_CIDR>`, loopback | Inner Django Admin allowlist. Nginx may enforce an additional outer allowlist. |
| Lockout escalation memory | 604800 seconds | Failed password/MFA escalation history is retained for 7 days unless successful authentication or an admin reset clears it. |
| Admin MFA idle timeout | 600 seconds | 10 minutes by default; code clamps values from 60 to 86400 seconds. |
| OpenKB AI prompts per 24 hours | 20 prompts | Per-user fixed window. The first accepted prompt starts the 24-hour expiry; later prompts do not extend it. Runtime range is 1-1000. |

### 17.4 Group and User Permission Management

Django Admin Groups represent the main role groups:

```text
Disabled User
Regular User
Article Writer
Article Approver
Article Manager
Internal User
Internal Article Writer
Internal Article Approver
Internal Article Manager
Admin Users
```

The Groups admin page shows current users in each group and provides a searchable left/right selector to add or remove users from the group.

The Users admin page provides direct Knowledge Repository permission checkboxes for one-off content-access exceptions:

```text
Can view published public articles
Can add/submit public articles for approval
Can approve/manage pending public article reviews
Can delete public articles
Can view internal articles
Can add/submit internal articles for approval
Can approve/manage pending internal article reviews
Can delete internal articles
Can use Knowledge Repository admin tools
```

These direct user permissions are add-on only. The final content permission result is:

```text
final content access = group permissions + direct user permissions
```

Direct permissions should be used sparingly because they can create custom combinations that are harder to audit than the standard role groups. `Disabled User` still overrides direct content permissions.

### 17.5 Visibility-Specific Review and Management Tools

| Tool | Main route | Required access |
|---|---|---|
| Public pending articles | `/profile/admin/pending-articles/` | Public approver/manager role, matching direct review permission, or Admin Users |
| Internal pending articles | `/internal/profile/admin/pending-articles/` | Internal approver/manager role, matching direct review permission, or Admin Users |
| Public owner article list | `/profile/articles/` | Public writer/owner scope or higher |
| Internal owner article list | `/internal/profile/articles/` | Internal writer/owner scope or higher |
| Public article creation | `/suggest/` | Public add permission, public manager, or Admin Users |
| Internal article creation | `/internal/suggest/` | Internal add permission, internal manager, or Admin Users |
| Article deletion queue | `/profile/admin/deletion-queue/` | `Admin Users` only |

Scope checks are repeated in the view layer. A user seeing a button in the UI is not the only protection; forged POSTs and direct URL access are also checked server-side.

### 17.6 Article Import/Export

Bulk import/export supports article content and referenced upload files. Zip member names are normalised to avoid unsafe paths. Duplicate article titles are detected during import.

The export package is an administrator backup/migration file. It includes the actual article data needed to restore the knowledge base, such as:

```text
article title
article body / Markdown content
article visibility: public/internal
keywords
published status and workflow status
pending update title/body/keywords when present
pending update image references when present
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

When restoring from a split export, the admin should extract the outer package first and import each part ZIP one at a time. Import restores article visibility, keywords, article body content, pending update fields, and referenced images. Published public imports are synced back into the public OpenKB-compatible Markdown files. Published internal imports are kept in the internal OpenKB workspace.

### 17.7 Authentication Lockout Administration

Administrators can manage password/MFA lockout policy stages through the Site settings page. Stages are shown inline and can be added, removed, reordered, enabled, or disabled. The simplified default is 10 failures -> 5 minutes twice, then 5 failures -> 15 minutes twice, then 3 failures -> 1 hour repeatedly.

Administrators can reset a user's lockout state from:

```text
Django Admin -> Users -> open user -> Authentication lockout -> Reset password/MFA lockout
```

Bulk reset actions are also available on selected Users and User Profiles. Reset actions are recorded in authentication activity logs.

### 17.8 Django Admin Usability

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

General site and content actions are logged in `ActivityLog`. This is separate from authentication logs so admins can review content and usage behaviour without mixing it with login/MFA activity. The model is append-only for normal application and Django Admin use; retention cleanup removes old records according to the configured policy.

Examples of logged activity include:

| Area | Example activity |
|---|---|
| Articles | Article created, updated, submitted, approved, published, returned as pending failed, and deleted |
| Published deletion queue | Article queued for deletion, restored, manually permanently purged, automatically purged by scheduler, or deleted immediately when the retention setting is `0` |
| Views | Article viewed once per browser session |
| Votes | Vote up, vote down, vote changed, vote removed |
| Uploads | Image uploaded, image deleted, stray upload cleanup |
| Profile security changes | Local profile email updated; local password changed |
| AI | OpenKB AI operational metadata, rate-limit/quota events, background-job execution status, and question length only; prompt/answer text is not kept in the long-lived log |
| Imports/exports | Bulk article import and export |
| Admin tools | Orphan article assigned/deleted, pending article administration, and other recorded tool actions |
| Django Admin | Admin article save/delete/bulk actions where applicable |

Search terms and profile language-choice changes are intentionally not stored as general activity history. This avoids collecting low-value, high-volume interaction data.

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

The scheduled cleanup service runs stray-upload cleanup, published-article deletion-queue cleanup, authentication-log cleanup, and general/admin activity-log cleanup on its configured interval (24 hours by default). Cleanup commands can also be run manually:

```bash
docker compose exec web python manage.py cleanup_article_deletion_queue --dry-run
docker compose exec web python manage.py cleanup_article_deletion_queue --noinput

docker compose exec web python manage.py cleanup_auth_activity_logs --dry-run
docker compose exec web python manage.py cleanup_auth_activity_logs --noinput

docker compose exec web python manage.py cleanup_activity_logs --dry-run
docker compose exec web python manage.py cleanup_activity_logs --noinput
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

Vault encrypts stored secrets at rest and gives the application access through the configured Vault token file. The application token is created as owner/group `0:10001` with mode `0440`: root may manage it, while only the unprivileged application group may read the bind-mounted file. The project does not rely on hardcoded production secrets in source code. The Django PostgreSQL password fallback is disabled in production, so missing production database secrets fail startup instead of silently using a weak default.

## 20. LDAPS Security

The project supports Active Directory authentication over LDAPS on port 636. LDAPS protects LDAP bind credentials in transit using TLS. When `LDAP_ENABLED=true`, valid AD users returned by `LDAP_USER_SEARCH_BASE` and `LDAP_USER_FILTER` may sign in. The LDAP bind account is used only to search AD and must remain low-privilege and read-only.

In the current lab configuration, LDAPS testing confirmed:

- DNS resolution from the Docker container to the Domain Controller.
- TLS handshake success.
- TLS 1.3 negotiation.
- Certificate subject matching the Domain Controller hostname.
- Certificate issuer matching the AD CS CA.

The encryption strength depends on the TLS cipher negotiated by the server and client. The important implementation point is that the project validates the server certificate and does not send LDAP bind credentials over plaintext LDAP in secure mode.

## 21. HTTPS and Nginx Security Headers

Nginx serves the application on host port `8080`; a perimeter firewall can safely publish only standard HTTPS port `443` and translate it to this private host port. The project includes security headers such as:

- `Strict-Transport-Security`
- `X-Content-Type-Options`
- `X-Frame-Options`
- `Referrer-Policy`
- `Permissions-Policy`
- `Content-Security-Policy`

The local lab deployment can use a self-signed certificate generated with the direct internal server IP as an IP subject-alternative name. For a real public deployment, use a trusted certificate and configure the final host names in `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS`. Nginx uses a read-only root filesystem with a writable `/tmp` `tmpfs`; its temporary request directories are intentionally configured directly below `/tmp` so uploads and proxied requests still work.

The current Content Security Policy permits `'unsafe-inline'` for scripts and styles because existing templates still contain inline JavaScript/CSS and a small number of inline event handlers. This is a deliberate compatibility trade-off, not a claim of a strict nonce/hash-based CSP. Removing it without a complete template/static-assets refactor would break login, article editing, and admin tools. A future hardening task is to move inline code into static files and then remove `'unsafe-inline'`.

## 22. Search-Engine Crawler Controls

DjOpenKB is a private, login-protected knowledge repository even when the host is reachable from a public network. It is not intended to appear in search engines.

`GET /robots.txt` is intentionally available without login and returns:

```text
User-agent: *
Disallow: /
```

The response is served as plain text with a short public cache lifetime. The project does not generate a sitemap because there are no public pages intended for indexing.

Django also adds the following `X-Robots-Tag` to application responses other than `/robots.txt`:

```text
noindex, nofollow, noarchive, nosnippet, noimageindex
```

These mechanisms are defence in depth for cooperative crawlers only. They do not protect routes, content, or secrets. Actual protection remains login enforcement, MFA, role/public-internal scope checks, restricted admin access, and 404 responses for unauthenticated or unauthorised routes.

## 23. Dependency Pinning

The project pins Python package versions/ranges in `requirements.txt` to reduce unexpected breakage from upstream updates.

Current dependencies are:

```text
Django==6.0.6
gunicorn==26.0.0
Markdown==3.10
bleach==6.3.0
Pillow==12.2.0
python-dotenv==1.2.1
django-auth-ldap==5.2.0
psycopg[binary]==3.3.2
pyotp==2.9.0
qrcode[pil]==8.2
cryptography>=42.0.0
redis>=5.0.0,<7.0.0
```

This helps ensure the same behaviour across developer machines and deployment servers. Dependency review and rebuilding should still be performed when applying security updates.

## 24. Database and Storage

### 24.1 PostgreSQL

PostgreSQL is the default database. The database credentials are provided through Docker Compose and Vault.

### 24.2 SQLite Fallback

The intended deployment uses PostgreSQL through Docker Compose. SQLite fallback is not part of the supported deployment path and should not be relied on for normal testing or production use.

### 24.3 Article Storage

Article metadata is stored in PostgreSQL. Article Markdown content is also mirrored into OpenKB-compatible folders so OpenKB can index and use it.

### 24.4 Redis Cache and Counters

Redis is used as the shared Django cache backend in production. It stores temporary counters and lock flags for authentication lockout, AI burst limits/cooldowns, fixed 24-hour AI quota counters, encrypted short-lived AI job records, job update locks, and AI concurrency control. Redis DB 2 is the default Celery broker for the `ai-worker` queue. It is not the primary database and should not be used as the only backup source for article data.

## 25. Main Security Controls Summary

| Area | Implemented control |
|---|---|
| Password storage | Django password hashing for local users. AD passwords managed by Active Directory. |
| Account source separation | Local and AD users are separated by stored profile metadata, not email domain guessing. |
| MFA | TOTP MFA required after password/AD authentication. |
| Password/MFA lockout | Progressive admin-configurable lockout stages with repeat counts, Redis-backed counters, automatic reset after successful login/MFA, and admin reset actions. |
| Sensitive profile changes | Fresh MFA/OTP required for sensitive local profile changes. AD-managed values are blocked locally. |
| Sessions | Configurable fixed session timeout (8 hours by default), server-side sign-in timestamp, browser cookie aligned to the remaining lifetime, and secure cookie settings. |
| CSRF | Django CSRF middleware and token-protected POST forms/endpoints. |
| XSS | Markdown rendered then sanitised with Bleach. |
| Upload safety | Extension allowlist, 2 MB size limit, Pillow image verification, pixel limit, generated filenames. |
| Access control | Public/internal article visibility checks, scoped writer/approver/manager roles, protected image serving, admin-only tools, recovery queue restricted to Admin Users, and 404 for non-admin admin-tool access. |
| Article deletion recovery | Published deletion requires MFA and normally enters a configurable admin recovery queue; non-published content deletes immediately; setting `0` deliberately enables immediate published deletion. |
| Orphan content | Admin-only orphan article scan, assign, delete, and confirmation workflow across article visibility scopes. |
| Secrets | Runtime secrets stored in Vault instead of source code, with production database fallback disabled. Vault app token is readable only by root and application group `10001` (`0:10001`, mode `0440`). |
| LDAP | LDAPS with certificate validation, low-privilege read-only bind-account guidance, connection/operation timeouts, and configurable AD user-search scope. |
| HTTPS / edge protection | Nginx HTTPS and security headers; POST-only endpoint rate limits, per-IP connection limits, request timeouts, 3 MB ordinary request size limit, restricted 100 MB bulk-import route, and current CSP inline compatibility trade-off. |
| Crawler controls | `/robots.txt` disallows all cooperative crawling and application responses receive an `X-Robots-Tag` no-index header. This is not access control. |
| Auth logs | Read-only auth/MFA logs with IP/user-agent details and retention cleanup. |
| Activity logs | Article, deletion queue, vote, upload, local profile email/password, AI, import/export, and admin-tool activity logging with retention cleanup. Search/language history is intentionally excluded. |
| Admin log display | Admin log pages use pagination and horizontal scrolling for wide tables. |
| AI endpoint | Public/internal index isolation, role-scoped query selection, encrypted short-lived background jobs, owner/scope checks before execution and result delivery, prompt length limit, 5 questions per 60 seconds, 30-minute burst cooldown, Admin-configurable fixed 24-hour user quota (default 20), timeout/query controls, safe text rendering, and privacy-safe activity metadata. |
| Container hardening | Private Compose backend networks, unprivileged web/worker/scheduler services, read-only root filesystems, temporary `tmpfs`, capability dropping, `no-new-privileges`, PID limits, and a network-isolated mount-permission helper. |
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
openkb-data-internal/.openkb/
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

# Confirm the mount-permission helper and Vault token permissions after deployment.
docker compose logs --tail=80 app-permissions-init
stat -c '%u:%g %a %n' vault/keys/djopenkb-app-token.txt
```

Verify crawler controls through Nginx:

```bash
curl -k https://<server-ip>:8080/robots.txt
curl -k -I https://<server-ip>:8080/login/
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
docker compose exec web python manage.py cleanup_article_deletion_queue --dry-run
docker compose exec web python manage.py cleanup_article_deletion_queue --noinput
docker compose exec web python manage.py cleanup_auth_activity_logs --dry-run
docker compose exec web python manage.py cleanup_auth_activity_logs --noinput
docker compose exec web python manage.py cleanup_activity_logs --dry-run
docker compose exec web python manage.py cleanup_activity_logs --noinput
```

Useful support diagnostics:

```bash
# Diagnose MFA time/device status without displaying a TOTP secret
docker compose exec web python manage.py diagnose_mfa <username-or-email>

# Reset one user's MFA from the server when the normal admin reset path is unavailable
docker compose exec web python manage.py reset_user_mfa <username-or-email> --yes

# Test LDAP bind/search; add a username to test that lookup
docker compose exec web python manage.py test_ldap_auth <ad-username>
```

`repair_kb_schema --noinput` runs automatically during normal web-container startup after migrations. `seed_djopenkb_roles` is a recovery/maintenance command for recreating or normalising default role groups and should be used carefully on a test or maintenance window.

Build and restart after dependency or Docker Compose changes:

```bash
docker compose build web ai-worker cleanup-scheduler
docker compose up -d
```

## 28. Operational Notes for Administrators

- Keep `DJANGO_DEBUG=false` for deployment.
- Match `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` to the exact browser URL: direct internal-IP access includes `:8080`; later public firewall/DNS access on 443 does not.
- Keep Vault secrets out of shared packages.
- Confirm `app-permissions-init` exits successfully and `vault/keys/djopenkb-app-token.txt` remains `0:10001` with mode `0440` after Vault maintenance.
- Do not weaken Nginx read-only filesystem settings to solve temporary-path errors; retain the direct `/tmp/*_temp` paths instead.
- Treat the worker `egress` network as structural separation, not a replacement for host/firewall egress policy.
- Use LDAPS with certificate validation for AD.
- Use the activity logs and authentication logs to review suspicious behaviour.
- Keep both OpenKB AI controls enabled: the short-burst cooldown and the fixed per-user 24-hour quota. Start with the default quota of 20 prompts.
- Keep log retention at 30 days unless longer investigation history is needed.
- Use `--dry-run` before cleanup commands when validating behaviour.
- Review the Article deletion queue retention setting. Keep the default 7-day recovery period unless immediate permanent published deletion (`0`) is genuinely intended.
- Test `/robots.txt` and the `X-Robots-Tag` response after an Nginx/Django routing change.
- Admin log pages can show 500 rows per page, but very large logs should still be filtered by date, user, event type, or action.

- Keep the site login-only unless there is a clear business requirement for anonymous article browsing.
- Keep the group model clear: `Disabled User`, fallback `Regular User`, public roles (`Article Writer`, `Article Approver`, `Article Manager`), internal add-on roles (`Internal User`, `Internal Article Writer`, `Internal Article Approver`, `Internal Article Manager`), and `Admin Users`.
- Use direct user permission checkboxes only for one-off exceptions because they add permissions on top of group permissions.
- Keep `.dockerignore` updated so secrets and runtime folders are not copied into Docker images.

## 29. Final Notes

DjOpenKB is designed as a secure internal knowledge base and cyber security project. The current implementation covers authentication, MFA, LDAPS, HTTPS, CSRF, upload validation, Markdown sanitisation, audit logging, published-article deletion recovery, public/internal article review workflows, orphan article management, crawler no-index defence in depth, scoped role separation between local and AD users, and role-scoped OpenKB AI integration.

For a controlled local or intranet deployment, the implemented controls are suitable as long as secrets are not shared, Vault is seeded correctly, LDAPS certificates are mounted correctly, debug mode remains off, the login-only route policy is maintained, public/internal role groups are reviewed, internal OpenKB isolation is checked after major changes, and cleanup/log retention/deletion-queue settings are reviewed by administrators. Crawler controls should be retained as defence in depth, while access control remains the real security boundary.
