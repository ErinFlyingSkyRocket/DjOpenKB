# DjOpenKB bootstrap secret generator

Use these scripts to generate strong local secrets for `vault/bootstrap/djopenkb.env`.

Generated/updated values:

```env
DJANGO_SECRET_KEY=...
POSTGRES_PASSWORD=...
DJANGO_FIELD_ENCRYPTION_KEY=...
LDAP_PLACEHOLDER_PASSWORD=...
```

The scripts preserve existing lines such as `AI_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `LDAP_BIND_PASSWORD`.

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

Use no quotes where possible and keep values in plain `KEY=value` format. Avoid spaces around `=`.

If PostgreSQL has already been initialized with an old password, do not randomly change `POSTGRES_PASSWORD` unless you also change the database password or recreate the database volume.

Do not randomly rotate `DJANGO_FIELD_ENCRYPTION_KEY` on an existing deployment. It is used for encrypted application fields such as MFA-related secrets; changing it without a migration/reset plan can make existing encrypted values unreadable.

If Vault was already seeded, changing `vault/bootstrap/djopenkb.env` alone may not update Vault. Update Vault manually or rerun your Vault init process according to your project setup.
