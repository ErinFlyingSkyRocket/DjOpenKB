# DjOpenKB — Exchange SMTP Relay Readiness, TLS and Testing Guide

**Purpose:** Prepare an on-premises Microsoft Exchange server to act as DjOpenKB's authenticated SMTP submission endpoint for article-review notifications.

**Target flow:**

```text
DjOpenKB web service
  → SMTP AUTH + STARTTLS on TCP 587
  → Exchange Client Frontend Receive connector
  → Exchange transport and recipient mailboxes
```

This guide is deliberately limited to the Exchange relay integration. It does **not** configure legacy IIS SMTP on the Active Directory/LDAPS server.

---

## 1. TLS certificate clarification

A different Exchange server IP address does **not**, by itself, require a new certificate configuration in DjOpenKB. DjOpenKB connects by a **DNS hostname**, not an IP address. The certificate presented by Exchange must contain that DNS hostname in its Subject Alternative Name (SAN) or Subject/CN, have a private key on the Exchange server, and be enabled for Exchange SMTP.

DjOpenKB uses Django's standard SMTP backend and the web container's normal operating-system trust store for SMTP TLS.

| Situation | What is needed on Exchange | What DjOpenKB needs |
|---|---|---|
| Existing Exchange certificate already contains `<EXCHANGE_SMTP_FQDN>`, is valid, has a private key, and is SMTP-enabled | Reuse the existing Exchange certificate | Use the hostname and pass the SMTP TLS test with the container standard trust store. |
| Existing Exchange certificate does not contain `<EXCHANGE_SMTP_FQDN>` | Request/install a **new Exchange server certificate** containing the FQDN | Use the hostname and pass the SMTP TLS test with the container standard trust store. |
| Exchange certificate chains to a private CA not trusted by the container | Use a valid certificate with the correct FQDN | The platform team must make the issuing CA available through the container image's standard trust store; do not disable validation. |

### Never copy these items to DjOpenKB

- The Exchange certificate private key.
- A `.pfx` / `.p12` bundle containing a private key.
- Service-account passwords in `.env`, documentation, Git, or chat.

Keep TLS certificate and hostname validation enabled for SMTP authentication.

---

## 2. Decide the SMTP hostname first

Choose one internal DNS name for DjOpenKB to use, for example:

```text
<EXCHANGE_SMTP_FQDN>
```

Requirements:

1. It resolves from the DjOpenKB Linux host to the Exchange server's internal address.
2. The name appears in the Exchange SMTP certificate SAN or Subject/CN.
3. DjOpenKB uses this exact name in `SMTP_RELAY_HOST`.
4. Do **not** configure an IP address in `SMTP_RELAY_HOST`.
5. Do **not** expose TCP 587 to the public Internet for this internal application.

On a DNS server or authorised administration workstation, create/confirm an internal DNS record for `<EXCHANGE_SMTP_FQDN>` that points to the Exchange server. Do not continue until this hostname resolves correctly from the DjOpenKB server.

---

## 3. Pre-flight information to collect on Exchange

Open the **Exchange Management Shell** as an Exchange administrator.

### 3.1 Inspect the current Client Frontend connector

```powershell
Get-ReceiveConnector -Identity "Client Frontend*" |
    Format-List Identity,Bindings,RemoteIPRanges,Fqdn,AuthMechanism,PermissionGroups,TlsCertificateName,ProtocolLoggingLevel
```

For this use case, the relevant connector should be the default **Client Frontend** Receive connector. Exchange documents the Client usage type as the authenticated SMTP endpoint on TCP 587, with TLS, Basic authentication, Basic authentication only after TLS, and Exchange user permissions. Do not create another connector or change existing connector bindings unless the Exchange administrator confirms there is no overlap with existing connectors.

Expected high-level properties:

```text
Bindings:          <EXCHANGE_LOCAL_IP>:587 or 0.0.0.0:587
Fqdn:              <EXCHANGE_SMTP_FQDN>
AuthMechanism:     TLS, BasicAuth, BasicAuthRequireTLS, Integrated
PermissionGroups:  ExchangeUsers
```

### 3.2 Inspect existing certificates

```powershell
Get-ExchangeCertificate |
    Format-List Thumbprint,Subject,CertificateDomains,Services,NotAfter,Status,RootCAType,HasPrivateKey
```

Choose a certificate only when all of the following are true:

- `Status` is valid and `NotAfter` is in the future.
- `HasPrivateKey` is true.
- `CertificateDomains` or `Subject` contains `<EXCHANGE_SMTP_FQDN>`.
- `Services` includes `SMTP`, or the certificate can be safely enabled for SMTP by the Exchange administrator.
- The issuer chains to a CA trusted by the web container's standard operating-system trust store.

### 3.3 Confirm the service account is suitable

The SMTP service account should:

- Be an enabled AD/Exchange user with a mailbox or a mail-enabled identity supported by the Exchange administrator.
- Authenticate using its full UPN: `<SERVICE_ACCOUNT_UPN>`.
- Send from its own mailbox address by default.
- Have **Send As** permission if `SMTP_FROM_EMAIL` will use a different shared mailbox or no-reply address.
- Be dedicated to DjOpenKB in production, rather than shared with LDAPS. Reusing the existing account is acceptable only for this development transition.

---

## 4. Reuse an existing Exchange certificate, if it already matches

If the pre-flight checks show that an existing Exchange certificate meets every requirement, do not request another one.

Set the Client Frontend connector FQDN to the exact hostname that clients will use:

```powershell
Get-ReceiveConnector -Identity "Client Frontend*" |
    Set-ReceiveConnector -Fqdn <EXCHANGE_SMTP_FQDN>
```

Point the connector at the selected certificate:

```powershell
$TLSCert = Get-ExchangeCertificate -Thumbprint <EXCHANGE_CERT_THUMBPRINT>
$TLSCertName = "<I>$($TLSCert.Issuer)<S>$($TLSCert.Subject)"

Get-ReceiveConnector -Identity "Client Frontend*" |
    Set-ReceiveConnector -TlsCertificateName $TLSCertName
```

Verify:

```powershell
Get-ReceiveConnector -Identity "Client Frontend*" |
    Format-List Identity,Fqdn,TlsCertificateName,AuthMechanism,PermissionGroups

Get-ExchangeCertificate -Thumbprint <EXCHANGE_CERT_THUMBPRINT> |
    Format-List Thumbprint,Subject,CertificateDomains,Services,NotAfter,Status,HasPrivateKey
```

Do not run a broad certificate change against unrelated Exchange services without reviewing the current configuration. A certificate assigned to SMTP can affect Exchange's existing SMTP TLS selection.

---

## 5. Request a new Exchange server certificate when needed

Request a new certificate only when no current Exchange certificate contains `<EXCHANGE_SMTP_FQDN>` or when the current certificate is unsuitable.

### 5.1 Certificate requirements

Request from the internal AD Certificate Services CA using an approved server-certificate template with:

```text
Subject/SAN DNS name:  <EXCHANGE_SMTP_FQDN>
Enhanced Key Usage:    Server Authentication
Private key:           created and retained only on Exchange
Key size:              2048 bits or organisation standard
Issuer:                the internal CA trusted by DjOpenKB, where possible
```

Use a DNS name, not an IP address, in the certificate request. A DNS SAN is normally sufficient for this DjOpenKB SMTP endpoint.

### 5.2 Create the request in Exchange Admin Center (where available)

For Exchange versions that support certificate management in EAC:

1. Open **Exchange admin center**.
2. Go to **Servers → Certificates**.
3. Select the Exchange server.
4. Choose **Add** and select **Create a request for a certificate from a certification authority**.
5. Use a friendly name such as:

   ```text
   DjOpenKB SMTP submission TLS
   ```

6. Choose a SAN or single-host request, not a self-signed certificate.
7. Include `<EXCHANGE_SMTP_FQDN>` in the hostname list.
8. Save the certificate request file to an authorised location.
9. Submit the request to the internal CA using the approved server-certificate template.
10. Download the issued certificate and complete/import it back into Exchange.

### 5.3 Create the request using Exchange Management Shell

Use this route when the Exchange version does not offer certificate request management in EAC, or when the Exchange administrator prefers the shell.

```powershell
$csr = New-ExchangeCertificate `
    -GenerateRequest `
    -FriendlyName "DjOpenKB SMTP submission TLS" `
    -SubjectName "CN=<EXCHANGE_SMTP_FQDN>" `
    -DomainName <EXCHANGE_SMTP_FQDN> `
    -KeySize 2048

[System.IO.File]::WriteAllBytes(
    "C:\Temp\DjOpenKB-SMTP-Exchange.req",
    [System.Text.Encoding]::Unicode.GetBytes($csr)
)
```

Submit `C:\Temp\DjOpenKB-SMTP-Exchange.req` to the internal CA. Once it has been issued, import/complete it in Exchange through EAC or the Exchange Management Shell according to the Exchange administrator's standard certificate procedure.

### 5.4 Enable and select the certificate for SMTP

After the certificate is installed and its thumbprint is known:

```powershell
Enable-ExchangeCertificate -Thumbprint <NEW_CERT_THUMBPRINT> -Services SMTP

$TLSCert = Get-ExchangeCertificate -Thumbprint <NEW_CERT_THUMBPRINT>
$TLSCertName = "<I>$($TLSCert.Issuer)<S>$($TLSCert.Subject)"

Get-ReceiveConnector -Identity "Client Frontend*" |
    Set-ReceiveConnector -Fqdn <EXCHANGE_SMTP_FQDN> -TlsCertificateName $TLSCertName
```

When Exchange asks whether to replace an existing SMTP certificate, stop and have the Exchange administrator review the prompt. Do not replace an existing certificate blindly, because that can affect other Exchange SMTP TLS flows.

---

## 6. Confirm authenticated SMTP submission on port 587

The Client Frontend connector should require TLS before Basic authentication and allow authenticated Exchange users.

Inspect it first:

```powershell
Get-ReceiveConnector -Identity "Client Frontend*" |
    Format-List Identity,Bindings,Fqdn,AuthMechanism,PermissionGroups,TlsCertificateName
```

The intended security model is:

```text
TCP port:             587
Encryption:           STARTTLS
Authentication:       SMTP AUTH using the service-account UPN/password
Authentication rule:  Basic authentication offered only after TLS
Permission group:     ExchangeUsers
```

Do not enable anonymous relay or broad relay permissions for DjOpenKB. Do not use an IP-based anonymous relay as a shortcut when the application is configured for SMTP AUTH.

### Network restriction

At the Exchange host firewall and any network firewall, allow inbound TCP 587 only from:

```text
<DJOPENKB_LINUX_HOST_IP>
```

Do not use Docker bridge/container addresses as the source allowlist unless the network team has explicitly confirmed that Exchange sees those addresses. In most deployments, Exchange sees the Linux Docker host address.

---

## 7. SMTP TLS validation in DjOpenKB

The web container uses its normal operating-system trust store for SMTP TLS.

Use the exact Exchange certificate hostname in `SMTP_RELAY_HOST`, then run the TLS test in section 9. If the test reports `CERTIFICATE_VERIFY_FAILED`, correct the Exchange certificate chain or have the platform team add the issuing CA to the container image's standard trust store and rebuild the image. Do **not** disable TLS, hostname validation, or certificate validation.

---

## 8. DjOpenKB configuration changes

Only the SMTP relay endpoint changes. Keep all LDAP settings and the LDAPS CA configuration pointed to the AD/LDAPS server.

In `/opt/DjOpenKB/.env`:

```dotenv
EMAIL_NOTIFICATIONS_ENABLED=true

SMTP_RELAY_HOST=<EXCHANGE_SMTP_FQDN>
SMTP_RELAY_PORT=587
SMTP_RELAY_USE_TLS=true
SMTP_RELAY_USE_SSL=false
SMTP_RELAY_TIMEOUT_SECONDS=10

SMTP_FROM_EMAIL=<SERVICE_ACCOUNT_EMAIL>
SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS=<APPROVED_DOMAIN_1>,<APPROVED_DOMAIN_2>
```

Keep the credentials in Vault only:

```text
SMTP_RELAY_USERNAME
SMTP_RELAY_PASSWORD
```

Do not place these passwords in `.env`, source code, Git, tickets, or documentation.

Apply the configuration:

```bash
cd /opt/DjOpenKB
sudo docker compose up -d --force-recreate --remove-orphans web
```

---

## 9. Test in the correct order

### 9.1 DNS and network reachability from the DjOpenKB host

```bash
getent hosts <EXCHANGE_SMTP_FQDN>
```

The returned address must be the internal Exchange address.

Test TCP reachability without sending email:

```bash
python3 - <<'PY'
import socket
host = "<EXCHANGE_SMTP_FQDN>"
port = 587
with socket.create_connection((host, port), timeout=10) as sock:
    print(f"Connected to {host}:{port}")
    print(sock.recv(1024).decode(errors="replace").strip())
PY
```

### 9.2 Verify STARTTLS and the certificate from the worker container

Run this from `/opt/DjOpenKB`:

```bash
sudo docker compose exec web python - <<'PY'
import os
import smtplib
import ssl

host = os.environ["SMTP_RELAY_HOST"]
port = int(os.environ.get("SMTP_RELAY_PORT", "587"))
context = ssl.create_default_context()

with smtplib.SMTP(host, port, timeout=10) as client:
    client.ehlo()
    print("STARTTLS advertised:", client.has_extn("starttls"))
    if not client.has_extn("starttls"):
        raise SystemExit("Exchange did not advertise STARTTLS")
    client.starttls(context=context)
    client.ehlo()
    print("TLS handshake and hostname validation succeeded")
PY
```

Expected result:

```text
STARTTLS advertised: True
TLS handshake and hostname validation succeeded
```

### 9.3 Send one controlled SMTP test email

Use a test mailbox inside an allowed recipient domain:

```bash
sudo docker compose exec web \
  python manage.py test_smtp_relay <TEST_RECIPIENT_EMAIL>
```

Confirm the message arrives and shows the expected sender address.

### 9.4 Test the business workflow

1. Submit one **Public** article for approval.
2. Confirm one Bcc notification is sent to enabled users with email addresses in:
   - Public Article Approver
   - Public Article Manager
   - Admin Users
3. Submit one **Internal** article for approval.
4. Confirm one Bcc notification is sent to enabled users with email addresses in:
   - Internal Article Approver
   - Internal Article Manager
   - Admin Users
5. Confirm internal notification emails do not disclose the internal article title or body.

---

## 10. Logs and troubleshooting

### DjOpenKB worker logs

```bash
cd /opt/DjOpenKB
sudo docker compose logs --tail=150 web
```

Useful outcomes:

| Symptom | Likely cause | First check |
|---|---|---|
| Connection timeout/refused | Firewall, DNS, wrong listener, Exchange service issue | DNS resolution; TCP 587 firewall; connector bindings |
| `CERTIFICATE_VERIFY_FAILED` | Certificate chain is not trusted by the container standard trust store, or FQDN/SAN mismatch | `SMTP_RELAY_HOST`; Exchange certificate SAN; trusted chain in the container image |
| STARTTLS not advertised | TLS not enabled on connector or no matching certificate | Connector `AuthMechanism`; `Fqdn`; `TlsCertificateName`; Exchange Application log |
| Authentication failed | Account cannot authenticate, password stale, wrong connector settings | Service account; `BasicAuthRequireTLS`; Exchange protocol logs |
| Sender rejected | From address differs from authenticated mailbox without Send As | `SMTP_FROM_EMAIL`; mailbox permissions |
| No recipients receive a workflow email | Reviewer accounts inactive, disabled, no email, wrong role scope, or domain allowlist | Django users/groups, email fields, web service logs |

### Exchange checks

```powershell
Get-ReceiveConnector -Identity "Client Frontend*" |
    Format-List Identity,Bindings,Fqdn,AuthMechanism,PermissionGroups,TlsCertificateName,ProtocolLoggingLevel

Get-ExchangeCertificate |
    Format-List Thumbprint,Subject,CertificateDomains,Services,NotAfter,Status,HasPrivateKey
```

If Exchange cannot find a certificate matching the connector's FQDN or `TlsCertificateName`, it may not advertise STARTTLS and can log Event ID 12014 in the Windows Application log.

For protocol logs, check the connector's configured protocol-log path:

```powershell
Get-ReceiveConnector -Identity "Client Frontend*" |
    Format-List Identity,ProtocolLoggingLevel,ProtocolLogPath
```

Enable verbose logging only for a controlled test window and return it to the organisation standard afterwards.

---

## 11. Rollback plan

If testing fails or must be paused:

1. Disable DjOpenKB notifications:

   ```dotenv
   EMAIL_NOTIFICATIONS_ENABLED=false
   ```

2. Recreate the application containers:

   ```bash
   cd /opt/DjOpenKB
   sudo docker compose up -d --force-recreate --remove-orphans web
   ```

3. Do not remove or overwrite Exchange certificates without the Exchange administrator's approval.
4. Restore the Client Frontend connector's documented previous `Fqdn` and `TlsCertificateName` values only if a change was made specifically for this integration.

---

## 12. Production readiness checklist

- [ ] `<EXCHANGE_SMTP_FQDN>` resolves internally to Exchange.
- [ ] DjOpenKB uses the FQDN, never an IP address.
- [ ] Exchange certificate is valid, has a private key, contains the FQDN, and is SMTP-enabled.
- [ ] Client Frontend connector is configured for TCP 587, STARTTLS, and authenticated Exchange users.
- [ ] Network/firewall access to TCP 587 is limited to the DjOpenKB host.
- [ ] No anonymous relay is enabled for DjOpenKB.
- [ ] The Exchange certificate chain is trusted by the web container standard trust store.
- [ ] SMTP credentials are in Vault only.
- [ ] Service account can send from `SMTP_FROM_EMAIL`.
- [ ] A controlled SMTP test succeeds.
- [ ] Public and internal notification routing has been tested separately.
- [ ] Logs have been reviewed and no passwords or sensitive internal content are logged.
- [ ] A separate SMTP-only service account and a password rotation plan are scheduled for production.

---

## References

- Microsoft Learn — Configure authenticated SMTP settings for POP3 and IMAP4 clients in Exchange Server: https://learn.microsoft.com/en-us/exchange/clients/pop3-and-imap4/configure-authenticated-smtp
- Microsoft Learn — Receive connectors in Exchange Server: https://learn.microsoft.com/en-us/exchange/mail-flow/connectors/receive-connectors
- Microsoft Learn — Selection of inbound STARTTLS certificates: https://learn.microsoft.com/en-us/exchange/mail-flow/mail-routing/inbound-starttls-certificates-selection
- Microsoft Learn — Create an Exchange Server certificate request for a certification authority: https://learn.microsoft.com/en-us/exchange/architecture/client-access/create-ca-certificate-requests
- Microsoft Learn — Assign certificates to Exchange Server services: https://learn.microsoft.com/en-us/exchange/architecture/client-access/assign-certificates-to-services
