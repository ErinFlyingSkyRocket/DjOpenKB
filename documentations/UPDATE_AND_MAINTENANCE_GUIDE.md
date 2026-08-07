# DjOpenKB Code, Dependency, and Configuration Update Guide

This guide is for updating an existing DjOpenKB deployment after the initial installation is complete.
Use [CONFIGURATION_REFERENCE.md](CONFIGURATION_REFERENCE.md) to confirm configuration ownership and restart requirements before changing a value.

Use `documentations/DEPLOYMENT_GUIDE.md` for a fresh server deployment. The normal deployed project directory used below is:

```text
/opt/DjOpenKB
```

## 1. Recommended update method: development computer → Git push → server Git pull

This is the recommended method for normal feature, code, template, documentation, and dependency updates.

### 1.1 What is required on the development computer

Git is the tool used to push and pull project updates. Docker Desktop is **not required** just to update the Git repository.

A normal Windows development computer can use:

- **VS Code** to edit the project files.
- **Git for Windows** to run `git pull`, `git status`, `git commit`, and `git push`.
- **GitHub Desktop** as an optional graphical alternative to Git command-line operations.
- **Docker Desktop** only when the project needs to be built or tested locally with Docker containers before the update is pushed.

The normal update path is therefore:

```text
Development computer
        ↓
Edit and test the project
        ↓
Git commit and push
        ↓
Git repository
        ↓
Linux server git pull
        ↓
Docker Compose rebuild/restart
```

Before editing an existing local project copy, first make sure it is up to date:

```bash
git status
git pull
```

If the local working tree contains unfinished changes, review or commit them before pulling.

### 1.2 Make and push changes from the development computer

Open the local DjOpenKB project folder in VS Code and edit the required files.

If a Python package version must change, update the required version in:

```text
requirements.txt
```

Review the changes before pushing:

```bash
git status
git diff
```

Then commit and push them:

```bash
git add .
git commit -m "Describe the update"
git push
```

The project `.gitignore` excludes normal local/runtime files such as `.env`, Vault runtime files, generated data, and private certificate material. Even so, always review `git status` before committing to make sure no sensitive or unintended file has been staged.

#### Optional GitHub Desktop workflow

Instead of using Git commands, the same development-side workflow can be completed with GitHub Desktop:

1. Open the DjOpenKB repository in GitHub Desktop.
2. Use **Fetch origin** and then **Pull origin** before starting work.
3. Edit the files in VS Code.
4. Return to GitHub Desktop and review the changed files.
5. Enter a commit summary and select **Commit to main**.
6. Select **Push origin**.

GitHub Desktop and Git command-line operations perform the same repository update process. Use one approach consistently for a given update to avoid unnecessary confusion.

#### Optional local Docker testing

Docker Desktop is useful only when the development computer needs to run the Docker Compose stack locally before pushing an update.

Typical local testing may include:

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=120 web
```

Stop the local test stack when finished:

```bash
docker compose down
```

Do not use `docker compose down -v` unless the local test data is intentionally being deleted.

Local Docker Desktop testing is optional. The production Linux server still performs its own Docker image rebuild after pulling the committed update.

Keep dependency versions controlled in `requirements.txt` rather than relying on an unpinned latest version. Update dependencies deliberately and test the application after rebuilding.

### 1.3 Pull and deploy the latest code on the Linux server

After the update has been pushed successfully, connect to the Linux server and run:

```bash
cd /opt/DjOpenKB
git status
git pull
```

If `git status` shows unexpected local changes, review them before pulling. Local edits can conflict with changes from Git.

For a full controlled application update, stop the current Compose stack, rebuild the images, and start it again:

```bash
sudo docker compose down
sudo docker compose up -d --build
sudo docker compose ps
```

`docker compose down` does not delete the persistent project data used by this deployment. Do not use `docker compose down -v` for normal updates because `-v` also removes named Docker volumes.

Check the main services after the update:

```bash
sudo docker compose logs --tail=120 web
sudo docker compose logs --tail=120 ai-worker
sudo docker compose logs --tail=120 nginx
```

Then run the Django checks:

```bash
sudo docker compose exec web python manage.py check
sudo docker compose exec web python manage.py migrate --noinput
```

The current `web` container startup already runs migrations and static-file collection, so the migration command above is mainly a confirmation that the database is fully up to date.

---

## 2. Manual server-edit method

For a small emergency or controlled change, a file can be edited directly on the Linux server.

Move to the project directory:

```bash
cd /opt/DjOpenKB
```

Edit the required file, for example:

```bash
sudo nano requirements.txt
```

or:

```bash
sudo nano <PATH_TO_FILE>
```

After saving the change, rebuild and restart the stack:

```bash
sudo docker compose down
sudo docker compose up -d --build
sudo docker compose ps
```

Source code, templates, static files, Python dependencies, and Docker build files are copied into the application image. Therefore, `docker compose restart web` alone does not load newly edited host source files; rebuild the image after these changes.

Direct server edits should be used carefully. They leave the deployed Git working tree different from the remote repository and may cause a later `git pull` conflict. When the change is permanent, apply the same change to the main source repository and push it to Git.

---

## 3. Updating `requirements.txt`

Edit the dependency version in:

```text
/opt/DjOpenKB/requirements.txt
```

For example:

```bash
cd /opt/DjOpenKB
sudo nano requirements.txt
```

After changing a dependency version, rebuild the application images:

```bash
sudo docker compose down
sudo docker compose up -d --build
```

Confirm the required package version inside the running web container when needed:

```bash
sudo docker compose exec web python -m pip show Django
```

Use the same approach for other Python packages. Update one dependency set at a time where practical so that any compatibility issue is easier to identify.

---

## 4. Updating non-secret `.env` settings

Edit the deployment environment file:

```bash
cd /opt/DjOpenKB
sudo nano .env
```

The `.env` file is for non-secret runtime configuration. Passwords, API keys, the Django secret key, and other protected secrets belong in Vault.

After changing only `.env` values, recreate the stack so the containers receive the updated environment:

```bash
sudo docker compose down
sudo docker compose up -d
```

If source code or dependencies were changed at the same time, use:

```bash
sudo docker compose down
sudo docker compose up -d --build
```

---

## 5. Updating Vault secrets

Use the existing Vault bootstrap mechanism only when a stored secret must be added or rotated, such as:

- `AI_API_KEY`
- `LDAP_BIND_PASSWORD`
- `SMTP_RELAY_USERNAME`
- `SMTP_RELAY_PASSWORD`

Do not place these values in `.env`.

### 5.1 Create a temporary update file

Create the temporary bootstrap file directly and include only the secret values that need to change:

```bash
cd /opt/DjOpenKB
sudo nano vault/bootstrap/djopenkb.env
```

Example for an AI API key change:

```env
AI_API_KEY='new-api-key'
```

Example for an SMTP credential change:

```env
SMTP_RELAY_USERNAME='service-account@example.local'
SMTP_RELAY_PASSWORD='new-password'
```

Existing Vault values that are not provided in this temporary file are preserved by the current Vault initialization script.

Protect the temporary file:

```bash
sudo chmod 600 vault/bootstrap/djopenkb.env
```

### 5.2 Apply the Vault update

Run the one-time Vault initialization service again:

```bash
sudo docker compose up -d --force-recreate vault-init
sudo docker compose logs --tail=120 vault-init
```

Confirm that the log reports that the DjOpenKB secret was seeded successfully and ends with `Vault is ready for DjOpenKB.` The one-shot `vault-init` service normally exits after this work. It reuses the existing valid static read-only application token and does not rotate it for an ordinary secret update. `vault-auto-unseal` is unseal-only.

A warning that Vault did not return complete metadata for a replacement token is non-fatal only when the log explicitly says the existing valid token was preserved and then reports `Vault is ready for DjOpenKB.` Do not delete Vault key files simply to remove that warning.

Then immediately remove the temporary plaintext bootstrap file:

```bash
sudo rm -f vault/bootstrap/djopenkb.env
```

Restart the stack so the application services load the current Vault token and updated secret values:

```bash
sudo docker compose down
sudo docker compose up -d
```

If code or dependency changes are being deployed at the same time, use:

```bash
sudo docker compose down
sudo docker compose up -d --build
```

### 5.3 Secrets that should not be casually rotated

Do not change these as part of a routine update without a planned migration or recovery procedure:

```text
DJANGO_FIELD_ENCRYPTION_KEY
POSTGRES_PASSWORD
```

Changing `DJANGO_FIELD_ENCRYPTION_KEY` can make existing encrypted application data, including stored MFA secrets, unreadable unless the data is re-encrypted correctly.

For an existing PostgreSQL database, changing only the Vault `POSTGRES_PASSWORD` value does not automatically change the database user's password inside PostgreSQL.

Keep `DJANGO_SECRET_KEY` stable unless there is a deliberate reason to rotate it and the effect on active sessions and related security data has been considered.

Do not manually replace `vault/keys/djopenkb-app-token.txt` during a routine secret update. Token validation/replacement belongs to `vault-init`; the accessor and pending-revocation queue must remain root-only.

---

## 6. Emergency Admin IP Allowlist Recovery

The Django Admin IPv4/IPv6 allowlist is managed dynamically from **Site settings** and is disabled by default. Once enabled, it uses an **implicit-deny** policy: only configured IP addresses or CIDR ranges can proceed to the Admin authentication checks.

If an administrator accidentally removes their own address/range, enables an incorrect list, or wants to completely discard the current allowlist and start again, recover/reset it directly from the Linux server:

> **Warning:** this reset permanently clears every saved Admin IPv4/IPv6 address and CIDR range.

```bash
cd /opt/DjOpenKB
sudo docker compose exec web python manage.py reset_admin_ip_allowlist
```

Expected behaviour:

- The Admin IP allowlist toggle is disabled immediately.
- All existing configured IPv4/IPv6/CIDR entries are permanently cleared.
- Source-IP filtering returns to the default unrestricted state.
- Normal login, superuser permissions, normal MFA, and Admin MFA are still required.

After recovery, sign in normally, configure a new allowlist under:

```text
Django Admin → Site settings → Django Admin access restrictions
```

Then re-enable **Admin IP allowlist** only after confirming that the current management IP or management CIDR is included.

This recovery does **not** require editing `.env` or `nginx/nginx.conf`, and it does not require keeping a permanent emergency IP allowlist.

### Recovery command quick check

If the allowlist is already disabled and the stored IP/CIDR list is already empty, the command reports that it is already fully reset. It is therefore safe to run as a recovery check when the current allowlist state is uncertain.

---

## 7. Clear a stale LDAP DN cache entry for one AD user

Use this targeted maintenance action when one Active Directory login alias works but another alias for the same user fails, for example:

```text
alice@<AD_DOMAIN> works
alice fails
```

The most common cause is a stale `django-auth-ldap` distinguished-name lookup retained in Redis for one username format. This can appear after an AD username, UPN, email, or OU change; an LDAP search-base/filter change; or a deployment that changed LDAP normalisation while Redis retained earlier cache entries.

Do not clear all Redis data. Clear only the affected user's LDAP DN cache:

```bash
cd /opt/DjOpenKB
chmod +x scripts/clear_ldap_dn_cache.sh
./scripts/clear_ldap_dn_cache.sh alice
```

The script:

- clears only `django-auth-ldap` DN-cache keys associated with the supplied username and linked email/UPN;
- leaves sessions, MFA state, application lockouts, rate limits, Celery data, and AI jobs unchanged; and
- does not require a container restart or Docker rebuild.

Provide extra aliases only when the Django user record does not yet contain the expected email/UPN:

```bash
./scripts/clear_ldap_dn_cache.sh \
  alice \
  alice@<AD_DOMAIN>
```

Then retry the normal AD login. To verify the account lookup and password independently:

```bash
sudo docker compose exec -it web \
  python manage.py test_ldap_auth alice --auth
```

Do not reset the user's email-server cache or AD password unless the diagnostic specifically shows that Active Directory rejected the password. Do not use `redis-cli FLUSHALL`, remove the Redis volume, or run `docker compose down -v` for an account-specific DN-cache issue.

---

## 8. Quick update reference

| Change | Normal action |
|---|---|
| Python/Django code, templates, static files | Pull/edit the files, then `docker compose down` and `docker compose up -d --build` |
| `requirements.txt` or Docker build files | Rebuild with `docker compose up -d --build` |
| `.env` only | Recreate the stack with `docker compose down` and `docker compose up -d` |
| Vault secret | Apply the temporary bootstrap update, remove the bootstrap file, then restart the stack |
| Nginx configuration | Recreate/restart the stack; rebuild only if another image-based change also requires it |
| Documentation only | No application rebuild is required unless the documentation is served from the deployed application image |
| Site setting only | Save it in Django Admin; request-rate and AI-quota caches are cleared by the model save path where applicable |
| Accidental Admin IP allowlist lockout | From the server, run `sudo docker compose exec web python manage.py reset_admin_ip_allowlist`, configure a new allowlist in Site settings, then re-enable the allowlist |
| One AD login alias works but another fails | Run `./scripts/clear_ldap_dn_cache.sh alice`, then retry the AD login; no rebuild is required |

After any application update, confirm:

```bash
cd /opt/DjOpenKB
sudo docker compose ps
sudo docker compose exec web python manage.py check
sudo docker compose logs --tail=120 web
```

## 9. Post-update verification by change type

Always start with:

```bash
cd /opt/DjOpenKB
sudo docker compose config >/dev/null
sudo docker compose ps
sudo docker compose exec web python manage.py check
sudo docker compose exec web python manage.py check --deploy
sudo docker compose exec web python manage.py migrate --noinput
python scripts/verify_supply_chain_pins.py
```

Run the complete suite after broad application changes:

```bash
sudo docker compose exec web python manage.py test kb.tests
```

For a focused change, run the matching functional package:

```bash
# Authentication, MFA, LDAP identity, lockouts and account validation
sudo docker compose exec web python manage.py test \
  kb.tests.auth \
  kb.tests.users \
  kb.tests.admin

# Article workflow, visibility, permissions, rendering and limits
sudo docker compose exec web python manage.py test \
  kb.tests.articles \
  kb.tests.permissions

# Persistent New Article checkpoint, leave/discard workflow and image lifecycle
sudo docker compose exec web python manage.py test \
  kb.tests.articles.test_article_creation_workspace

# Existing-article manual edit/review, shared update staging and approval precedence
sudo docker compose exec web python manage.py test \
  kb.tests.articles.test_article_edit_workspace

# Uploaded media and bulk ZIP import protections
sudo docker compose exec web python manage.py test \
  kb.tests.media \
  kb.tests.bulk_import

# Request, browser, cookie, Nginx and input security controls
sudo docker compose exec web python manage.py test kb.tests.security

# OpenKB AI jobs, quotas, messages and distributed locks
sudo docker compose exec web python manage.py test kb.tests.ai
```

The detailed test layout and single-module examples are maintained in `kb/tests/README.md`.

After a role/workflow update, test both the visible control and a forged POST request. Static JavaScript/CSS is served with Nginx revalidation (`expires -1` / `Cache-Control: no-cache`), so normal navigation/reload should validate the current asset rather than reuse a seven-day-fresh copy; use a hard refresh only as troubleshooting, not as a deployment requirement. After a Vault update, check token/accessor permissions without printing either value.

After changing the new-article workspace or image lifecycle, manually verify these paths in a non-production account:

1. Open New Article, change one field, click an in-site Back/navbar link, and confirm navigation is stopped before redirecting. Verify that clicking the backdrop, pressing Escape, or looking for a close icon cannot dismiss the prompt.
2. Choose **Keep checkpoint and continue**, confirm the originally selected page/action continues, then reopen New Article and confirm the title, body, keywords, visibility, and uploaded images are restored. Confirm no Draft appears in My Articles and no reviewer email is sent.
3. Upload an image without saving an article, choose **Discard and continue**, and confirm the workspace and file are removed before navigation continues. Reopen New Article and confirm it is blank.
4. Change fields again, use **Reset article**, confirm the warning, and verify the page reloads with a new blank workspace and no uncommitted images. Confirm ordinary browser refresh restores rather than clears the checkpoint.
5. Use the normal **Save draft** button and confirm a real Draft appears in My Articles. Use **Submit for approval** separately and confirm the article enters the matching pending queue and the existing SMTP reviewer notification is still issued. Confirm checkpoint autosave and Save draft do not send reviewer notifications.
6. After autosave completes, type another same-site URL directly into the address bar, leave the page, and reopen New Article to confirm recovery. Repeat while a save is still pending and confirm the browser-native unsaved-changes warning appears.
7. Open New Article in two tabs. Save from one tab, then attempt to save or discard from the older tab. Confirm the older tab receives the checkpoint-conflict message and cannot replace or remove the newer checkpoint. Reload the older tab and confirm it restores the latest content.
8. Set a checkpoint and its image timestamps to an old date in a development environment, run cleanup, and confirm the checkpoint and owned image remain. Create a genuinely unreferenced test upload and confirm only that orphan is listed/deleted. Preview cleanup before deleting anything:

```bash
sudo docker compose exec web python manage.py cleanup_stray_upload_files --dry-run
```


9. Verify account-state cleanup separately: set a test user inactive and confirm its workspaces/articles remain; assign `Disabled User` and confirm they remain; then permanently delete another test user and confirm its New Article/edit workspaces, saved Draft/Pending/Pending-failed articles, unpublished staged update copy, private image files, checkpoint upload/activity rows, and sessions are removed while already-published knowledge remains as an orphan with author snapshots. Run:

```bash
sudo docker compose exec web python manage.py test kb.tests.users.test_user_account_deletion_cleanup
```

After changing the existing-article edit/review workflow, manually verify these paths:

1. Open an existing Draft or published article, type changes, leave without clicking a workflow button, and reopen it. Confirm the unsaved text was **not** restored. Existing articles do not autosave.
2. For a published article owner, click **Save draft** and confirm the public article remains unchanged, `update_status=NONE`, the item is absent from Manage Pending, and no reviewer notification is sent. Reopen the article as the owner and confirm the staged update draft is available.
3. Submit that update and confirm it becomes `PENDING`, appears in the matching review queue, and sends the normal reviewer notification. Then open it as the owner and click **Save draft** again; confirm it retracts from the queue to `NONE`, clears submitted/reviewed timing state, and leaves Manage Pending until resubmitted.
4. While a published article has an `UpdateStatus.NONE` staged copy, open the article from normal Edit as a matching Manager/Admin. Confirm the same staged title/body is loaded and no separate manager personal draft is created.
5. As a Manager/Admin on a published article, test the single **Save** action with each status. Confirm Draft keeps the main article Published while storing staged `NONE`; Pending keeps the main article Published and stores `PENDING`; Pending failed keeps the main article Published and stores `FAILED` with required comments; Published applies the form content to the live article and clears `pending_update_*`. Confirm the model no longer raises "Only published articles can store an unpublished update draft."
6. For a Manager/Admin who is also the article owner, confirm **Revert to last published version** remains available and clears the staged update. Confirm a non-owner Manager/Admin does not receive a separate per-user draft/revert store.
7. For a reviewer, select Keep pending, Approve/Publish, and Pending failed in separate tests. Confirm changing the dropdown alone does not apply the decision; only the final review action changes shared workflow state and notifications. Confirm an ordinary published article with no Pending/Failed review state returns 404 when `editor_mode=review` is forged.
8. Submit a Pending copy, record its `review_submission_snapshot`, then let a Manager/Admin edit the already-Pending copy from normal Edit and save it as Pending again. Confirm the shared pending content changes but the stored submission snapshot does not; **Reset to user-submitted version** must still restore the original submission baseline.
9. Test approval precedence by opening a published article editor, approving/publishing a newer version elsewhere, then submitting the stale editor. Confirm the stale operation is rejected. Modify/add any browser approval timestamp field and confirm it cannot bypass the check because the server workspace snapshot is authoritative.
10. Upload images while editing and confirm the final article can reference only its own committed images plus uploads owned by that exact edit workspace. Confirm copying another article's managed upload URL is rejected. Test 25%-200% image sizing/manual dimensions and verify video preview width matches the final Admin-configured published width.
11. Verify keyword suggestions with unusual characters such as `<`, `>`, quotes, and `</script>` remain data and cannot break out of the JSON script element.
12. After a static JavaScript/CSS deployment, reload normally and confirm the browser revalidates the asset. Verify the Nginx static block still uses `expires -1`.
13. Run stray cleanup in dry-run mode and confirm images referenced by active New Article workspaces, staged published updates, review snapshots, or committed articles are not listed as stray.

Run the focused regression modules when these areas change:

```bash
sudo docker compose exec web python manage.py test \
  kb.tests.articles.test_article_edit_workspace \
  kb.tests.security.test_keyword_suggestion_json \
  kb.tests.security.test_nginx_admin_and_bulk_limits \
  kb.tests.users.test_user_account_deletion_cleanup \
  kb.tests.users.test_user_author_snapshot_signal
```

## 10. Documentation synchronisation checklist

When implementation changes, update the smallest relevant existing documents rather than creating overlapping guides:

| Change area | Documentation to review |
|---|---|
| User roles, article workflow, editor/display, feature behaviour | `FULL_FEATURE_DOCUMENTATION.md` and README summary |
| `.env`, Vault keys/secrets, Site settings, Nginx values, restart rules | `CONFIGURATION_REFERENCE.md` |
| First installation or operational commands | `DEPLOYMENT_GUIDE.md` |
| Update/recovery workflow | This guide |
| Network/security controls or residual risks | `PUBLIC_EXPOSURE_HARDENING.md` |
| LDAP/LDAPS | `LDAP_LDAPS_SETUP.md` and Windows test guide where applicable |
| SMTP notifications | `SMTP_RELAY_NOTIFICATIONS.md` |

Before packaging documentation, search for stale defaults such as article body size, image count, MFA timeout, session timeout, Vault token mode, request-rate values, and role names. Do not document a browser-only restriction as a server security boundary, and keep known production follow-up items separate from implemented controls.

For fresh installation, first administrator creation, certificates, LDAPS, OpenKB initialization, and server reboot persistence, use `documentations/DEPLOYMENT_GUIDE.md`.
