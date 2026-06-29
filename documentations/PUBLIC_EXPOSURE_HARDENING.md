# Public Exposure Hardening Notes

This document records the configuration introduced for a firewall-published Knowledge Repository deployment. It applies whether a public DNS name exists now or later.

## Current temporary IP-based configuration

Before DNS is available, keep this in `.env` using the browser-facing public IP:

```env
DJANGO_ALLOWED_HOSTS=<BROWSER_FACING_PUBLIC_IP>
DJANGO_CSRF_TRUSTED_ORIGINS=https://<BROWSER_FACING_PUBLIC_IP>
DJANGO_SESSION_TIMEOUT_HOURS=8
```

The Nginx configuration uses `server_name _;` temporarily. The trusted CSRF origin must match the URL users actually open in their browser; do not add `:8080` when the firewall exposes standard external HTTPS on port 443 and merely translates it internally. Django is still responsible for strict host-header validation through `DJANGO_ALLOWED_HOSTS`.

## Later public DNS configuration

When the approved hostname exists and the perimeter firewall publishes TCP 443, change only the public host values:

```env
DJANGO_ALLOWED_HOSTS=kb.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://kb.example.com
```

Then replace `server_name _;` with `server_name kb.example.com;`, install a certificate for that hostname, and add a default Nginx server that rejects unknown `Host` headers.

## AD scope restriction

Create a dedicated read-only access group such as `KB-Users`. It must be a normal AD security group, not a privileged AD administration group.

```env
LDAP_GROUP_SEARCH_BASE=DC=company,DC=local
LDAP_REQUIRED_GROUP_DN=CN=KB-Users,OU=Security Groups,DC=company,DC=local
```

The application refuses to start with `LDAP_ENABLED=true` when the required group DN is absent. The LDAP bind account must be able to read user and approved-group membership but must not have interactive or administrator privileges.

## Firewall boundaries

At the perimeter, publish only public TCP 443. It may translate to host port 8080 on this server. Do not expose PostgreSQL (5432), Redis (6379), Gunicorn (8000), Vault (8200), Docker, LDAP, SMB, RDP, Kerberos, RPC, or WinRM.

From the web VM to Active Directory, allow only the named Domain Controller IPs and TCP 636 for LDAPS. Block broad access from this VM to internal network services.

## Important CSP note

The project still requires CSP `unsafe-inline` because existing templates contain inline JavaScript, styles, and a few inline event handlers. It has not been removed in this hardening patch because doing so would break authenticated workflows. A separate template refactor is required before moving to a nonce/hash-based CSP.
