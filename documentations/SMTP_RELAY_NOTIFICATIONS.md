# SMTP Relay Article-Review Notifications

## Purpose

DjOpenKB sends an email when a writer submits an article for review or submits a published-article update for review. It uses the organisation SMTP relay and one Vault-stored SMTP service account.

The Django `web` service sends the message **after the article database transaction commits**. There is no separate notification Celery queue or `notification-worker` container. The Exchange certificate export and Linux trust-file process are in [EXCHANGE_SMTP_RELAY_READINESS_AND_SETUP.md](EXCHANGE_SMTP_RELAY_READINESS_AND_SETUP.md).

```text
Writer submits an article
        |
        v
Django commits the pending article/update
        |
        v
Django resolves eligible current role-group users
        |
        v
One SMTP message with all recipients in Bcc
        |
        v
Exchange / SMTP relay delivers the message
```

A relay problem never rolls back a valid article submission. DjOpenKB records the failure in the append-only activity log and the item remains pending for review.

## Recipient Matrix

The application uses the existing **Django role groups**, not Active Directory security groups, so email delivery matches the permissions that control each review queue.

| Submitted item | Eligible recipients |
|---|---|
| Public new article or public published-article update | `Article Approver`, `Article Manager`, `Admin Users` |
| Internal new article or internal published-article update | `Internal Article Approver`, `Internal Article Manager`, `Admin Users` |

A duplicate email address receives only one message. Inactive, disabled, main-site-blocked, blank-email, invalid-email, and non-allowlisted accounts are excluded. A direct Django superuser is included defensively for older administrative accounts that have not yet been normalised into `Admin Users`.

## Privacy and Security Behaviour

- SMTP username and password are read only from Vault as `SMTP_RELAY_USERNAME` and `SMTP_RELAY_PASSWORD`.
- The web service opens TLS before SMTP authentication. `SMTP_RELAY_USE_TLS=true` is the normal STARTTLS configuration for port `587`.
- Certificate and hostname validation remain enabled. The SMTP backend starts with the web container's normal trust store and can add one read-only public certificate from `SMTP_RELAY_CA_CERT_FILE` for a private CA or self-signed Exchange relay.
- DjOpenKB sends **one Bcc-only message** per review event. Reviewer email addresses are not exposed to other reviewers in `To`, `Cc`, or message headers.
- Public notifications include only the public article title, never article content.
- Internal notifications omit both internal title and internal content because inboxes and relay logs are outside the internal article access-control boundary.
- Recipient addresses must use a domain listed in `SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS`.
- Audit entries contain only status, counts, scope, and reason codes. They never contain recipient addresses, passwords, or SMTP protocol details.
- The direct path has no automatic retry. This avoids duplicate review email when an SMTP relay accepts a message but reports an error afterwards. A relay failure is logged and can be addressed by resubmitting an eligible item or by using the administrator workflow after the relay is restored.

## When Email Is Sent

| Workflow action | Email sent? |
|---|---:|
| Writer saves a new draft | No |
| Writer submits a new article | Yes |
| Writer resubmits a draft or pending-failed article | Yes |
| Writer saves a private draft of a published-article update | No |
| Writer submits a published-article update | Yes |
| Writer edits an already-pending item | No |
| Reviewer saves edits while keeping an item pending | No |
| Admin publishes directly | No |
| Admin approves or rejects an item | No |

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

- Public submissions notify only public approvers, public managers, and admins.
- Internal submissions notify only internal approvers, internal managers, and admins.
- One SMTP message is submitted per event with all eligible recipients in Bcc.
- Missing or invalid reviewer email addresses do not stop the article workflow; those accounts are skipped.
- When there are no eligible recipients, the item remains pending and an audit event records the skip.
- When SMTP is unavailable, the item remains pending and an audit event records the failure.

## Troubleshooting

| Symptom | First checks |
|---|---|
| Relay test cannot connect | Relay DNS name, internal DNS, firewall to TCP 587, Exchange receive connector |
| `CERTIFICATE_VERIFY_FAILED` | `SMTP_RELAY_HOST` must match certificate SAN/CN; confirm `SMTP_RELAY_CA_CERT_FILE` points to a readable PEM/CRT that issued or is the Exchange certificate |
| STARTTLS unavailable | Exchange connector TLS settings and assigned SMTP certificate |
| Authentication fails | Vault SMTP credentials, account status, Exchange authenticated SMTP settings |
| Sender rejected | `SMTP_FROM_EMAIL` and the service account's Send As / mailbox permissions |
| No workflow recipients | Reviewer role groups, active/disabled status, Django `User.email`, recipient-domain allowlist |

Historical `article_review_notification_queued` audit records are retained for existing data, but new direct delivery does not create queue events.
