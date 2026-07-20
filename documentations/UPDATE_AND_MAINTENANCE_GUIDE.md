# DjOpenKB Code, Dependency, and Configuration Update Guide

This guide is for updating an existing DjOpenKB deployment after the initial installation is complete.

Use `documentations/DEPLOYMENT_GUIDE.md` for a fresh server deployment. The normal deployed project directory used below is:

```text
/opt/DjOpenKB
```

## 1. Recommended update method: VS Code, Git push, then Git pull

This is the recommended method for normal feature, code, template, documentation, and dependency updates.

### 1.1 Make and push the changes from the development computer

Edit the required files in VS Code. If a Python package version must change, update the required version in:

```text
requirements.txt
```

Then review and push the changes:

```bash
git status
git add .
git commit -m "Describe the update"
git push
```

Keep dependency versions controlled in `requirements.txt` rather than relying on an unpinned latest version. Update dependencies deliberately and test the application after rebuilding.

### 1.2 Pull and deploy the latest code on the Linux server

Connect to the Linux server and run:

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

Confirm that the log reports that the DjOpenKB secret was seeded successfully. Then immediately remove the temporary plaintext bootstrap file:

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

## 7. Quick update reference

| Change | Normal action |
|---|---|
| Python/Django code, templates, static files | Pull/edit the files, then `docker compose down` and `docker compose up -d --build` |
| `requirements.txt` or Docker build files | Rebuild with `docker compose up -d --build` |
| `.env` only | Recreate the stack with `docker compose down` and `docker compose up -d` |
| Vault secret | Apply the temporary bootstrap update, remove the bootstrap file, then restart the stack |
| Nginx configuration | Recreate/restart the stack; rebuild only if another image-based change also requires it |
| Documentation only | No application rebuild is required unless the documentation is served from the deployed application image |
| Accidental Admin IP allowlist lockout | From the server, run `sudo docker compose exec web python manage.py reset_admin_ip_allowlist`, configure a new allowlist in Site settings, then re-enable the allowlist |

After any application update, confirm:

```bash
cd /opt/DjOpenKB
sudo docker compose ps
sudo docker compose exec web python manage.py check
sudo docker compose logs --tail=120 web
```

For fresh installation, first administrator creation, certificates, LDAPS, OpenKB initialization, and server reboot persistence, use `documentations/DEPLOYMENT_GUIDE.md`.
