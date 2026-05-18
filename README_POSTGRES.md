# Switch DjOpenKB from SQLite to PostgreSQL

This patch changes Django from SQLite to PostgreSQL. Existing SQLite data is not copied. Running migrations will create fresh Django tables in PostgreSQL.

## 1. Install/update the changed files

Copy these patched files into your project root:

```text
djopenkb/settings.py
docker-compose.yml
Dockerfile
.env.example
```

## 2. Create your local `.env`

If you do not already have a root `.env`, create one from the example:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` and set a stronger `POSTGRES_PASSWORD` and your real API keys if needed.

## 3. Rebuild and start fresh

Because you said the SQLite data does not need to be retained, you can start with a fresh Postgres volume.

```bash
docker compose down -v
docker compose up --build
```

The `web` service will automatically run:

```bash
python manage.py migrate --noinput
python manage.py collectstatic --noinput
```

So your Django tables, including users, sessions, admin tables, and `kb_suggestedarticle`, will be created in PostgreSQL.

## 4. Create a new admin account

Open a new terminal in the project folder:

```bash
docker compose exec web python manage.py createsuperuser
```

## 5. Confirm Django is using PostgreSQL

```bash
docker compose exec web python manage.py dbshell
```

Then inside the Postgres shell:

```sql
\dt
```

You should see tables like `auth_user`, `django_session`, and `kb_suggestedarticle`.

## Notes

- Future article metadata saved through Django will go into PostgreSQL.
- Your OpenKB Markdown files under `openkb-data/raw` and `openkb-data/wiki` are still filesystem files, not database rows.
- Do not commit `.env` because it may contain passwords/API keys.
- To temporarily use SQLite outside Docker, set `USE_SQLITE=true`.
