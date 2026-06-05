# DjOpenKB bootstrap secret generator

Use these scripts to generate strong local secrets for `vault/bootstrap/djopenkb.env`.

Generated/updated values:

```env
DJANGO_SECRET_KEY="..."
POSTGRES_PASSWORD="..."
```

The scripts preserve existing lines such as `GEMINI_API_KEY` or `LDAP_BIND_PASSWORD`.

## Windows PowerShell

From the project root:

```powershell
powershell -ExecutionPolicy Bypass -File vault/bootstrap/generate-secrets.ps1
```

Or run the helper batch file:

```bat
vault\bootstrap\generate-secrets.bat
```

## Linux/macOS/Git Bash

From the project root:

```sh
sh vault/bootstrap/generate-secrets.sh
```

## Important

`vault/bootstrap/djopenkb.env` contains real secrets. Do not commit, upload, or submit it.

If PostgreSQL has already been initialized with an old password, do not randomly change `POSTGRES_PASSWORD` unless you also change the database password or recreate the database volume.

If Vault was already seeded, changing `vault/bootstrap/djopenkb.env` alone may not update Vault. Update Vault manually or rerun your Vault init process according to your project setup.
