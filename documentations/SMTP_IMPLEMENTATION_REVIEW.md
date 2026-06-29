# SMTP Relay Notification Implementation Review

## Scope Reviewed

This review covers the SMTP relay notification feature added to DjOpenKB for article-review submissions. It does not change the existing AD/LDAPS authentication, article approval permissions, or public/internal article visibility rules.

## Existing Project Findings

1. DjOpenKB already has separate review scopes:
   - public: `Article Approver`, `Article Manager`, `Admin Users`;
   - internal: `Internal Article Approver`, `Internal Article Manager`, `Admin Users`.
2. The existing approval workflow has clear pending-state transitions for new articles and staged updates to published articles.
3. Existing Active Directory integration maps `mail` to Django `User.email` and uses `AUTH_LDAP_ALWAYS_UPDATE_USER=True`. Accounts without AD `mail` data receive a validated UPN/domain fallback on first successful AD login.
4. There was no SMTP configuration, delivery queue, SMTP worker, or review-notification audit event in this project version.

## Implemented Design

```text
Writer submits or resubmits article
        |
        v
Django saves article / pending update
        |
        v
Transaction on_commit queues Celery task
        |
        v
notification-worker resolves current eligible reviewer roles
        |
        v
One TLS-authenticated SMTP message per reviewer
        |
        +--> append-only ActivityLog status/count event
```

The web request does not connect to SMTP. A broker or SMTP outage cannot reverse a valid article submission.

## Recipient Matrix

| Article scope | Recipient groups | Excluded |
|---|---|---|
| Public | Article Approver, Article Manager, Admin Users | Internal-only reviewers, disabled users, inactive users, main-site-blocked users, blank/invalid email addresses, domains outside the SMTP allowlist |
| Internal | Internal Article Approver, Internal Article Manager, Admin Users | Public-only reviewers, disabled users, inactive users, main-site-blocked users, blank/invalid email addresses, domains outside the SMTP allowlist |

The code also includes a direct Django superuser as an administrative fallback for older accounts that have not yet been normalised into `Admin Users`.

## Security Controls

| Control | Result |
|---|---|
| Vault-only SMTP credentials | The service-account username/password are not in `.env`, Compose, tasks, or logs. |
| TLS required | Startup refuses to enable notifications if neither STARTTLS nor implicit TLS is configured. |
| Certificate validation | The backend uses normal system trust plus an optional mounted AD/private CA file. |
| Recipient domain allowlist | Reviewer email must be within `SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS`. |
| Per-recipient messages | Reviewer membership is not disclosed in headers. |
| Internal-email minimisation | Internal notification emails omit internal titles and content. |
| Current-state check | A task does not send if the item has been approved/rejected before the worker handles it. |
| Retry safety | Only failures before relay connection are retried; post-connection failures are not retried to reduce duplicate email. |
| Audit trail | Queue/sent/failed/skipped state is appended to `ActivityLog`, without recipient addresses or SMTP secrets. |
| Privilege separation | `notification-worker` has no frontend network and no OpenKB content mounts; it only has application/Vault/egress access plus the mounted CA. |

## Operational Preconditions

- Relay is configured to require TLS and to permit the dedicated service account to send only as `SMTP_FROM_EMAIL`.
- The SMTP relay host uses a valid certificate whose hostname matches `SMTP_RELAY_HOST`.
- The internal CA chain is present in `ldap-certs/ad-ca.crt` when required.
- Reviewer accounts are assigned the correct DjOpenKB role groups and have valid organisation-domain email addresses.
- The relay host is reachable from the new `notification-worker`. For a relay running on the same AD server as LDAPS, the existing Compose hostname mapping applies.

## Validation Completed in This Patch Environment

- Python syntax compilation passed for every changed/new Python file.
- Docker Compose YAML parsed successfully and includes the new `notification-worker`.
- Vault bootstrap shell syntax passed (`sh -n`).
- No live SMTP connection test was performed because this patch environment does not have the organisation’s relay hostname, CA certificate, service-account credentials, or an approved recipient mailbox.

## Remaining Deployment Validation

1. Start with `EMAIL_NOTIFICATIONS_ENABLED=false`.
2. Add SMTP relay settings and CA file.
3. Store only `SMTP_RELAY_USERNAME` and `SMTP_RELAY_PASSWORD` in Vault.
4. Enable notifications and recreate `web` and `notification-worker`.
5. Run `python manage.py test_smtp_relay <TEST_RECIPIENT_EMAIL>` inside `notification-worker`.
6. Submit a public and an internal test article and verify the recipient matrix.
7. Inspect `notification-worker` logs and Django Admin activity logs.
