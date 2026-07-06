# SMTP Review Notification Implementation Review

## Current Design

DjOpenKB uses direct post-commit SMTP delivery for low-volume article review notifications.

```text
Writer submits or resubmits article
        |
        v
Django commits the article / pending update
        |
        v
Django resolves current eligible reviewer role groups
        |
        v
One TLS-authenticated Bcc SMTP message through Exchange
        |
        +--> append-only ActivityLog status/count event
```

No notification Celery queue or standalone `notification-worker` is used. The separate `ai-worker` remains responsible only for OpenKB AI jobs.

## Recipient Matrix

| Article scope | Recipient groups | Excluded |
|---|---|---|
| Public | Article Approver, Article Manager, Admin Users | Internal-only reviewers, disabled users, inactive users, main-site-blocked users, blank/invalid addresses, domains outside the allowlist |
| Internal | Internal Article Approver, Internal Article Manager, Admin Users | Public-only reviewers, disabled users, inactive users, main-site-blocked users, blank/invalid addresses, domains outside the allowlist |

Recipient eligibility is strict: direct Django superuser status alone is not enough. An administrator receives reviewer notifications only through the current `Admin Users` role, which is the source of truth for DjOpenKB administrator access.

Approval and Pending-failed outcome email is direct to the current article owner only. It is skipped when the owner is inactive, assigned to `Disabled User`, blocked from the main site, missing a valid allowed-domain address, or is the reviewer who completed their own review.

## Security Controls

| Control | Result |
|---|---|
| Vault-only credentials | SMTP username/password are not in `.env`, Compose, source code, or logs. |
| TLS required | Startup rejects enabled notifications unless STARTTLS or implicit TLS is configured. |
| Certificate validation | The SMTP backend validates the relay certificate and hostname. It begins with the web container's normal trust store and can add one mounted public trust certificate from `SMTP_RELAY_CA_CERT_FILE`. |
| Strict role validation | Reviewer notification recipients must hold the matching current DjOpenKB role: public approver/manager/admin for public items, or internal approver/manager/admin for internal items. |
| Recipient domain allowlist | A reviewer or article-owner email must be within `SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS`. |
| Bcc-only group message | One relay message is submitted per review event without exposing reviewer membership in headers. |
| Internal-email minimisation | Internal notification emails omit internal titles and content. |
| Post-commit execution | Email is attempted only after a valid submission has committed. |
| Workflow availability | SMTP failure is caught, audited, and does not roll back the pending article. |
| Audit trail | Sent, failed, and skipped outcomes are appended without recipient addresses or SMTP secrets. |

## Operational Preconditions

- The Exchange relay requires TLS and permits the SMTP service account to send as `SMTP_FROM_EMAIL`.
- `SMTP_RELAY_HOST` is the certificate hostname, not an IP address.
- When Exchange uses a private CA or self-signed certificate, `SMTP_RELAY_CA_CERT_FILE` points to a mounted public PEM/CRT trust certificate. A CA-issued certificate already trusted by the container can leave it blank.
- The Django web service can resolve and reach the Exchange SMTP endpoint.
- Intended reviewer accounts have valid organisation-domain values in `User.email` and correct DjOpenKB role groups.

## Validation Performed for This Patch

- Python syntax compilation of modified code.
- Django notification regression tests updated for direct post-commit Bcc delivery.
- Docker Compose structure checked after removing the standalone notification worker.
- Vault bootstrap shell syntax checked.

A live SMTP test remains environment-specific and must be run against the authorised Exchange relay and a controlled mailbox.

## Deployment Validation

1. Keep `EMAIL_NOTIFICATIONS_ENABLED=false` while the Exchange endpoint, certificate hostname, and TLS trust certificate are prepared.
2. Store `SMTP_RELAY_USERNAME` and `SMTP_RELAY_PASSWORD` only in Vault.
3. Place the public Exchange CA/chain or exact self-signed Exchange certificate at `ldap-certs/exchange-smtp.crt`, configure `SMTP_RELAY_CA_CERT_FILE`, and verify the file is readable in `web`.
4. Deploy with `docker compose up -d --build --remove-orphans`, then run `python manage.py test_smtp_relay <TEST_RECIPIENT_EMAIL>` inside `web`.
5. Enable notifications and recreate `web`.
6. Submit one public and one internal test article and verify the recipient matrix and internal-content minimisation.
7. Inspect Django Admin activity logs and `docker compose logs web`.
