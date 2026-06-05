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
Windows Server IP: 192.168.81.128
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

Open:

```text
certlm.msc
```

Find the Root CA certificate under:

```text
Trusted Root Certification Authorities
→ Certificates
```

Export:

```text
Right-click CA certificate
→ All Tasks
→ Export
→ No, do not export private key
→ Base-64 encoded X.509 (.CER)
→ Save as ad-ca.cer
```

Copy it to the DjOpenKB project and rename:

```text
ldap-certs/ad-ca.crt
```

Do not export the private key.

---

## 8. Configure DjOpenKB `.env`

Use the Domain Controller hostname:

```env
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
LDAP_BIND_DN=svc_djopenkb@openkb.local
```

Store the service account password in Vault:

```env
LDAP_BIND_PASSWORD="service-account-password"
```

---

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

Check:

```text
ldap-certs/ad-ca.crt exists
Docker Compose mounts ldap-certs correctly
LDAP_SERVER_URI uses the hostname from the certificate
CA certificate is Base-64 encoded X.509
```

### Enterprise CA is unavailable

Log in as:

```text
OPENKB\Administrator
```

Use the service account only for LDAP bind/search, not AD CS installation.
