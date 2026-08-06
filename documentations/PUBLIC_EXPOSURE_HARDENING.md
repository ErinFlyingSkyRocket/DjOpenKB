# Public Exposure and Direct-IP Hardening Notes

This document records the security hardening applied to the firewall-published Knowledge Repository deployment. It covers the current internal direct-IP development phase and the later public-DNS phase. A public DNS name is not required to keep developing safely on a controlled internal network.

## 1. Current direct internal-IP development

Users reach the service directly on the Linux host through standard HTTPS port `443`, so the browser URL does not require a port suffix. For the current development VM, an example is:

```text
https://<INTERNAL_SERVER_IP>
```

Use the exact reachable server IP in `.env`:

```env
DJANGO_ALLOWED_HOSTS=<INTERNAL_SERVER_IP>
DJANGO_CSRF_TRUSTED_ORIGINS=https://<INTERNAL_SERVER_IP>
DJANGO_SESSION_TIMEOUT_HOURS=8
```

`localhost` and `127.0.0.1` are not remote-user addresses. They refer to the Linux server itself and are not needed for a browser running on another workstation. The temporary Nginx configuration deliberately uses:

```nginx
server_name _;
```

Django still enforces the allowed host header through `DJANGO_ALLOWED_HOSTS`.

Generate the development certificate with the direct server IP as an IP subject-alternative name (SAN):

```bash
cd /opt/DjOpenKB
sudo sh nginx/certs/generate-localhost-cert.sh <INTERNAL_SERVER_IP>
sudo docker compose up -d --force-recreate nginx
```

The certificate remains self-signed. Trust its `.crt` on the approved development devices to remove browser warnings. Replace it with a certificate issued for the final DNS name before public rollout.

## 2. Later firewall and public-DNS configuration

When a perimeter firewall or public address is introduced, publish only standard HTTPS on public TCP `443` and forward it to host TCP `443`. Use the public IP or final DNS hostname exactly as seen by the browser:

```env
# Public-IP phase, before DNS exists
DJANGO_ALLOWED_HOSTS=<PUBLIC_SERVER_IP>
DJANGO_CSRF_TRUSTED_ORIGINS=https://<PUBLIC_SERVER_IP>

# Final DNS phase
DJANGO_ALLOWED_HOSTS=<PUBLIC_HOSTNAME>
DJANGO_CSRF_TRUSTED_ORIGINS=https://<PUBLIC_HOSTNAME>
```

Before making the service broadly reachable, replace `server_name _;` with the final DNS name, install a trusted certificate for that name, and add a separate default Nginx server that rejects unknown `Host` headers. The external firewall should publish only TCP `443` (and, only if required for certificate issuance, TCP `80`).

## 3. Implemented Nginx edge controls

Nginx is the only service published to the network on standard host HTTPS port `443`. PostgreSQL, Redis, Gunicorn, and Docker are not published. Vault is bound only to host loopback (`127.0.0.1:8200`) for local administrator access and is not externally reachable through the network firewall.

The reverse proxy now applies these controls before traffic reaches Django or Active Directory:

| Control | Behaviour |
|---|---|
| POST-only request rate limits | Login, normal MFA, Admin MFA, AI question submission, article-image upload, and admin bulk import are rate-limited per source IP. Normal GET page loads and browser refreshes are not counted. Exceeded limits return HTTP `429`. |
| Connection limits | Per-IP concurrent connection caps limit one client from consuming all Nginx workers. |
| Request-body limit | Ordinary requests are limited to `3 MB`. |
| Bulk-import exception | The authorised admin bulk-import endpoint alone permits up to `100 MB`, matching the application ZIP validation limit. |
| Timeouts | Header, body, proxy-connect, proxy-read, proxy-send, and keepalive timeouts prevent slow or stuck connections from holding resources indefinitely. |
| Admin network gate | Nginx does not keep a static Admin IP allowlist. Django Site settings can dynamically enable an IPv4/IPv6 address/CIDR allowlist; when disabled, source IP is unrestricted. The hidden `/admin/login/` route, superuser checks, and separate Admin MFA gate still apply. |

The rate limits use the TCP peer address seen by Nginx. With direct firewall NAT, this is normally the browser IP. If a CDN or Layer-7 reverse proxy is introduced later, configure `real_ip_header` and `set_real_ip_from` for the known proxy range before relying on IP-based rate limits or audit records. Never trust browser-supplied `X-Forwarded-For` directly.

### 3.1 Emergency recovery from an incorrect Admin IP allowlist

The dynamic Django Admin allowlist intentionally uses an implicit-deny policy when enabled. This means an administrator can lock out their own management device by saving the wrong address or CIDR range.

The supported recovery path is a server-side Django management command:

```bash
cd /opt/DjOpenKB
sudo docker compose exec web python manage.py reset_admin_ip_allowlist
```

This is an operational recovery control, not an authentication bypass. The command disables source-IP filtering and permanently clears the configured ranges. Normal account authentication, superuser checks, normal MFA, and the separate Admin MFA gate remain in force.

After recovery, configure a new allowlist in **Django Admin → Site settings → Django Admin access restrictions** before re-enabling the allowlist. There is no permanent emergency allowlist in `.env` or Nginx, reducing the risk of an overlooked fallback network range.

Server shell access is already a privileged administrative capability and should remain restricted to authorised infrastructure administrators.

### 3.2 Application text input limits

Nginx request-body and rate limits are complemented by Django-side character limits. Browser `maxlength` controls improve normal use. Django forms/model validation and the central middleware reject oversized query strings and normal URL-encoded form values before expensive authentication, search, database, or workflow processing. Upload/import/JSON endpoints retain their own direct validation.

| High-risk input | Maximum characters |
|---|---:|
| Login identifier | 254 |
| Password | 256 |
| MFA / OTP / TOTP code | 32 |
| Search query | 200 |
| Article title | 200 |
| Article keywords | 500 |
| Review comments / deletion reason | 4,000 |
| Video URL | 2,048 |
| Admin allowed IP/CIDR list | 4,096 |
| Unknown future text field fallback | 4,096 |

The article body remains a separately configurable `1,000`–`2,000,000` characters with a default of `100,000`. The OpenKB AI prompt remains configurable from `100`–`10,000` characters with a default of `1,000`. Removing `maxlength`, disabling JavaScript, or editing a normal request does not remove the form/model/middleware checks. The generic middleware intentionally does not parse every multipart body; each multipart endpoint must validate its accompanying text fields directly as well as its files. Treat arbitrary multipart re-encoding as a remaining hardening item rather than claiming the generic middleware alone covers it.

Nginx uses a read-only root filesystem. Temporary paths are intentionally under the writable `/tmp` `tmpfs`:

```nginx
client_body_temp_path /tmp/client_temp 1 2;
proxy_temp_path /tmp/proxy_temp 1 2;
fastcgi_temp_path /tmp/fastcgi_temp 1 2;
uwsgi_temp_path /tmp/uwsgi_temp 1 2;
scgi_temp_path /tmp/scgi_temp 1 2;
```

The image entrypoint may log that it cannot change the unused default Nginx configuration because the root filesystem is read-only. That informational message is harmless when Nginx subsequently starts without an `[emerg]` error. A `mkdir()` error for one of the Nginx temporary paths is not harmless and requires restoring the paths above.

### 3.3 Database-Backed Abuse and Integrity Limits

Several controls that previously depended mainly on request/session state are now persistent or transactional:

- Pending article images are limited per authenticated user across all sessions and browsers: 100 uncommitted images and 100 MB by default, both configurable in Site settings. Individual files are limited to 2 MB and pass extension, Pillow verification, decompression-bomb, and pixel-count checks.
- Simultaneous uploads for the same account are serialised before quota calculation.
- Article titles have a unique normalised database key, closing concurrent duplicate-title races.
- Bulk ZIP manifests reject unknown/duplicate fields, invalid types/lengths, unsupported workflow combinations, and transient deletion-queue states before article creation.
- Imported records pass model validation and database uniqueness inside a transaction.

Application-side Redis request limits are separately configurable in Site settings and take effect without editing Nginx:

| Application limit | Default | Scope |
|---|---:|---|
| Login POST submissions | 8 per minute | Per client IP |
| Normal/Admin MFA POST submissions | 10 per minute | Per client IP |
| Ordinary Django Admin POST changes | 120 per minute | Per signed-in administrator |

Setting one of these values to `0` disables only that application-side request limit. Nginx edge ceilings and progressive account/MFA lockouts remain separate.

Local account hardening is also centralised: self-service and Django Admin use the same 12-character complexity validator, while PostgreSQL-backed case-insensitive unique indexes prevent duplicate usernames and non-blank emails across concurrent write paths.

## 4. Docker network and container hardening

The Compose stack separates service connectivity into four networks:

| Network | Purpose |
|---|---|
| `frontend` | Nginx and Django web service only. Nginx can reach `web:8000`; it cannot join the database or Vault networks. |
| `app_backend` | Django web, PostgreSQL, Redis, AI worker, and cleanup scheduler. It is an internal Docker network and is not published to the host network. |
| `vault_backend` | Vault and only the services that need secrets. Nginx is not attached. |
| `egress` | A dedicated bridge attached to the AI worker for model-provider access. It is structural separation only; enforce actual outbound network policy with host/firewall controls if required. |

The `web`, `ai-worker`, and `cleanup-scheduler` services run as UID/GID `10001`, use read-only root filesystems, receive a limited writable `/tmp` `tmpfs`, set `no-new-privileges`, drop Linux capabilities, and have PID limits. The short-lived `app-permissions-init` helper is the only root application helper; it has no network access and prepares the static/OpenKB bind mounts for UID/GID `10001` before application services start.

A healthy deployment shows `app-permissions-init` exiting successfully after printing three `Prepared ...` lines. Do not continue troubleshooting Nginx/Django access until this helper has completed with exit code `0`:

```bash
sudo docker compose logs --tail=80 app-permissions-init
```

## 5. Static Vault Application Token and Permissions

The Vault initialisation process creates one long-lived static read-only application token and stores it at:

```text
vault/keys/djopenkb-app-token.txt
owner/group: 0:10001
mode: 0440
```

Only the application group can read the usable token. Its non-secret accessor is stored separately at:

```text
vault/keys/djopenkb-app-token-accessor.txt
owner/group: 0:0
mode: 0600
```

`vault-init` reuses the existing static token while Vault confirms that it is valid. It creates a replacement only when the token is missing, invalid, or when converting once from the earlier renewable-token mode. `vault-auto-unseal` now performs unsealing only; it does not renew or rotate the application token.

The token request uses the original long-lived TTL of `87600h`. Vault may cap that request according to its server lease configuration. The application therefore does not depend on the removed `VAULT_APP_TOKEN_PERIOD` or `VAULT_APP_TOKEN_RENEW_BEFORE_SECONDS` settings.

If a replaced token cannot be revoked immediately, its accessor remains in the root-only `vault/keys/pending-token-revocations.txt` queue for a later `vault-init` retry. This cleanup failure does not discard the working replacement or block application startup.

Verify the files without printing their contents:

```bash
cd /opt/DjOpenKB
sudo stat -c '%u:%g %a %n' \
  vault/keys/djopenkb-app-token.txt \
  vault/keys/djopenkb-app-token-accessor.txt
```

Expected modes are `0:10001 440` for the token and `0:0 600` for the accessor. Never make either file world-readable.

## 6. Fixed eight-hour session policy

All normal authenticated sessions and pending-MFA sessions have a fixed maximum lifetime of eight hours by default. The session deadline starts at the original sign-in attempt. Page activity, refreshes, and cookie renewal do not extend that original deadline. When the deadline is reached, the next request clears the session and returns the user to login.

The runtime setting is available in Django Admin:

```text
Site settings → Authentication and session settings → User session timeout (hours)
```

Allowed values are `1` to `168` hours. The `.env` value `DJANGO_SESSION_TIMEOUT_HOURS=8` is the safe startup fallback before the database setting is available.

## 7. Active Directory authentication scope

When `LDAP_ENABLED=true`, every valid AD account returned by
`LDAP_USER_SEARCH_BASE` and `LDAP_USER_FILTER` may sign in. Use these values
to keep the authentication search within the intended AD domain or
organisational unit.

```env
LDAP_USER_SEARCH_BASE=DC=company,DC=local
LDAP_USER_FILTER=(|(userPrincipalName=%(user)s)(sAMAccountName=%(user)s)(mail=%(user)s))
```

The LDAP bind account must only be able to search users. It must not be a
Domain Admin, local administrator, or interactive-login account. Do not allow
privileged AD accounts to use the site.

From the web VM, allow only the intended Domain Controller IPs on LDAPS TCP
`636`. Block SMB, RDP, WinRM, Kerberos, RPC, and broad internal-subnet access
from this web server.

## 8. CSP status

The application sends a strict per-response Content Security Policy with a fresh nonce and does not include `'unsafe-inline'`. Project-owned inline script and style blocks that need server-rendered values use this nonce. Inline event attributes and inline `style=` attributes are forbidden, while static JavaScript and CSS remain self-hosted.

The article-video feature adds only narrow external media exceptions: `frame-src` permits `https://www.youtube-nocookie.com` and `https://player.vimeo.com`, while `media-src` permits HTTPS direct video files. The server still generates and sanitises the player markup; arbitrary article-supplied iframe sources are not accepted. SharePoint/OneDrive direct-video validation probes only recognised Microsoft hosts and rejects external authentication redirects.

When adding a new dynamic inline block, preserve the `csp_nonce`; do not reintroduce `onclick=`, `onsubmit=`, `style=`, or `'unsafe-inline'`. Any new external frame/media host must be reviewed deliberately rather than broadly weakening the CSP.

## 9. Article Visibility Selection Enforcement

The visibility field is treated as an authorisation-sensitive workflow value, not as a trusted browser control. Creation and editing use separate server-side rules:

- During creation, the server accepts only visibility scopes for which the user has article-creation permission. A dual public/internal writer may choose either scope; a single-scope writer remains fixed to the permitted scope even when the request is modified manually.
- After creation, moving an article between Public and Internal is allowed only for a full Admin user or a user holding both `Article Manager` and `Internal Article Manager`. Both manager groups are checked deliberately; direct permission combinations, dual writer roles, dual approver roles, and single-scope managers do not receive this operation.
- The edit view recalculates permission from the authenticated user and current article on every request. Hiding or enabling the HTML selector, altering a hidden field, disabling JavaScript, or changing `article_visibility` through an interception proxy cannot bypass the server rule.
- Every article detail, image, review, edit, delete, search, and AI-data path continues to enforce the resulting Public/Internal scope after a permitted change.

Regression coverage is maintained in `kb/tests/permissions/test_article_visibility_edit_permissions.py`, including creation selection, single-scope request tampering, dual-writer edit tampering, both manager directions, and the Admin override.

### 9.1 Persistent New-Article Checkpoint and Upload Ownership

The New Article page uses a private database-backed `ArticleCreationWorkspace` rather than relying on browser memory or a session-only image list. The workspace is owned by exactly one authenticated user and persistently stores checkpoint fields plus the generated filenames uploaded in that editor context until the user explicitly completes or resets the workflow. Autosave and discard requests require the exact owned workspace UUID and recheck article-creation scope on the server.

This closes the normal stray-image gap when an author pastes or uploads an image and then leaves without creating an article:

- In-application navigation is intercepted before redirecting and opens a blocking **Keep checkpoint and continue / Discard and continue** decision. The selected destination is held, and backdrop clicks, Escape, and a close icon cannot dismiss the dialog.
- **Keep checkpoint and continue** performs the latest server autosave, preserves the workspace and its owned images, and only then continues the selected navigation. It does not create a `SuggestedArticle` row and does not notify reviewers.
- **Discard and continue** deletes the workspace and its uncommitted owned images after commit before navigation continues.
- The explicit **Reset article** control uses the same owner-checked discard path and then opens a fresh blank New Article workspace. A normal page refresh restores the checkpoint instead of silently clearing it.
- Save Draft/Submit/Publish retains only images referenced by the resulting article and removes unused workspace uploads. Normal Submit continues invoking the existing reviewer-notification workflow, while Draft creation and checkpoint autosave do not notify reviewers.
- Active workspace images remain protected from stray cleanup but continue counting against persistent per-user pending count/byte limits.
- A Markdown body cannot claim another user's uncommitted filename; body text is not treated as proof of file ownership.
- Upload/delete requests require an authorised workspace or existing-article context, so changing JavaScript or forging a context identifier does not grant access to another user's temporary files.

Direct address-bar navigation, refresh, tab close, and browser close cannot show the custom application modal. The editor therefore autosaves after a short delay and attempts a final same-origin flush on `visibilitychange` and `pagehide`; the browser-native unsaved-changes warning is used only while a newer snapshot is still pending. A valid checkpoint never expires because of age. The scheduled cleanup service remains necessary only for genuinely orphaned files left after a browser or host crash, power loss, network interruption, failed filesystem deletion, interrupted finalisation, legacy data, or manually introduced files.

Multiple tabs share the same user checkpoint, so every content save includes a server revision, per-tab editor token, and increasing save sequence. A different stale tab cannot overwrite or discard a newer checkpoint and receives HTTP 409. Requests from the same editor are ordered by sequence so a late unload request cannot replace a newer save. This is an integrity safeguard in addition to normal owner and CSRF checks.

Permanent user deletion has a separate privacy cleanup path. A `pre_delete` user signal locks and snapshots the active checkpoint, removes checkpoint-specific image-upload and image-activity rows through the protected transaction-local account-deletion mechanism, and schedules physical file/session cleanup only after the database transaction commits. Existing articles use `SET_NULL` ownership and retain author snapshots. Disabled and inactive accounts do not trigger this path, so their checkpoints remain recoverable. Physical deletion rechecks article and other-workspace references before unlinking a file, preventing account removal from deleting an image still needed by preserved content. The account-deletion action itself remains in the append-only administrator audit trail according to its configured retention period, but the private checkpoint content and checkpoint-only image metadata are purged.

Regression coverage is maintained in `kb/tests/articles/test_article_creation_workspace.py` and `kb/tests/users/test_user_account_deletion_cleanup.py`.

## 10. OpenKB Retrieved-Content and Distributed-Lock Hardening

OpenKB Q&A treats article-derived text and image captions as untrusted data. Files are restricted by approved directory, extension, and size; page requests are bounded; a wiki-local instruction file cannot replace the package-owned trusted schema; and model output is never used to make authentication or visibility decisions.

AI concurrency and job-update locks use Redis atomic acquisition and owner-checked Lua release. When production Redis is configured but unavailable, acquisition fails closed rather than falling back to a process-local lock that another worker cannot see.

## 11. Supply-Chain Baseline

Direct Python and OpenKB dependencies, the OpenKB build backend, Python build tools, and production container tags are explicitly pinned. The final runtime image installs from the builder wheelhouse with `--no-index`. Verify the repository guard with:

```bash
python scripts/verify_supply_chain_pins.py
```

This guard complements, but does not replace, CVE scanning, a hash-locked transitive dependency set, SBOM review, and immutable image digests.

## 12. Current Residual Risks and Production Follow-Up

The controls above describe the current implementation, but they should not be interpreted as a claim that every production-hardening item is complete.

| Current limitation | Security/operational consequence | Recommended production follow-up |
|---|---|---|
| Generic input middleware skips arbitrary multipart parsing | Changing a normal form to multipart may bypass that middleware even when form/view validation still applies | Validate multipart `request.POST` fields centrally or on every route, excluding only `request.FILES` bytes |
| Pending upload quota covers uncommitted files only | Committing images into repeated drafts can move them outside the pending quota | Add total per-user committed count/byte quotas, disk-free thresholds, and indexed image-state accounting |
| Static Vault token is long-lived and shared by several services | Compromise of one token-bearing container has a wider secret-reading impact | Use service-specific policies and AppRole/Vault Agent short-lived tokens |
| Auto-unseal key is stored on the same host as Vault data | Full host compromise can obtain both encrypted data and unseal material | Use external KMS/HSM auto-unseal or separately held Shamir shares for production |
| Database article save and Markdown mirror are not one atomic transaction | Disk/full-permission failure can leave database and OpenKB files temporarily inconsistent | Use atomic file replacement, explicit sync status, retry/reconciliation alerts |
| Runtime database identity can run migrations and retention cleanup | Audit-table triggers are strong against normal ORM actions, not a fully separate tamper-proof boundary | Split migration, runtime, audit-writer, and cleanup database roles; export important logs externally |
| Direct dependency versions and image patch tags are pinned, but not hashes/digests | Rebuilds are improved but not fully immutable | Generate hash-locked transitive requirements, pin image digests, create SBOMs, and run CVE scans |

These items are suitable to track as production blockers or accepted risks. They are deliberately documented here so operational reviewers do not mistake defence-in-depth controls for absolute guarantees.

## 13. Required verification after an update

```bash
cd /opt/DjOpenKB
sudo docker compose config >/dev/null && echo "Compose configuration is valid"
sudo docker compose up -d --build
sudo docker compose ps
sudo docker compose logs --tail=100 app-permissions-init web ai-worker nginx vault-init vault-auto-unseal
sudo docker compose exec web python manage.py check --deploy
python scripts/verify_supply_chain_pins.py
sudo stat -c '%u:%g %a %n' vault/keys/djopenkb-app-token*.txt
```

For the current direct internal deployment, test the browser-facing address rather than `localhost`:

```bash
curl -k https://<INTERNAL_SERVER_IP>/robots.txt
curl -k -I https://<INTERNAL_SERVER_IP>/login/
```

Do not use `docker compose down -v` for routine troubleshooting or configuration changes. The `-v` option removes named volumes and can destroy persistent state.
