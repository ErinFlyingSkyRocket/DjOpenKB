# LDAP / LDAPS Setup for DjOpenKB

DjOpenKB supports Active Directory login through LDAP or LDAPS.

For security, use **LDAPS**:

```text
ldaps://<domain-controller-hostname>:636
```

Plain LDAP on port `389` should only be used temporarily for testing.

---

## 1. Required AD Information

Prepare these values from the Windows Server AD lab:

```text
AD domain:              openkb.local
NetBIOS domain:         OPENKB
Domain Controller host: WIN-VVCA4BIOSK7.openkb.local
Domain Controller IP:   192.168.81.128
Service account:        svc_djopenkb@openkb.local
Search base:            DC=openkb,DC=local
```

The service account should be a low-privilege AD account used only for LDAP bind/search. It should not be a Domain Admin or Enterprise Admin.

---

## 2. `.env` LDAPS Configuration

Use this format:

```env
LDAP_ENABLED=true
LDAP_PLACEHOLDER_ENABLED=false
LDAP_PLACEHOLDER_AUTO_CREATE_USERS=false

LDAP_DC_IP=192.168.81.128
LDAP_SERVER_URI=ldaps://WIN-VVCA4BIOSK7.openkb.local:636
LDAP_START_TLS=false
LDAP_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/ad-ca.crt
LDAP_TLS_REQUIRE_CERT=demand
LDAP_ALLOW_INSECURE=false

LDAP_AD_DOMAIN=openkb.local
LDAP_NETBIOS_DOMAIN=OPENKB
LDAP_ALLOWED_EMAIL_DOMAINS=openkb.local

LDAP_USER_SEARCH_BASE=DC=openkb,DC=local
LDAP_USER_FILTER=(|(userPrincipalName=%(user)s)(sAMAccountName=%(user)s)(mail=%(user)s))
LDAP_BIND_DN=svc_djopenkb@openkb.local
```

Store the service account password in Vault, not `.env`:

```env
LDAP_BIND_PASSWORD="service-account-password"
```

---

## 3. CA Certificate File

Export the AD CS Root CA certificate from Windows Server as:

```text
Base-64 encoded X.509 (.CER)
```

Rename it to:

```text
ad-ca.crt
```

Place it here:

```text
ldap-certs/ad-ca.crt
```

Docker Compose mounts it into the web container as:

```text
/etc/ssl/certs/djopenkb-ldap/ad-ca.crt
```

---

## 4. Docker Compose Requirements

The `web` service should include the CA cert mount:

```yaml
volumes:
  - ./ldap-certs:/etc/ssl/certs/djopenkb-ldap:ro
```

If Docker cannot resolve the AD hostname, use `extra_hosts`:

```yaml
extra_hosts:
  - "WIN-VVCA4BIOSK7.openkb.local:${LDAP_DC_IP}"
```

The `web` service should pass these LDAP variables:

```yaml
environment:
  LDAP_SERVER_URI: ${LDAP_SERVER_URI:-}
  LDAP_START_TLS: ${LDAP_START_TLS:-false}
  LDAP_CA_CERT_FILE: ${LDAP_CA_CERT_FILE:-}
  LDAP_TLS_REQUIRE_CERT: ${LDAP_TLS_REQUIRE_CERT:-demand}
  LDAP_ALLOW_INSECURE: ${LDAP_ALLOW_INSECURE:-false}
```

---

## 5. Test LDAPS from Docker

Start the stack:

```bash
docker compose up -d --build
```

Run:

```bash
docker compose exec web sh scripts/test_ldaps.sh
```

Expected result:

```text
Resolving hostname...
Opening TLS connection...
TLS handshake OK
TLS version: TLSv1.3
LDAPS DNS + TLS certificate validation looks good.
```

This confirms:

```text
1. Docker can resolve the Domain Controller hostname.
2. Port 636 is reachable.
3. TLS handshake succeeds.
4. The Domain Controller certificate is trusted by the mounted CA certificate.
```

---

## 6. Test Login

After LDAPS passes, test the website login with a domain user.

Accepted formats:

```text
alice
alice@openkb.local
OPENKB\alice
```

All formats should map to one Django account:

```text
alice
```

This avoids duplicate Django accounts for the same AD user.

---

## 7. Common Errors

### `FileNotFoundError: /etc/ssl/certs/djopenkb-ldap/ad-ca.crt`

The CA file is missing or not mounted.

Check host:

```bash
ls -la ldap-certs
```

Check container:

```bash
docker compose exec web ls -l /etc/ssl/certs/djopenkb-ldap/
```

### Hostname resolves incorrectly or not at all

Check:

```bash
docker compose exec web python -c "import socket; print(socket.getaddrinfo('WIN-VVCA4BIOSK7.openkb.local', 636))"
```

If it fails, set `LDAP_DC_IP` in `.env` and use the Compose `extra_hosts` entry.

### Certificate validation fails

Common causes:

```text
Wrong CA certificate exported
Certificate file is DER instead of Base-64 PEM format
LDAP_SERVER_URI uses IP address instead of certificate hostname
Domain Controller certificate does not contain the hostname
```

Use hostname format:

```env
LDAP_SERVER_URI=ldaps://WIN-VVCA4BIOSK7.openkb.local:636
```
