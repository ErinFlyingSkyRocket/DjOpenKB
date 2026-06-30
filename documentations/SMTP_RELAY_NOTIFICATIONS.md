# SMTP Relay Article-Review Notifications

## Purpose

DjOpenKB sends an email when a writer submits an article for review or submits a published-article update for review. It uses the organisation SMTP relay and one Vault-stored SMTP service account.

The Django `web` service sends the message **after the article database transaction commits**. There is no separate notification Celery queue or `notification-worker` container.

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
- Certificate and hostname validation remain enabled. An enterprise CA file can be mounted under `ldap-certs/` and referenced by `SMTP_RELAY_CA_CERT_FILE`.
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
4. The `web` container must be able to resolve and reach the relay hostname on its configured port.
5. Each intended reviewer must have a valid Django `User.email` address within the configured recipient-domain allowlist. Active Directory users normally receive their email value from the existing LDAP mapping during login.

## Configuration

### 1. Add non-secret values to `.env`

Keep notifications disabled until the controlled relay test succeeds.

```dotenv
EMAIL_NOTIFICATIONS_ENABLED=false
SMTP_RELAY_HOST=<EXCHANGE_SMTP_FQDN>
SMTP_RELAY_PORT=587
SMTP_RELAY_USE_TLS=true
SMTP_RELAY_USE_SSL=false
SMTP_RELAY_TIMEOUT_SECONDS=10
SMTP_RELAY_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/exchange-ca-chain.crt
SMTP_FROM_EMAIL=knowledge-repository@company.example
SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS=company.example
SITE_BASE_URL=https://<PUBLIC_HOSTNAME>
EMAIL_SUBJECT_PREFIX=[Knowledge Repository]
```

`SITE_BASE_URL` must be the exact HTTPS browser origin, without a path, query string, fragment, or trailing slash.

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

### 2. Make the issuing CA available

Place the trusted Exchange issuing CA certificate or complete chain on the host:

```bash
sudo install -d -m 0755 ldap-certs
sudo install -m 0644 <EXCHANGE_CA_CHAIN_ON_HOST> ldap-certs/exchange-ca-chain.crt
```

The directory is mounted read-only into the Django web container at `/etc/ssl/certs/djopenkb-ldap/`.

When Exchange uses the same issuing CA as LDAPS, use the existing path instead:

```dotenv
SMTP_RELAY_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/ad-ca.crt
```

Do not copy an Exchange `.pfx`, server private key, or leaf server certificate to DjOpenKB.

### 3. Store only SMTP credentials in Vault

For an existing deployment, create a temporary bootstrap file containing only the values to add or rotate:

```bash
sudo nano vault/bootstrap/djopenkb.env
```

```dotenv
SMTP_RELAY_USERNAME=svc_djopenkb_mail@ad.example.com
SMTP_RELAY_PASSWORD=<SMTP_SERVICE_ACCOUNT_PASSWORD>
```

Seed the values and remove the temporary bootstrap file:

```bash
sudo docker compose up -d --force-recreate vault-init
sudo rm -f vault/bootstrap/djopenkb.env
```

## Deployment and Testing

1. Deploy the direct-SMTP patch. The first update removes the retired worker container:

   ```bash
   cd /opt/DjOpenKB
   sudo docker compose up -d --build --remove-orphans
   ```

2. Recreate the web service after changing SMTP configuration:

   ```bash
   sudo docker compose up -d --force-recreate web
   ```

3. Send one controlled relay test from the web service:

   ```bash
   sudo docker compose exec web \
     python manage.py test_smtp_relay <TEST_RECIPIENT_EMAIL>
   ```

4. When the test succeeds, set `EMAIL_NOTIFICATIONS_ENABLED=true` and recreate `web` again.

5. Check application logs if needed:

   ```bash
   sudo docker compose logs --tail=120 web
   ```

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
| `CERTIFICATE_VERIFY_FAILED` | `SMTP_RELAY_HOST` must match certificate SAN/CN; confirm CA chain path and contents |
| STARTTLS unavailable | Exchange connector TLS settings and assigned SMTP certificate |
| Authentication fails | Vault SMTP credentials, account status, Exchange authenticated SMTP settings |
| Sender rejected | `SMTP_FROM_EMAIL` and the service account's Send As / mailbox permissions |
| No workflow recipients | Reviewer role groups, active/disabled status, Django `User.email`, recipient-domain allowlist |

Historical `article_review_notification_queued` audit records are retained for existing data, but new direct delivery does not create queue events.
