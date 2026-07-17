# SMTP Relay Setup and Notifications

DjOpenKB can use an organisation SMTP relay to send article-workflow notifications and account-lockout alerts. The Django `web` service sends the messages directly; no separate notification worker is required.

Keep email notifications disabled until the SMTP server, certificate trust, Vault credentials, and test message are working.

---

## 1. Notification Behaviour

| Event | Recipient |
|---|---|
| Public article or public pending update submitted/resubmitted | Active `Article Approver`, `Article Manager`, and `Admin Users` recipients |
| Internal article or internal pending update submitted/resubmitted | Active `Internal Article Approver`, `Internal Article Manager`, and `Admin Users` recipients |
| Article or pending update approved | Current eligible article owner |
| Article or pending update marked Pending failed | Current eligible article owner |
| Recognised account reaches a new password, MFA, or Django Admin MFA lockout | Active eligible `Admin Users` recipients |

Reviewer and lockout alerts are sent as one Bcc-only message. Owner outcome notifications are sent directly to the owner.

Recipients must be active, not assigned to `Disabled User`, allowed to use the main site, have a valid email address, and use a domain listed in `SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS`.

Internal workflow notifications do not include internal article titles, article content, or review comments. SMTP delivery failure is logged but does not undo an article workflow action or remove an account lockout.

---

## 2. Required SMTP Information

Prepare these values before configuring DjOpenKB:

```text
SMTP relay DNS name:     <SMTP_RELAY_FQDN>
SMTP relay port:         587
SMTP service account:    <SMTP_SERVICE_ACCOUNT>
Sender email address:    <SMTP_SENDER_EMAIL>
Allowed recipient domain:<ALLOWED_RECIPIENT_DOMAIN>
```

Use the SMTP server DNS name that matches the certificate SAN/CN. Do not use a raw IP address for the TLS SMTP connection.

---

## 3. SMTP Certificate File

When the SMTP relay uses a self-signed certificate, export that public SMTP server certificate. If the SMTP certificate is issued by a private organisation CA, export the public issuing/root CA certificate or chain instead.

### Export the required public certificate from Windows

1. Open `certlm.msc` on the SMTP/Exchange server.
2. For a self-signed SMTP certificate, go to **Certificates (Local Computer) → Personal → Certificates** and select the certificate used by SMTP.
3. Confirm its DNS name matches the hostname that will be configured in `SMTP_RELAY_HOST`.
4. Right-click the required certificate and select **All Tasks → Export**.
5. Select **No, do not export the private key**.
6. Select **Base-64 encoded X.509 (.CER)**.
7. Export the public certificate and copy it to the DjOpenKB Linux server.

For a private-CA-issued SMTP certificate, use the public issuing/root CA certificate or CA chain rather than exporting a private key or PFX bundle.

Place it in:

```text
/opt/DjOpenKB/ldap-certs/exchange-smtp.crt
```

The file may be exported as `.cer` first and renamed to `exchange-smtp.crt`.

Check the format on Linux:

```bash
cd /opt/DjOpenKB
head -n 1 ldap-certs/exchange-smtp.crt
```

Expected:

```text
-----BEGIN CERTIFICATE-----
```

If this line appears, the certificate is already PEM/Base-64 and no conversion or system-wide certificate installation is required.

If the exported file is binary DER format, convert it once:

```bash
openssl x509 -inform DER \
  -in ldap-certs/exchange-smtp.cer \
  -out ldap-certs/exchange-smtp.crt
```

Verify the certificate:

```bash
openssl x509 \
  -in ldap-certs/exchange-smtp.crt \
  -noout -subject -issuer -dates
```

Docker Compose mounts `ldap-certs/` into the `web` container. The application therefore uses:

```text
/etc/ssl/certs/djopenkb-ldap/exchange-smtp.crt
```

For a certificate already trusted by the container's normal CA store, `SMTP_RELAY_CA_CERT_FILE` may be left blank. Never copy a private key, `.pfx`, or `.p12` file into the project.

---

## 4. `.env` SMTP Configuration

Add the non-secret SMTP values to `.env`:

```env
EMAIL_NOTIFICATIONS_ENABLED=false

SMTP_RELAY_HOST=<SMTP_RELAY_FQDN>
SMTP_RELAY_PORT=587
SMTP_RELAY_USE_TLS=true
SMTP_RELAY_USE_SSL=false
SMTP_RELAY_TIMEOUT_SECONDS=10

SMTP_RELAY_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/exchange-smtp.crt
SMTP_FROM_EMAIL=<SMTP_SENDER_EMAIL>
SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS=<ALLOWED_RECIPIENT_DOMAIN>
SITE_BASE_URL=https://<INTERNAL_SERVER_IP>:8080
EMAIL_SUBJECT_PREFIX=[Knowledge Repository]
```

Use `SMTP_RELAY_USE_TLS=true` for normal STARTTLS on port `587`. Do not enable both `SMTP_RELAY_USE_TLS` and `SMTP_RELAY_USE_SSL` at the same time.

`SITE_BASE_URL` must be the exact HTTPS address used to access DjOpenKB and must not have a trailing slash.

Keep `EMAIL_NOTIFICATIONS_ENABLED=false` until the SMTP credentials and certificate are ready.

---

## 5. Store SMTP Credentials in Vault

Do not place the SMTP username or password in `.env`.

For a fresh deployment, add them to `vault/bootstrap/djopenkb.env` before the initial Vault seed:

```env
SMTP_RELAY_USERNAME=<SMTP_SERVICE_ACCOUNT>
SMTP_RELAY_PASSWORD=<SMTP_SERVICE_ACCOUNT_PASSWORD>
```

For an existing deployment, the temporary bootstrap file can contain only the SMTP values being added or changed. Existing unrelated Vault values are preserved by the Vault initialization script.

Apply the update:

```bash
cd /opt/DjOpenKB
sudo docker compose up -d --force-recreate vault-init
sudo rm -f vault/bootstrap/djopenkb.env
```

Then recreate the Django web service:

```bash
sudo docker compose up -d --force-recreate web
```

---

## 6. Test the SMTP Relay

After the certificate and credentials are ready, change:

```env
EMAIL_NOTIFICATIONS_ENABLED=true
```

Recreate the web service:

```bash
cd /opt/DjOpenKB
sudo docker compose up -d --force-recreate web
```

Confirm the certificate is available inside the container when `SMTP_RELAY_CA_CERT_FILE` is configured:

```bash
sudo docker compose exec web \
  sh -c 'test -r /etc/ssl/certs/djopenkb-ldap/exchange-smtp.crt && echo "SMTP certificate is available."'
```

Send one controlled test message to an allowed recipient:

```bash
sudo docker compose exec web \
  python manage.py test_smtp_relay <TEST_RECIPIENT_EMAIL>
```

Expected result:

```text
SMTP relay accepted one test message. Check the recipient mailbox.
```

After the test succeeds, submit one test article and confirm the expected reviewer notification is received.

---

## 7. Common Errors

### Certificate validation fails

Check that:

```text
- SMTP_RELAY_HOST matches the certificate SAN/CN.
- exchange-smtp.crt is the correct public certificate or CA/chain.
- The file is PEM/Base-64 text.
- SMTP_RELAY_CA_CERT_FILE uses the container path, not the host path.
```

### SMTP authentication fails

Check the Vault-stored `SMTP_RELAY_USERNAME` and `SMTP_RELAY_PASSWORD` and confirm the account is permitted to send as `SMTP_FROM_EMAIL`.

### Recipient is rejected by DjOpenKB

Confirm the recipient domain is included in:

```text
SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS
```

### No workflow notification is received

Confirm:

```text
EMAIL_NOTIFICATIONS_ENABLED=true
```

Then check the recipient's DjOpenKB role, account status, email address, and the Django `web` logs.
