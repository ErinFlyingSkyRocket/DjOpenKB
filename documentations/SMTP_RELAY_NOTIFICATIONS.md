# SMTP Relay Article-Review Notifications

## Purpose

This feature sends a notification when an article author submits an item for review. It uses the organisation’s SMTP relay and a dedicated SMTP service account stored in Vault.

The application does **not** query Active Directory groups at send time. It uses the existing DjOpenKB role groups in Django, so it matches the authorisation model that already controls each review queue.

| Submitted item | Recipients resolved at delivery time |
|---|---|
| Public new article or public published-article update | `Article Approver`, `Article Manager`, `Admin Users` |
| Internal new article or internal published-article update | `Internal Article Approver`, `Internal Article Manager`, `Admin Users` |

A reviewer in more than one role still receives only one email. Disabled, inactive, main-site-blocked, blank-email, and invalid-email accounts are excluded. A legacy direct Django superuser is included defensively even if its group membership has not yet been normalised.

## Security and Privacy Design

- SMTP credentials are only read from Vault as `SMTP_RELAY_USERNAME` and `SMTP_RELAY_PASSWORD`; they are not placed in `.env`, Compose, source code, task payloads, or activity logs.
- A separate `notification-worker` consumes only the `article_review_notifications` Celery queue. SMTP latency or a relay outage cannot block the author’s article submission page or consume the OpenKB AI worker.
- The worker opens TLS before SMTP authentication. `SMTP_RELAY_USE_TLS=true` is the default STARTTLS configuration for port `587`; implicit TLS is supported only when explicitly configured instead.
- Certificate validation remains enabled. For an internal AD certificate authority, mount its CA certificate under `ldap-certs/` and set `SMTP_RELAY_CA_CERT_FILE`; do not disable verification.
- Each email is sent to one recipient only. Reviewers cannot see other reviewer email addresses through `To`, `Cc`, or `Bcc`.
- Email contains no article body. Internal article notifications also omit the internal title because relay logs and inboxes are outside the internal article page’s access checks.
- Reviewer addresses must match the explicit `SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS` allowlist. This prevents a role account with an unexpected external address from receiving review links.
- Delivery audits contain only event status/counts, scope, and reason codes. Recipient addresses and SMTP credentials are never written to `ActivityLog`.
- A queued notification is suppressed if an approver resolves the item before the worker processes it.
- Only failures before opening the SMTP connection are retried. A failure after the connection is open is recorded but not retried automatically, because the relay could have accepted the message before reporting an error and a retry could duplicate mail.

## When an Email Is Queued

| Workflow action | Email queued? |
|---|---:|
| Writer saves a new draft | No |
| Writer submits a new article | Yes |
| Writer resubmits a draft or pending-failed article | Yes |
| Writer saves a private draft of a published-article update | No |
| Writer submits a published-article update | Yes |
| Writer edits an already-pending item | No |
| Reviewer saves edits while keeping an item pending | No |
| Admin publishes directly | No |
| Admin approves/rejects an item | No |

## Prerequisites

1. The SMTP relay must permit the dedicated service account to authenticate and send using `SMTP_FROM_EMAIL`.
2. The relay must offer TLS:
   - normally `STARTTLS` on TCP `587`; or
   - implicit TLS only if the relay administrator explicitly specifies it.
3. Use the relay DNS name that appears in the relay certificate. Do not configure a raw server IP for a production TLS connection.
4. The `notification-worker` must be able to resolve and reach that relay host. When the relay runs on the same AD host already configured for LDAPS, the existing Docker hostname mapping is reused.
5. Every reviewer who should receive mail must have a valid address in their Django `User.email` field and its domain must be in `SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS`. AD accounts normally receive an email address from LDAP/UPN on first successful login. Verify local accounts and accounts whose AD `mail` data is incomplete.

## Configuration

### 1. Add the non-secret values to `.env`

Merge the following values into the existing `.env`. Keep notifications disabled until the relay test succeeds.

```dotenv
EMAIL_NOTIFICATIONS_ENABLED=false
SMTP_RELAY_HOST=<SMTP_RELAY_FQDN>
SMTP_RELAY_PORT=587
SMTP_RELAY_USE_TLS=true
SMTP_RELAY_USE_SSL=false
SMTP_RELAY_TIMEOUT_SECONDS=10
SMTP_RELAY_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/ad-ca.crt
SMTP_FROM_EMAIL=knowledge-repository@company.example
SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS=company.example
SITE_BASE_URL=https://<PUBLIC_HOSTNAME>
EMAIL_SUBJECT_PREFIX=[Knowledge Repository]

ARTICLE_REVIEW_NOTIFICATION_CELERY_QUEUE=article_review_notifications
ARTICLE_REVIEW_NOTIFICATION_WORKER_CONCURRENCY=1
ARTICLE_REVIEW_NOTIFICATION_MAX_RETRIES=3
ARTICLE_REVIEW_NOTIFICATION_RETRY_DELAY_SECONDS=30
```

`SITE_BASE_URL` must be the exact HTTPS browser origin, with no path, query string, or trailing slash. Examples:

```dotenv
SITE_BASE_URL=https://<INTERNAL_SERVER_IP>:8080
# or, after final DNS and perimeter HTTPS are in place:
SITE_BASE_URL=https://<PUBLIC_HOSTNAME>
```

Use exactly one transport:

```dotenv
# Typical secure SMTP submission:
SMTP_RELAY_USE_TLS=true
SMTP_RELAY_USE_SSL=false
```

```dotenv
# Only when the relay explicitly requires implicit TLS, commonly on 465:
SMTP_RELAY_PORT=465
SMTP_RELAY_USE_TLS=false
SMTP_RELAY_USE_SSL=true
```

### 2. Make the relay CA available to containers

Place the trusted issuing CA certificate in the existing host folder:

```bash
sudo install -d -m 0755 ldap-certs
sudo install -m 0644 <AD_CA_CERT_FILE_ON_HOST> ldap-certs/ad-ca.crt
```

The `web` and `notification-worker` containers mount this directory read-only at `/etc/ssl/certs/djopenkb-ldap/`.

### 3. Add only the SMTP secrets to Vault

For an **existing deployment**, do **not** copy the full example file containing placeholder values. Create a minimal temporary bootstrap file containing only the two new values:

```bash
sudo nano vault/bootstrap/djopenkb.env
```

```dotenv
SMTP_RELAY_USERNAME=svc_djopenkb_mail@ad.example.com
SMTP_RELAY_PASSWORD=<SMTP_SERVICE_ACCOUNT_PASSWORD>
```

The patched Vault bootstrap script preserves existing stored secrets when a temporary bootstrap file leaves them blank. It will not print these values.

Seed the values, then remove the temporary local file:

```bash
sudo docker compose up -d --force-recreate vault-init
sudo rm -f vault/bootstrap/djopenkb.env
```

For a brand-new deployment, start from `vault/bootstrap/djopenkb.env.example` and provide all first-time required secrets.

### 4. Build, start, and test before enabling notifications

Deploy the patched code, keeping `EMAIL_NOTIFICATIONS_ENABLED=false` initially:

```bash
git pull
sudo docker compose up -d --build
```

After the Vault values and CA certificate are in place, change this in `.env`:

```dotenv
EMAIL_NOTIFICATIONS_ENABLED=true
```

Then recreate the application and notification services:

```bash
sudo docker compose up -d --build --force-recreate web notification-worker
```

Send one test email from the actual notification worker:

```bash
sudo docker compose exec notification-worker \
  python manage.py test_smtp_relay <TEST_RECIPIENT_EMAIL>
```

Check worker status and logs:

```bash
sudo docker compose ps
sudo docker compose logs --tail=120 notification-worker
```

## Expected Behaviour

- Public writers submit to the public review queue; only public approvers/managers and admins receive mail.
- Internal writers submit to the internal review queue; only internal approvers/managers and admins receive mail.
- The author is not added as a recipient merely because they submitted an article.
- Direct publication by an admin does not emit review mail.
- Missing recipient email addresses do not stop the article workflow; the account is skipped.
- If there are no eligible recipients, the item remains pending and an audit event records the skip.
- If the broker or relay is down, the article remains pending. Queue/connection failures are visible in logs and the append-only activity log.

## Verification Checklist

1. Use the SMTP test command with a mailbox controlled by an administrator.
2. Submit one public draft as an `Article Writer`.
3. Confirm that only accounts in `Article Approver`, `Article Manager`, and `Admin Users` receive individual emails.
4. Submit one internal draft as an `Internal Article Writer`.
5. Confirm that only accounts in `Internal Article Approver`, `Internal Article Manager`, and `Admin Users` receive individual emails.
6. Confirm that no internal article title or body appears in the internal notification email.
7. Inspect Django Admin → Activity logs for queued/sent/skipped/failed notification events.
8. Test a reviewer with a blank or invalid email address; the article must still submit successfully and the account must not be emailed.

## Troubleshooting

| Symptom | Check |
|---|---|
| `EMAIL_NOTIFICATIONS_ENABLED=true requires ...` during startup | Required SMTP non-secret values are missing from `.env`, or the SMTP username/password were not seeded into Vault. |
| Certificate error | Use the relay FQDN from its certificate, ensure the CA chain file is mounted, and keep TLS verification enabled. |
| Timeout / connection refusal | Confirm the relay host/port, firewall path from Docker `egress`, and the Docker hostname mapping if this AD host is not resolvable from Linux. |
| Authentication rejected | Confirm `SMTP_RELAY_USERNAME`, `SMTP_RELAY_PASSWORD`, and that the relay grants the service account permission to send as `SMTP_FROM_EMAIL`. |
| No email for a reviewer | Verify the account is active, not `Disabled User`, can access the main site, has a valid `User.email`, and has the matching scope’s reviewer role. |
| Duplicate notification | Check for duplicate message acceptance in the relay. The worker intentionally avoids retrying individual post-connection send failures to reduce duplicate-mail risk. |
| Worker is not consuming | Run `sudo docker compose ps` and inspect `notification-worker` logs. Confirm `ARTICLE_REVIEW_NOTIFICATION_CELERY_QUEUE` has the same value for `web` and `notification-worker`. |
