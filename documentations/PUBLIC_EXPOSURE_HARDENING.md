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

Nginx request-body and rate limits are complemented by Django-side character limits. The browser stops normal typing/pasting at the configured boundary, while server validation rejects manually crafted oversized query-string and form values before expensive authentication, search, database, or workflow processing.

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

The article body remains a separately configurable `1,000`–`2,000,000` characters with a default of `100,000`. The OpenKB AI prompt remains configurable from `100`–`10,000` characters with a default of `1,000`. These checks remain server-side even when a client removes `maxlength`, disables JavaScript, or submits requests through an interception tool.

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

- Pending article images are limited per authenticated user across all sessions and browsers: 100 uncommitted images and 100 MB by default, both configurable in Site settings.
- Simultaneous uploads for the same account are serialised before quota calculation.
- Article titles have a unique normalised database key, closing concurrent duplicate-title races.
- Bulk ZIP manifests reject unknown/duplicate fields, invalid types/lengths, unsupported workflow combinations, and transient deletion-queue states before article creation.
- Imported records pass model validation and database uniqueness inside a transaction.

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

## 9. OpenKB Retrieved-Content and Distributed-Lock Hardening

OpenKB Q&A treats article-derived text and image captions as untrusted data. Files are restricted by approved directory, extension, and size; page requests are bounded; a wiki-local instruction file cannot replace the package-owned trusted schema; and model output is never used to make authentication or visibility decisions.

AI concurrency and job-update locks use Redis atomic acquisition and owner-checked Lua release. When production Redis is configured but unavailable, acquisition fails closed rather than falling back to a process-local lock that another worker cannot see.

## 10. Supply-Chain Baseline

Direct Python and OpenKB dependencies, the OpenKB build backend, Python build tools, and production container tags are explicitly pinned. The final runtime image installs from the builder wheelhouse with `--no-index`. Verify the repository guard with:

```bash
python scripts/verify_supply_chain_pins.py
```

This guard complements, but does not replace, CVE scanning, a hash-locked transitive dependency set, SBOM review, and immutable image digests.

## 11. Required verification after an update

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
