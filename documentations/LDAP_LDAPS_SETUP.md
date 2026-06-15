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

LDAP_DC_IP=<AD_DC_IP>
LDAP_SERVER_URI=ldaps://<DOMAIN_CONTROLLER_FQDN>:636
LDAP_START_TLS=false
LDAP_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/ad-ca.crt
LDAP_TLS_REQUIRE_CERT=demand
LDAP_ALLOW_INSECURE=false

# Example AD domain values. Replace openkb.local/OPENKB with the real AD values.
LDAP_AD_DOMAIN=openkb.local
LDAP_NETBIOS_DOMAIN=OPENKB
LDAP_ALLOWED_EMAIL_DOMAINS=openkb.local,company.com

# For openkb.local, use DC=openkb,DC=local.
# For example.corp.local, use DC=example,DC=corp,DC=local.
LDAP_USER_SEARCH_BASE=DC=openkb,DC=local
LDAP_USER_FILTER=(|(sAMAccountName=%(user)s)(userPrincipalName=%(user)s)(userPrincipalName=%(user)s@openkb.local)(mail=%(user)s)(mail=%(user)s@openkb.local)(mail=%(user)s@company.com)(userPrincipalName=%(user)s@company.com))
LDAP_BIND_DN=svc_djopenkb@openkb.local
```

Notes:

```text
- `openkb.local` is only a documentation example. Replace it with the real AD DNS domain.
- Convert the AD DNS domain into the search base by splitting each part into `DC=` values. Example: `openkb.local` becomes `DC=openkb,DC=local`.
- `LDAP_SERVER_URI` should use the Domain Controller hostname/FQDN, not the IP address.
- `LDAP_EXTRA_HOSTNAME`, `LDAP_EXTRA_SHORT_HOSTNAME`, and `LDAP_DC_IP` are optional and only help Docker resolve the hostname when DNS is unavailable.
- `LDAP_BIND_DN` must be the real AD service account login format that can bind/search, for example `svc_djopenkb@openkb.local`.
- The public email domain and the AD UPN suffix may be different. Include both in LDAP_ALLOWED_EMAIL_DOMAINS if both are accepted for login.
```

Store the service account password in Vault, not `.env`:

```env
LDAP_BIND_PASSWORD=service-account-password
```

Use plain `KEY=value` format in `vault/bootstrap/djopenkb.env` where possible:

```env
LDAP_BIND_PASSWORD=P@ssw0rd-example
```

Avoid spaces around `=`. For the simplest deployment experience, use service-account passwords made from letters, numbers, `@`, `.`, `_`, and `-`. If the password contains shell-breaking characters, test the Vault bootstrap logs carefully and rotate to a simpler service-account password if seeding fails.
## 3. CA Certificate File

Export the AD CS Root CA certificate from Windows Server. Recommended export format:

```text
Base-64 encoded X.509 (.CER)
```

Rename/copy it to:

```text
ldap-certs/ad-ca.crt
```

Docker Compose mounts it into the web container as:

```text
/etc/ssl/certs/djopenkb-ldap/ad-ca.crt
```

On the Linux server, check whether the certificate is readable PEM text:

```bash
head -n 5 ldap-certs/ad-ca.crt
```

Expected:

```text
-----BEGIN CERTIFICATE-----
...
```

If the file displays unreadable binary characters, it is DER/binary format. Convert it on the Linux server:

```bash
openssl x509 -inform DER -in ldap-certs/ad-ca.crt -out ldap-certs/ad-ca.pem
mv ldap-certs/ad-ca.crt ldap-certs/ad-ca.der.bak
mv ldap-certs/ad-ca.pem ldap-certs/ad-ca.crt
```

Verify the converted certificate:

```bash
openssl x509 -in ldap-certs/ad-ca.crt -noout -subject -issuer -dates
```

If the Domain Controller certificate is issued by an intermediate CA, place the full CA chain in the same PEM file:

```bash
cat issuing-ca.crt root-ca.crt > ldap-certs/ad-ca.crt
```

Do not use the Domain Controller server certificate as the CA file unless it is self-signed. The CA file should normally contain the Root CA and, if applicable, the Issuing/Intermediate CA.
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
Certificate file is DER/binary instead of Base-64 PEM format
The CA chain is incomplete
LDAP_SERVER_URI uses an IP address instead of the certificate hostname
The Domain Controller certificate does not contain the hostname used by LDAP_SERVER_URI
```

Check the mounted file:

```bash
docker compose exec web head -n 5 /etc/ssl/certs/djopenkb-ldap/ad-ca.crt
docker compose exec web openssl x509 -in /etc/ssl/certs/djopenkb-ldap/ad-ca.crt -noout -subject -issuer -dates
```

A readable PEM file starts with:

```text
-----BEGIN CERTIFICATE-----
```

If the file is DER/binary, convert it on the Linux server:

```bash
openssl x509 -inform DER -in ldap-certs/ad-ca.crt -out ldap-certs/ad-ca.pem
mv ldap-certs/ad-ca.crt ldap-certs/ad-ca.der.bak
mv ldap-certs/ad-ca.pem ldap-certs/ad-ca.crt
```

Then test the LDAPS handshake:

```bash
docker compose exec web openssl s_client \
  -connect <DOMAIN_CONTROLLER_FQDN>:636 \
  -servername <DOMAIN_CONTROLLER_FQDN> \
  -CAfile /etc/ssl/certs/djopenkb-ldap/ad-ca.crt
```

Expected:

```text
Verify return code: 0 (ok)
```

If it still says `unable to get local issuer certificate`, export the Root CA and any Issuing/Intermediate CA from Windows Server and combine them into `ldap-certs/ad-ca.crt`.

---

## 7. DjOpenKB Role and MFA Behaviour After AD Login

When a valid AD user signs in for the first time, DjOpenKB creates or updates the Django-side user record and assigns the default website role.

Current default behaviour:

```text
New AD / LDAP user → Regular User group
Regular User       → can view published articles and vote after login
```

Admins can later move the user into `Article Writer`, `Article Manager`, or `Admin Users` from Django Admin → Groups. The Groups page provides a searchable left/right selector for adding and removing users.

The Users admin page also provides direct DjOpenKB permission checkboxes for one-off exceptions. These are add-on permissions only and do not remove permissions inherited from groups.

MFA is still required after successful AD password authentication where MFA is enabled. AD passwords remain managed by Active Directory, so users cannot change their AD password from the DjOpenKB profile page.

Because the current site is login-only, anonymous users should not be able to browse articles or use the AI chatbot. Protected paths return 404 before normal article/admin content is shown.
