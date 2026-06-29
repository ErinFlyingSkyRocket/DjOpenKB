# Public Exposure and Direct-IP Hardening Notes

This document records the security hardening applied to the firewall-published Knowledge Repository deployment. It covers the current internal direct-IP development phase and the later public-DNS phase. A public DNS name is not required to keep developing safely on a controlled internal network.

## 1. Current direct internal-IP development

While users reach the service directly on the Linux host listener, the browser URL includes port `8080`. For the current development VM, an example is:

```text
https://<INTERNAL_SERVER_IP>:8080
```

Use the exact reachable server IP in `.env`:

```env
DJANGO_ALLOWED_HOSTS=<INTERNAL_SERVER_IP>
DJANGO_CSRF_TRUSTED_ORIGINS=https://<INTERNAL_SERVER_IP>:8080
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

When a perimeter firewall publishes standard HTTPS on public TCP `443` and translates it to this host’s port `8080`, browsers no longer see `:8080`. Use the public IP or final DNS hostname exactly as seen by the browser:

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

Nginx is the only service published to the network on host port `8080`. PostgreSQL, Redis, Gunicorn, and Docker are not published. Vault is bound only to host loopback (`127.0.0.1:8200`) for local administrator access and is not externally reachable through the network firewall.

The reverse proxy now applies these controls before traffic reaches Django or Active Directory:

| Control | Behaviour |
|---|---|
| POST-only request rate limits | Login, normal MFA, Admin MFA, AI question submission, article-image upload, and admin bulk import are rate-limited per source IP. Normal GET page loads and browser refreshes are not counted. Exceeded limits return HTTP `429`. |
| Connection limits | Per-IP concurrent connection caps limit one client from consuming all Nginx workers. |
| Request-body limit | Ordinary requests are limited to `3 MB`. |
| Bulk-import exception | The authorised admin bulk-import endpoint alone permits up to `100 MB`, matching the application ZIP validation limit. |
| Timeouts | Header, body, proxy-connect, proxy-read, proxy-send, and keepalive timeouts prevent slow or stuck connections from holding resources indefinitely. |
| Admin outer gate | The Nginx administrator CIDR/VPN allowlist returns `404` before unauthorised `/admin/` requests reach Django. Django enforces a second allowlist and its separate Admin MFA gate. |

The rate limits use the TCP peer address seen by Nginx. With direct firewall NAT, this is normally the browser IP. If a CDN or Layer-7 reverse proxy is introduced later, configure `real_ip_header` and `set_real_ip_from` for the known proxy range before relying on IP-based rate limits or audit records. Never trust browser-supplied `X-Forwarded-For` directly.

Nginx uses a read-only root filesystem. Temporary paths are intentionally under the writable `/tmp` `tmpfs`:

```nginx
client_body_temp_path /tmp/client_temp 1 2;
proxy_temp_path /tmp/proxy_temp 1 2;
fastcgi_temp_path /tmp/fastcgi_temp 1 2;
uwsgi_temp_path /tmp/uwsgi_temp 1 2;
scgi_temp_path /tmp/scgi_temp 1 2;
```

The image entrypoint may log that it cannot change the unused default Nginx configuration because the root filesystem is read-only. That informational message is harmless when Nginx subsequently starts without an `[emerg]` error. A `mkdir()` error for one of the Nginx temporary paths is not harmless and requires restoring the paths above.

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

## 5. Vault application-token permissions

The Vault initialisation and auto-unseal scripts create `vault/keys/djopenkb-app-token.txt` with:

```text
owner/group: 0:10001
mode: 0440
```

This permits the unprivileged Django, Celery, and scheduler containers to read the bind-mounted token while keeping it unreadable to unrelated users. Verify it after a first deployment or a Vault recovery:

```bash
cd /opt/DjOpenKB
sudo stat -c '%u:%g %a %n' vault/keys/djopenkb-app-token.txt
```

Expected output includes:

```text
0:10001 440 vault/keys/djopenkb-app-token.txt
```

Do not loosen this file to world-readable mode. If `web` or `ai-worker` reports `Permission denied` for `/run/vault-keys/djopenkb-app-token.txt`, restore these exact owner/group and mode values, then recreate the affected services without deleting volumes:

```bash
sudo chown 0:10001 vault/keys/djopenkb-app-token.txt
sudo chmod 0440 vault/keys/djopenkb-app-token.txt
sudo docker compose up -d --force-recreate web ai-worker cleanup-scheduler
```

## 6. Fixed eight-hour session policy

All normal authenticated sessions and pending-MFA sessions have a fixed maximum lifetime of eight hours by default. The session deadline starts at the original sign-in attempt. Page activity, refreshes, and cookie renewal do not extend that original deadline. When the deadline is reached, the next request clears the session and returns the user to login.

The runtime setting is available in Django Admin:

```text
Site settings → Authentication and session settings → User session timeout (hours)
```

Allowed values are `1` to `168` hours. The `.env` value `DJANGO_SESSION_TIMEOUT_HOURS=8` is the safe startup fallback before the database setting is available.

## 7. Active Directory scope restriction

When `LDAP_ENABLED=true`, `LDAP_REQUIRED_GROUP_DN` and `LDAP_GROUP_SEARCH_BASE` are required. Django fails closed if the approved group DN is absent or incorrect.

```env
LDAP_GROUP_SEARCH_BASE=DC=company,DC=local
LDAP_REQUIRED_GROUP_DN=CN=KB-Users,OU=Security Groups,DC=company,DC=local
```

Use a dedicated normal AD security group such as `KB-Users`. Nested group membership is supported. The LDAP bind account must only be able to search users/group membership; it must not be a Domain Admin, local administrator, or interactive-login account. Do not allow privileged AD accounts to use the site.

From the web VM, allow only the intended Domain Controller IPs on LDAPS TCP `636`. Block SMB, RDP, WinRM, Kerberos, RPC, and broad internal-subnet access from this web server.

## 8. CSP status

The Content Security Policy still includes `'unsafe-inline'` for scripts and styles because existing templates contain inline JavaScript, inline styles, and a small number of inline event handlers. It remains an explicit compatibility trade-off. Removing it now would break login, article editing, and admin workflows. The future remediation is a tested template refactor to static assets and CSP nonces/hashes.

## 9. Required verification after an update

```bash
cd /opt/DjOpenKB
sudo docker compose config >/dev/null && echo "Compose configuration is valid"
sudo docker compose up -d --build
sudo docker compose ps
sudo docker compose logs --tail=100 app-permissions-init web ai-worker nginx
sudo docker compose exec web python manage.py check --deploy
```

For the current direct internal deployment, test the browser-facing address rather than `localhost`:

```bash
curl -k https://<INTERNAL_SERVER_IP>:8080/robots.txt
curl -k -I https://<INTERNAL_SERVER_IP>:8080/login/
```

Do not use `docker compose down -v` for routine troubleshooting or configuration changes. The `-v` option removes named volumes and can destroy persistent state.
