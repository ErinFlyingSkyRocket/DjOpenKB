# SMTP Relay Workflow and Lockout Notifications

## Purpose

DjOpenKB uses the organisation SMTP relay and one Vault-stored SMTP service account for workflow and security notifications. The Django `web` service sends the email directly; no notification Celery queue or `notification-worker` container is required.

The Exchange certificate export and Linux trust-file process are in [EXCHANGE_SMTP_RELAY_READINESS_AND_SETUP.md](EXCHANGE_SMTP_RELAY_READINESS_AND_SETUP.md).

```text
Article submission or outcome                     New password/MFA lockout
              |                                                |
              v                                                v
Django commits workflow change                  Django writes the lockout audit row
              |                                                |
              v                                                v
Resolve current eligible recipients              Resolve current eligible Admin Users
              |                                                |
              +---------------------+--------------------------+
                                    v
                    SMTP relay sends one privacy-preserving email
```

A relay problem never reverses a valid article workflow decision and never removes or shortens an account lockout. The original workflow or lockout audit event remains available to administrators.

## Recipient Matrix

The application uses the current **DjOpenKB Django role groups**, not Active Directory security groups. This keeps email delivery aligned with the permissions that control the relevant workflow or administration surface.

| Event | Eligible recipients | Delivery style |
|---|---|---|
| Public new article or public published-article update submitted/resubmitted | Active, non-disabled `Article Approver`, `Article Manager`, or `Admin Users` | One Bcc message |
| Internal new article or internal published-article update submitted/resubmitted | Active, non-disabled `Internal Article Approver`, `Internal Article Manager`, or `Admin Users` | One Bcc message |
| Public/internal article or pending update approved | Current eligible article owner only | One direct `To` message |
| Public/internal article or pending update marked Pending failed | Current eligible article owner only | One direct `To` message |
| New temporary password, normal MFA, or Django Admin MFA lockout for a recognised account | Active, non-disabled `Admin Users` | One Bcc message |

A recipient must also be main-site enabled, have a valid `User.email`, and use a domain listed in `SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS`. Duplicate addresses receive only one Bcc recipient entry. A standalone Django superuser is **not** a recipient unless they also belong to `Admin Users`.

For owner outcome messages, the owner does not need an approver, manager, or admin role. They must simply remain active, non-disabled, main-site enabled, and have a valid allowlisted address.

## Lockout Alert Behaviour

A lockout alert is created only when the progressive lockout policy creates a **new** temporary block, such as the configured 5-minute, 15-minute, or 1-hour stage. Attempts made while that same block is still active do not send another email.

Known-account password, normal MFA, and Django Admin MFA lockouts all use this rule. Failed attempts against an **unknown username** remain recorded in `AuthActivityLog`, but do not email administrators. This prevents arbitrary login names from being used to flood administrator inboxes.

The email includes only the account username, lockout type, temporary duration, policy stage, lockout strike, source IP, and a protected Django Admin authentication-log link. It never includes a password, MFA code, SMTP secret, or user agent.

## Privacy and Security Behaviour

- SMTP username and password are read only from Vault as `SMTP_RELAY_USERNAME` and `SMTP_RELAY_PASSWORD`.
- The web service opens TLS before SMTP authentication. `SMTP_RELAY_USE_TLS=true` is the normal STARTTLS configuration for port `587`.
- Certificate and hostname validation remain enabled. The SMTP backend starts with the web container's normal trust store and can add one read-only public certificate from `SMTP_RELAY_CA_CERT_FILE` for a private CA or self-signed Exchange relay.
- Bcc delivery is used for reviewer pools and lockout alerts. Recipient addresses are not exposed in `To`, `Cc`, or email headers.
- Public article notifications include only the public article title, never article content. Internal article notifications omit the internal title, content, and review comments.
- Owner outcome messages send only to the current eligible owner. The direct link requires normal DjOpenKB sign-in; Pending-failed review comments remain inside DjOpenKB.
- Recipient addresses must use a domain listed in `SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS`.
- Authentication lockout events are always recorded in append-only `AuthActivityLog` before the optional email is sent.
- The direct path has no automatic retry. This avoids duplicate email when an SMTP relay accepts a message but later reports an error. SMTP failures are written to the application log and never change the workflow or lockout state.

## When Email Is Sent

| Workflow or security action | Email sent? |
|---|---:|
| Writer saves a new draft | No |
| Writer submits/resubmits a new article | Yes, matching reviewer pool |
| Writer saves a private draft of a published-article update | No |
| Writer submits/resubmits a published-article update | Yes, matching reviewer pool |
| Reviewer saves edits while keeping an item pending | No |
| Reviewer approves an article or pending update | Yes, current eligible owner |
| Reviewer marks an article or pending update Pending failed | Yes, current eligible owner |
| Admin publishes their own article directly | No redundant owner email |
| A recognised account triggers a new password/MFA/Admin MFA temporary lockout | Yes, active eligible `Admin Users` |
| Retry while an existing temporary lockout is still active | No |
| Unknown username triggers a temporary password lockout | No email; append-only authentication log only |

## Prerequisites

1. The SMTP relay must permit the dedicated service account to authenticate and send using `SMTP_FROM_EMAIL`.
2. The relay must provide TLS, usually STARTTLS on TCP `587`.
3. Configure the relay DNS hostname shown on its TLS certificate. Do not use a raw IP address for a TLS connection.
4. For a private-CA or self-signed relay certificate, place its public trust certificate in `ldap-certs/exchange-smtp.crt` and configure `SMTP_RELAY_CA_CERT_FILE`. For the current Exchange lab, export the certificate through the Windows GUI and prepare it on Linux using [EXCHANGE_SMTP_RELAY_READINESS_AND_SETUP.md](EXCHANGE_SMTP_RELAY_READINESS_AND_SETUP.md). For a CA-issued relay already trusted by the container, leave the setting blank.
5. The `web` container must be able to resolve and reach the relay hostname on its configured port.
6. Each intended reviewer must have a valid Django `User.email` address within the configured recipient-domain allowlist. Active Directory users normally receive their email value from the existing LDAP mapping during login.

## Configuration

### 1. Add non-secret values to `.env`

Keep notifications disabled until the controlled relay test succeeds. Add only non-secret values to `.env`:

```dotenv
EMAIL_NOTIFICATIONS_ENABLED=false

# Use the exact DNS name in the SMTP certificate. Never use an IP address.
# Current lab self-signed certificate: CN=qapf1-exch
SMTP_RELAY_HOST=qapf1-exch
SMTP_RELAY_PORT=587
SMTP_RELAY_USE_TLS=true
SMTP_RELAY_USE_SSL=false
SMTP_RELAY_TIMEOUT_SECONDS=10

# Public PEM/CRT trust certificate. Use a CA/chain or the exact self-signed
# server certificate; never use a PFX or private key.
SMTP_RELAY_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/exchange-smtp.crt

SMTP_FROM_EMAIL=<SMTP_SENDER_EMAIL>
SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS=qapf1.qalab01.nextlabs.com
SITE_BASE_URL=https://<INTERNAL_SERVER_IP>:8080
EMAIL_SUBJECT_PREFIX=[Knowledge Repository]
```

`SITE_BASE_URL` must be the exact HTTPS browser origin, without a path, query string, fragment, or trailing slash. For a newly issued Exchange certificate that contains the full FQDN, use that exact FQDN in `SMTP_RELAY_HOST` instead of `qapf1-exch`.

Configure exactly one TLS mode:

```dotenv
# Typical authenticated SMTP submission.
SMTP_RELAY_PORT=587
SMTP_RELAY_USE_TLS=true
SMTP_RELAY_USE_SSL=false
```

```dotenv
# Only when the relay explicitly requires implicit TLS.
SMTP_RELAY_PORT=465
SMTP_RELAY_USE_TLS=false
SMTP_RELAY_USE_SSL=true
```

### 2. Provide the Exchange public trust certificate when required

The SMTP backend always begins with the web container's normal operating-system trust store. When Exchange uses a private CA or a self-signed certificate that the container does not already trust, copy a **public PEM/CRT** file into the mounted project directory:

```text
/opt/DjOpenKB/ldap-certs/exchange-smtp.crt
```

Then set:

```dotenv
SMTP_RELAY_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/exchange-smtp.crt
```

For a CA-issued Exchange certificate, use the public issuing CA certificate or full CA chain. For a temporary self-signed Exchange certificate, use the exact public Exchange server certificate. Do not use a PFX/P12, a private key, or the unrelated LDAPS CA certificate unless it actually issued the Exchange SMTP certificate.

Verify the file is PEM/CRT and does not contain a private key:

```bash
openssl x509 -in /opt/DjOpenKB/ldap-certs/exchange-smtp.crt \
  -noout -subject -issuer -ext subjectAltName
```

`SMTP_RELAY_HOST` must exactly match a DNS name in the certificate SAN/CN. Do not disable certificate or hostname validation.

### 3. Store only SMTP credentials in Vault

For an existing deployment, create a temporary bootstrap file containing only the values to add or rotate:

```bash
sudo nano vault/bootstrap/djopenkb.env
```

```dotenv
# Use the mail-enabled Exchange account that is permitted to send as
# SMTP_FROM_EMAIL. A dedicated mailbox is recommended for a long-lived service.
SMTP_RELAY_USERNAME=<SMTP_MAILBOX_UPN>
SMTP_RELAY_PASSWORD=<SMTP_MAILBOX_PASSWORD>
SMTP_RELAY_PASSWORD_USE_LDAP_BIND_PASSWORD=false
```

Do not put either credential in `.env`. The `false` value keeps the SMTP mailbox password separate from the LDAP bind password. Use `true` only when both services deliberately use the exact same account password.

Seed the values and remove the temporary bootstrap file:

```bash
sudo docker compose up -d --force-recreate vault-init
sudo rm -f vault/bootstrap/djopenkb.env
```

## Fresh Deployment and Testing

For a fresh deployment, complete the configuration in this order:

1. Complete the Exchange certificate export and Linux certificate validation in [EXCHANGE_SMTP_RELAY_READINESS_AND_SETUP.md](EXCHANGE_SMTP_RELAY_READINESS_AND_SETUP.md). The public file must be available at `ldap-certs/exchange-smtp.crt` before `web` starts.
2. Add the non-secret SMTP values to `.env` and the mailbox credentials to `vault/bootstrap/djopenkb.env` before the initial `sudo docker compose up -d --build`.
3. Keep `EMAIL_NOTIFICATIONS_ENABLED=false` until the site starts successfully and the SMTP trust file is confirmed readable. Then change it to `true` and recreate `web`:

   ```bash
   cd /opt/DjOpenKB
   sudo docker compose up -d --force-recreate web
   ```

4. Confirm the running container can read the certificate and resolve the SMTP hostname:

   ```bash
   sudo docker compose exec web \
     sh -c 'test -r /etc/ssl/certs/djopenkb-ldap/exchange-smtp.crt && echo "Exchange SMTP certificate is available inside the web container."'

   sudo docker compose exec web getent hosts qapf1-exch
   ```

5. Send one controlled test to a mailbox in `SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS`:

   ```bash
   sudo docker compose exec web \
     python manage.py test_smtp_relay <TEST_RECIPIENT_EMAIL>
   ```

   Expected result:

   ```text
   SMTP relay accepted one test message. Check the recipient mailbox.
   ```

6. Check application logs if needed:

   ```bash
   sudo docker compose logs --tail=120 web
   ```

When enabling SMTP on an already-running deployment, add the SMTP credentials to a temporary Vault bootstrap file, run `sudo docker compose up -d --force-recreate vault-init`, remove the plaintext bootstrap file, then recreate `web` before testing.

## Expected Workflow Results

- Public and internal reviewer pools remain strictly separated, except that `Admin Users` receive both scopes.
- Approved and Pending-failed outcomes notify only the current eligible article owner.
- A recognised user triggering a new password/MFA/Admin MFA temporary lockout sends one Bcc alert to current eligible `Admin Users`.
- Bcc is used for reviewer pools and administrator lockout alerts; direct `To` is used only for the single article owner outcome message.
- Inactive, disabled, main-site-blocked, blank-email, invalid-email, or non-allowlisted recipients are skipped without interrupting the article or lockout workflow.
- A direct Django superuser without `Admin Users` does not receive reviewer or lockout messages.
- Unknown-user password lockouts remain visible in the authentication activity log but intentionally do not send email.
- When SMTP is unavailable, the article state and lockout state remain unchanged. Review the application logs and the original append-only audit record.

## Troubleshooting

| Symptom | First checks |
|---|---|
| Relay test cannot connect | Relay DNS name, internal DNS, firewall to TCP 587, Exchange receive connector |
| `CERTIFICATE_VERIFY_FAILED` | `SMTP_RELAY_HOST` must match certificate SAN/CN; confirm `SMTP_RELAY_CA_CERT_FILE` points to a readable PEM/CRT that issued or is the Exchange certificate |
| STARTTLS unavailable | Exchange connector TLS settings and assigned SMTP certificate |
| Authentication fails | Vault SMTP credentials, account status, Exchange authenticated SMTP settings |
| Sender rejected | `SMTP_FROM_EMAIL` and the service account's Send As / mailbox permissions |
| No workflow recipients | Matching reviewer role groups, article-owner status, active/disabled status, Django `User.email`, recipient-domain allowlist |
| No lockout alert | Confirm a **recognised** account triggered a new temporary block, then check active `Admin Users` group membership, recipient-domain allowlist, and application logs |

Historical `article_review_notification_queued` audit records are retained for existing data, but new direct delivery does not create queue events.
