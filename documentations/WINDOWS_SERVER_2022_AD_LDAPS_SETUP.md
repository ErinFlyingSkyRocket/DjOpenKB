# Windows Server 2022 AD / LDAPS Testing Setup

This guide explains how to prepare a Windows Server 2022 VM to simulate Active Directory for DjOpenKB testing.

The expected lab result is:

```text
Windows Server 2022 Domain Controller
Domain: openkb.local
LDAPS: port 636
CA: AD CS Enterprise Root CA
Django service account: svc_djopenkb
Django login over LDAPS
```

---

## 1. Suggested VM Network

Use a host-only or NAT network where the Windows Server VM and the Docker host can reach each other.

Example:

```text
Windows Server IP: <DOMAIN_CONTROLLER_IP>
Domain:            openkb.local
Hostname:          WIN-VVCA4BIOSK7
FQDN:              WIN-VVCA4BIOSK7.openkb.local
```

Confirm IP:

```powershell
ipconfig
```

---

## 2. Install Active Directory Domain Services

On Windows Server:

```text
Server Manager
→ Manage
→ Add Roles and Features
→ Role-based or feature-based installation
→ Select current server
→ Server Roles
→ Active Directory Domain Services
→ Add Features
→ Next until Install
```

After installation, promote the server:

```text
Server Manager
→ Notification flag
→ Promote this server to a domain controller
→ Add a new forest
→ Root domain name: openkb.local
→ Set DSRM password
→ Next until Install
→ Server restarts
```

After restart, log in as:

```text
OPENKB\Administrator
```

or:

```text
Administrator@openkb.local
```

---

## 3. Create Test Users and Service Account

Open:

```text
Server Manager → Tools → Active Directory Users and Computers
```

Create users such as:

```text
alice
bob
```

Create a service account, preferably under a Service Accounts OU:

```text
svc_djopenkb
```

Recommended service account settings for the lab:

```text
User logon name: svc_djopenkb@openkb.local
Password: strong password stored in Vault
Password never expires: acceptable for lab, but document it
Account is not Domain Admin
Account is not Enterprise Admin
```

The service account only needs to bind and search users. It should remain low privilege.

---

## 4. Install Active Directory Certificate Services

LDAPS requires the Domain Controller to have a server authentication certificate.

Use AD CS to issue/trust certificates in the lab.

Install role:

```text
Server Manager
→ Manage
→ Add Roles and Features
→ Role-based or feature-based installation
→ Select current server
→ Server Roles
→ Active Directory Certificate Services
→ Add Features
→ Select Certification Authority
→ Install
```

Configure AD CS:

```text
Server Manager notification flag
→ Configure Active Directory Certificate Services
→ Credentials: use OPENKB\Administrator or an account with Enterprise Admin rights
→ Role Services: Certification Authority
→ Setup Type: Enterprise CA
→ CA Type: Root CA
→ Private Key: Create a new private key
→ Cryptography: RSA, SHA256
→ CA Name: keep default or use OPENKB-ROOT-CA
→ Validity Period: lab value is fine
→ Certificate Database: keep default
→ Configure
```

If **Enterprise CA** is greyed out, you are probably logged in as an account without enough rights. Use `OPENKB\Administrator` for this setup step. Do not permanently make `svc_djopenkb` an admin account.

---

## 5. Confirm Domain Controller Certificate

Open:

```text
Start → Run → certlm.msc
```

Check:

```text
Certificates - Local Computer
→ Personal
→ Certificates
```

There should be a certificate for the Domain Controller. It should match the hostname used by Django, for example:

```text
WIN-VVCA4BIOSK7.openkb.local
```

It should support server authentication.

---

## 6. Test LDAPS on Windows Server

PowerShell test:

```powershell
Test-NetConnection WIN-VVCA4BIOSK7.openkb.local -Port 636
```

Expected:

```text
TcpTestSucceeded : True
```

You can also test locally:

```powershell
Test-NetConnection localhost -Port 636
```

Optional GUI test:

```text
Run → ldp.exe
Connection → Connect
Server: WIN-VVCA4BIOSK7.openkb.local
Port: 636
SSL: checked
```

If it connects, LDAPS is working on Windows.

---

## 7. Export the CA Certificate for Django

On Windows Server, export the CA certificate that signed the Domain Controller LDAPS certificate.

Recommended method:

```text
Start → Run → certlm.msc
Trusted Root Certification Authorities → Certificates
Right-click the AD CS Root CA certificate
All Tasks → Export
```

Export format:

```text
Base-64 encoded X.509 (.CER)
```

Save it as:

```text
ad-ca.cer
```

Copy it to the Linux server project folder as:

```text
ldap-certs/ad-ca.crt
```

On the Linux server, confirm that the file is readable PEM text:

```bash
head -n 5 ldap-certs/ad-ca.crt
```

Expected:

```text
-----BEGIN CERTIFICATE-----
...
```

If the file shows unreadable binary characters, it was exported as DER/binary. Convert it on the Linux server:

```bash
openssl x509 -inform DER -in ldap-certs/ad-ca.crt -out ldap-certs/ad-ca.pem
mv ldap-certs/ad-ca.crt ldap-certs/ad-ca.der.bak
mv ldap-certs/ad-ca.pem ldap-certs/ad-ca.crt
```

Then verify:

```bash
openssl x509 -in ldap-certs/ad-ca.crt -noout -subject -issuer -dates
```

If Windows uses an Issuing/Intermediate CA, export both the Issuing CA and Root CA, convert each to PEM if needed, then combine them:

```bash
cat issuing-ca.crt root-ca.crt > ldap-certs/ad-ca.crt
```

The final `ldap-certs/ad-ca.crt` should contain one or more PEM certificate blocks.
## 8. Configure DjOpenKB `.env`

Use the Windows Server AD values in `.env`.

Example:

```env
LDAP_ENABLED=true
LDAP_PLACEHOLDER_ENABLED=false
LDAP_PLACEHOLDER_AUTO_CREATE_USERS=false

LDAP_SERVER_URI=ldaps://WIN-VVCA4BIOSK7.openkb.local:636
LDAP_START_TLS=false
LDAP_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/ad-ca.crt
LDAP_TLS_REQUIRE_CERT=demand
LDAP_ALLOW_INSECURE=false

LDAP_AD_DOMAIN=openkb.local
LDAP_NETBIOS_DOMAIN=OPENKB
LDAP_ALLOWED_EMAIL_DOMAINS=openkb.local

LDAP_USER_SEARCH_BASE=DC=openkb,DC=local
LDAP_USER_FILTER=(|(sAMAccountName=%(user)s)(userPrincipalName=%(user)s)(userPrincipalName=%(user)s@openkb.local)(mail=%(user)s)(mail=%(user)s@openkb.local))

# Every valid AD user returned by this search can sign in.

LDAP_BIND_DN=svc_djopenkb@openkb.local
LDAP_EXTRA_HOSTNAME=WIN-VVCA4BIOSK7.openkb.local
LDAP_EXTRA_SHORT_HOSTNAME=WIN-VVCA4BIOSK7
LDAP_DC_IP=<DOMAIN_CONTROLLER_IP>
```

If the organisation has a public email domain that differs from the AD UPN suffix, include both in `LDAP_ALLOWED_EMAIL_DOMAINS` and in the search filter. Users may still be told to log in with their short username, while Django checks the actual AD UPN/mail values internally.

Confirm that `LDAP_USER_SEARCH_BASE` and `LDAP_USER_FILTER` match the intended AD scope. A valid AD account returned by this search can sign in. Keep the LDAP bind account low-privilege and read-only; it is used only to locate and verify users.
## 9. Test from Docker

Start DjOpenKB:

```bash
docker compose up -d --build
```

Run:

```bash
docker compose exec web sh scripts/test_ldaps.sh
```

Good result:

```text
TLS handshake OK
TLS version: TLSv1.3
Peer subject: WIN-VVCA4BIOSK7.openkb.local
LDAPS DNS + TLS certificate validation looks good.
```

Then test website login with:

```text
alice
alice@openkb.local
OPENKB\alice
```

All should map to the same Django account.

---

## 10. Troubleshooting

### `dc01.openkb.local` cannot resolve

Use your actual server hostname. Check it with:

```powershell
hostname
```

Then use:

```text
<hostname>.openkb.local
```

Example:

```text
WIN-VVCA4BIOSK7.openkb.local
```

### Port 636 fails

Check that AD CS is installed and the Domain Controller has a valid certificate. Restart the server after AD CS setup if needed.

### Certificate validation fails in Docker

Confirm the CA cert exists inside the container:

```bash
docker compose exec web ls -l /etc/ssl/certs/djopenkb-ldap/ad-ca.crt
docker compose exec web head -n 5 /etc/ssl/certs/djopenkb-ldap/ad-ca.crt
```

Expected first line:

```text
-----BEGIN CERTIFICATE-----
```

If the file is binary/unreadable, convert it on the Linux server:

```bash
openssl x509 -inform DER -in ldap-certs/ad-ca.crt -out ldap-certs/ad-ca.pem
mv ldap-certs/ad-ca.crt ldap-certs/ad-ca.der.bak
mv ldap-certs/ad-ca.pem ldap-certs/ad-ca.crt
```

Then test:

```bash
docker compose exec web openssl s_client \
  -connect WIN-VVCA4BIOSK7.openkb.local:636 \
  -servername WIN-VVCA4BIOSK7.openkb.local \
  -CAfile /etc/ssl/certs/djopenkb-ldap/ad-ca.crt
```

Expected:

```text
Verify return code: 0 (ok)
```

If it says `unable to get local issuer certificate`, the certificate is readable but the chain is incomplete. Export the Root CA and any Issuing/Intermediate CA from Windows Server and combine them into `ldap-certs/ad-ca.crt`.

Do not leave production/testing documentation with:

```env
LDAP_TLS_REQUIRE_CERT=never
LDAP_ALLOW_INSECURE=true
```

Use those values only for temporary troubleshooting.
### Enterprise CA is unavailable

Log in as:

```text
OPENKB\Administrator
```

Use the service account only for LDAP bind/search, not AD CS installation.

---

## 11. Expected DjOpenKB Behaviour After LDAPS Login

After LDAPS is working and a domain user signs in successfully, DjOpenKB should create or update the corresponding Django-side account and assign the default role:

```text
New AD / LDAP user → Regular User group
```

The user should then complete MFA setup or verification if MFA is required. After login and MFA completion, the user can access the main site at `/home/` and view published articles.

Protected pages should not be available to anonymous users:

```text
/              → login page
/home/         → requires login
/admin/login/  → hidden / 404
/admin/        → requires login, admin/staff role, and allowed admin network/VPN
```

To test role changes, sign in as a Django admin and use Django Admin → Groups. The current standard groups are:

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

Move the AD test user between suitable groups and confirm the expected website permissions. Manager precedence is enforced within each visibility scope, while public and internal scopes remain independent. Direct user permission checkboxes can be used for one-off add-on permissions.
