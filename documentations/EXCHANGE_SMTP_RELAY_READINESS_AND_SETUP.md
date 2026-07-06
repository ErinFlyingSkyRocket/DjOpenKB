# DjOpenKB — Exchange SMTP Certificate Setup (Windows GUI + Linux Server)

## Purpose

This guide prepares the Exchange SMTP TLS certificate that DjOpenKB trusts for article-review email notifications.

DjOpenKB connects to Exchange by DNS name over authenticated SMTP with STARTTLS on TCP `587`. TLS certificate and hostname validation remain enabled. For the current lab, Exchange presents a self-signed certificate, so DjOpenKB needs a copy of its **public certificate only**.

The Exchange certificate work uses the **Exchange Admin Center** and **Windows Certificate Manager**. The final Linux commands validate and prepare the exported public certificate for the DjOpenKB Docker container.

---

## Before you start

### Current lab configuration

The current Exchange SMTP service presents this self-signed certificate:

```text
Subject: CN=qapf1-exch
Issuer:  CN=qapf1-exch
```

For this current certificate, DjOpenKB must use this SMTP hostname:

```dotenv
SMTP_RELAY_HOST=qapf1-exch
```

Do not use the Exchange IP address. Do not use the full FQDN unless the certificate contains that FQDN in its Common Name (CN) or Subject Alternative Name (SAN).

### Decide which path applies

| Situation | What to do |
|---|---|
| A valid SMTP certificate already exists and is assigned to SMTP | Export its public certificate using the GUI steps in **Section 2**. This is the current lab path. |
| No suitable SMTP certificate exists, or a new hostname is needed | Create a new self-signed certificate in Exchange Admin Center using **Section 3**, assign it to SMTP, then export it using **Section 2**. |
| A long-lived production deployment is planned | Prefer an organisation-CA-issued certificate that contains the final Exchange SMTP FQDN. See **Section 8**. |

Do not delete, replace, or unassign an existing Exchange certificate unless you know it is safe for the server’s other Exchange services.

---

## 1. Confirm which certificate Exchange uses for SMTP

1. Sign in to the **Exchange Admin Center** with an account permitted to manage Exchange certificates.
2. Go to **Servers** → **Certificates**.
3. Select the Exchange server from the server list.
4. Select the certificate that shows the SMTP-related name or matches `qapf1-exch`.
5. Choose **Edit** and open the **Services** tab.
6. Confirm that **SMTP** is selected for the certificate.
7. Open the certificate details and confirm that the certificate is valid and not expired.

For the current lab certificate, the certificate subject should show:

```text
CN=qapf1-exch
```

If that certificate is valid and SMTP is assigned, continue to the export steps.

---

## 2. Export the existing SMTP certificate as a public `.crt` file

This exports only the public certificate that DjOpenKB needs. It does not export the Exchange private key.

1. On the Exchange server, press **Windows + R**.
2. Enter:

```text
certlm.msc
```

3. Open:

```text
Certificates (Local Computer)
→ Personal
→ Certificates
```

4. Find the certificate whose **Issued To** value is:

```text
qapf1-exch
```

5. Double-click the certificate and confirm the **Subject** value contains:

```text
CN = qapf1-exch
```

6. Close the certificate details window.
7. Right-click the certificate → **All Tasks** → **Export**.
8. In the Certificate Export Wizard, click **Next**.
9. Select:

```text
No, do not export the private key
```

10. Select:

```text
Base-64 encoded X.509 (.CER)
```

11. Save the file as:

```text
C:\Temp\exchange-smtp.cer
```

12. Complete the wizard. Windows should show that the export was successful.
13. In File Explorer, rename the file to:

```text
exchange-smtp.crt
```

The **Base-64** export is already PEM-compatible. Renaming the extension from `.cer` to `.crt` is enough; no Windows conversion command is required.

### Important export rules

- Select **No, do not export the private key**.
- Do **not** export a `.pfx` or `.p12` file.
- Do **not** share a private key or certificate password.
- The exported file must contain only the public certificate.

---

## 3. Create a new self-signed SMTP certificate in the Exchange Admin Center (only if needed)

Use this section only when no usable SMTP certificate exists, the existing certificate is expired, or you want a new certificate with the full Exchange FQDN.

For the current lab, use these two DNS names:

```text
qapf1-exch.qapf1.qalab01.nextlabs.com
qapf1-exch
```

The full FQDN should be the Common Name. The short name should also be included so existing short-hostname connections continue to work.

1. Open the **Exchange Admin Center**.
2. Go to **Servers** → **Certificates**.
3. Select the Exchange server.
4. Click **Add** (`+`).
5. Select:

```text
Create a self-signed certificate
```

6. Enter a clear friendly name, for example:

```text
DjOpenKB SMTP TLS
```

7. Select the Exchange server that will use the certificate, then select **Next**.
8. On the domain-name pages, add these names:

```text
qapf1-exch.qapf1.qalab01.nextlabs.com
qapf1-exch
```

9. Select:

```text
qapf1-exch.qapf1.qalab01.nextlabs.com
```

and choose **Set as common name**. It should appear in bold in the domain list.
10. Confirm both names remain listed, then click **Finish**.
11. Return to **Servers** → **Certificates** and wait until the new certificate status is **Valid**.
12. Select the new certificate → **Edit** → **Services**.
13. Select **SMTP**, then click **Save**.

Exchange may ask whether to replace the current default SMTP certificate. Proceed only when you intentionally want DjOpenKB SMTP to use the new certificate and no other Exchange integration depends on the old SMTP certificate. Do not delete the previous certificate.

After creating the new certificate, export its public certificate using **Section 2**. Then use this host in DjOpenKB:

```dotenv
SMTP_RELAY_HOST=qapf1-exch.qapf1.qalab01.nextlabs.com
```

---

## 4. Place the exported certificate in DjOpenKB

Use your approved secure file-transfer method to place the exported public certificate on the DjOpenKB server at:

```text
/opt/DjOpenKB/ldap-certs/exchange-smtp.crt
```

The filename and path must be exactly as shown.

Do not copy a `.pfx`, private key, password, or any other Exchange certificate file into this folder.

---

## 5. Prepare and validate the certificate on the Linux server

These commands run on the DjOpenKB Linux server after `exchange-smtp.crt` has been copied into the folder above.

### 5.1 Confirm that the file is PEM/Base-64 text

```bash
cd /opt/DjOpenKB

sudo head -n 1 ldap-certs/exchange-smtp.crt
sudo tail -n 1 ldap-certs/exchange-smtp.crt
```

Expected output:

```text
-----BEGIN CERTIFICATE-----
-----END CERTIFICATE-----
```

When those two lines appear, the Windows GUI export is already in the correct PEM format. **No Linux conversion is needed.**

### 5.2 Convert only if the file is not Base-64/PEM

If the first line does not show `-----BEGIN CERTIFICATE-----`, the certificate was likely exported as binary DER instead of **Base-64 encoded X.509 (.CER)**.

The preferred fix is to repeat the GUI export in **Section 2** and select **Base-64 encoded X.509 (.CER)**.

Only if you must convert an already-copied binary certificate, keep the original as `exchange-smtp.cer` and run:

```bash
cd /opt/DjOpenKB

sudo openssl x509 -inform DER \
  -in ldap-certs/exchange-smtp.cer \
  -out ldap-certs/exchange-smtp.crt
```

Then run the checks in **Section 5.1** again.

### 5.3 Validate the certificate contents

```bash
cd /opt/DjOpenKB

sudo openssl x509 \
  -in ldap-certs/exchange-smtp.crt \
  -noout -subject -issuer -dates
```

For the current lab certificate, the output should show a subject and issuer similar to:

```text
subject=CN = qapf1-exch
issuer=CN = qapf1-exch
```

This confirms Linux can read the public certificate file. It does not expose or require a private key.

### 5.4 Set safe read permissions

```bash
cd /opt/DjOpenKB

sudo chown root:root ldap-certs/exchange-smtp.crt
sudo chmod 644 ldap-certs/exchange-smtp.crt
```

The certificate is public information. Read access is required so the non-root process inside the DjOpenKB `web` container can use it.

---

## 6. Configure DjOpenKB and make the Docker container use the certificate

Confirm that `/opt/DjOpenKB/.env` contains the correct values.

For the current self-signed certificate (`CN=qapf1-exch`):

```dotenv
SMTP_RELAY_HOST=qapf1-exch
SMTP_RELAY_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/exchange-smtp.crt
SMTP_RELAY_PORT=587
SMTP_RELAY_USE_TLS=true
SMTP_RELAY_USE_SSL=false
```

The Docker Compose configuration mounts the host folder:

```text
/opt/DjOpenKB/ldap-certs
```

inside the `web` container as:

```text
/etc/ssl/certs/djopenkb-ldap
```

Restart the `web` service so Django reloads the SMTP certificate setting:

```bash
cd /opt/DjOpenKB

sudo docker compose up -d --force-recreate web
```

Confirm that the running container can read the certificate:

```bash
cd /opt/DjOpenKB

sudo docker compose exec web \
  sh -c 'test -r /etc/ssl/certs/djopenkb-ldap/exchange-smtp.crt && echo "Exchange SMTP certificate is available inside the web container."'
```

Expected output:

```text
Exchange SMTP certificate is available inside the web container.
```

### Important hostname check

The current certificate name is only `qapf1-exch`. The web container must be able to resolve that short hostname:

```bash
cd /opt/DjOpenKB

sudo docker compose exec web getent hosts qapf1-exch
```

If this does not return the Exchange server IP address, do not change `SMTP_RELAY_HOST` to an IP address. Instead, ask the DNS administrator to make `qapf1-exch` resolvable from the DjOpenKB server and Docker containers, or issue a new Exchange SMTP certificate containing the full FQDN and then use that FQDN as the SMTP host.

---

## 7. Test SMTP notification delivery

Send one controlled test message after the certificate is visible inside the container:

```bash
cd /opt/DjOpenKB

sudo docker compose exec web \
  python manage.py test_smtp_relay john.tyler@qapf1.qalab01.nextlabs.com
```

Use a valid recipient mailbox that belongs to a domain allowed by `SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS`.

If the command succeeds, check the recipient mailbox in Outlook.

If it fails, inspect the web service log:

```bash
cd /opt/DjOpenKB

sudo docker compose logs --tail=150 web
```

### Common errors

| Error | Likely cause | Correct action |
|---|---|---|
| `SSLCertVerificationError` | Wrong certificate, missing `.crt`, or certificate changed on Exchange | Re-export the current public SMTP certificate and repeat Sections 4–6. |
| Hostname mismatch | `SMTP_RELAY_HOST` does not match the certificate CN/SAN | Use `qapf1-exch` for the current certificate, or issue a certificate that includes the FQDN. |
| Hostname cannot resolve | The Docker container cannot find `qapf1-exch` through DNS | Add or fix the internal DNS record; do not use a raw IP address. |
| Authentication failed | SMTP username or password is wrong, or authenticated SMTP is unavailable for the mailbox | Confirm the Exchange mailbox credentials and SMTP settings. |

Do not disable TLS certificate verification to bypass any error in this table.

---

## 8. Recommended production improvement: use an organisation-CA-issued certificate

For a long-lived deployment, use an organisation-issued certificate instead of a self-signed certificate. The certificate should contain the final Exchange SMTP name, for example:

```text
qapf1-exch.qapf1.qalab01.nextlabs.com
```

In the Exchange Admin Center, go to **Servers** → **Certificates** → **Add** and select:

```text
Create a request for a certificate from a certification authority
```

Use the intended SMTP FQDN as the Common Name and add other required Exchange names as SANs. After the certificate authority issues the certificate, complete the pending request in Exchange Admin Center, assign the certificate to **SMTP**, and export the public issuing CA certificate or CA chain through the Windows Certificate Manager GUI.

For DjOpenKB, place that public CA certificate or chain at the same project location:

```text
/opt/DjOpenKB/ldap-certs/exchange-smtp.crt
```

Then keep the existing Docker path in `.env`:

```dotenv
SMTP_RELAY_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/exchange-smtp.crt
```

---

## 9. Quick checklist

Before testing DjOpenKB notifications, confirm all of the following:

- [ ] The Exchange certificate is valid and assigned to **SMTP**.
- [ ] The certificate contains the same DNS name configured in `SMTP_RELAY_HOST`.
- [ ] The certificate was exported with **No, do not export the private key**.
- [ ] The exported format was **Base-64 encoded X.509 (.CER)**.
- [ ] The file is named `exchange-smtp.crt`.
- [ ] The file begins with `-----BEGIN CERTIFICATE-----` and ends with `-----END CERTIFICATE-----`.
- [ ] Linux validates the file with `openssl x509 -in ... -noout -subject -issuer -dates`.
- [ ] The file is stored at `/opt/DjOpenKB/ldap-certs/exchange-smtp.crt`.
- [ ] `SMTP_RELAY_CA_CERT_FILE` points to `/etc/ssl/certs/djopenkb-ldap/exchange-smtp.crt`.
- [ ] SMTP uses TCP `587` with STARTTLS enabled.
- [ ] The web container can read the certificate file.
- [ ] No PFX, private key, or password was copied into DjOpenKB.

---

## 10. When the certificate changes later

If the Exchange SMTP certificate is renewed or replaced:

1. Export the new public certificate again through **Section 2**.
2. Replace `/opt/DjOpenKB/ldap-certs/exchange-smtp.crt` with the new public file.
3. Repeat Sections **5** and **6**.
4. Keep `SMTP_RELAY_HOST` aligned with the new certificate CN/SAN.
5. Recreate the DjOpenKB `web` service and run the SMTP relay test.

Do not disable TLS certificate verification to work around a changed or untrusted certificate.
