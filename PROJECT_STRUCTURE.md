# DjOpenKB Project Structure

This file gives a quick overview of the main folders and files in DjOpenKB.
For deployment steps, use `README.md`. For daily commands, use `USEFUL_COMMANDS.md`.

## Main Application

```text
DjOpenKB/
├── djopenkb/              # Django project settings and root URLs
├── kb/                    # Main Django app: articles, search, admin tools, auth flow
├── website/templates/     # HTML templates used by the site
├── website/static/        # Static frontend assets
├── locale/                # Translation files for supported languages
├── manage.py              # Django management entry point
└── requirements.txt       # Python dependencies
```

## Docker and Deployment

```text
├── docker-compose.yml                 # Starts Vault, Postgres, Django web, nginx, cleanup scheduler
├── Dockerfile                         # Django web image
├── Dockerfile.postgres-vault          # Postgres image with Vault password loading
├── nginx/                             # Reverse proxy configuration
└── docker/postgres-vault-entrypoint.sh # Reads DB password from Vault before starting Postgres
```

## Vault Secret Management

```text
vault/
├── config/
│   ├── vault.hcl              # Vault server configuration
│   └── djopenkb-policy.hcl    # Vault policy for DjOpenKB services
├── scripts/
│   ├── init.sh                # Initializes/seeds Vault secrets
│   └── auto-unseal.sh         # Auto-unseals Vault for local VM deployment
├── bootstrap/
│   ├── djopenkb.env.example   # Example secret seed file
│   └── .gitkeep
├── file/                      # Vault persistent data, not committed
└── keys/                      # Vault unseal/token runtime files, not committed
```

`vault/bootstrap/djopenkb.env` is a temporary plaintext seed file. It is used during first setup or intentional secret rotation, then should be removed after Vault is seeded.

Secrets stored in Vault include examples such as:

```text
POSTGRES_PASSWORD
DJANGO_SECRET_KEY
LDAP_BIND_PASSWORD
GEMINI_API_KEY
LLM_API_KEY
```

## Database Persistence

```text
postgres-data/       # PostgreSQL persistent data, not committed
```

Do not delete this folder unless you intentionally want to reset the database. If `docker compose down -v` or manual deletion removes database/Vault data, the Vault bootstrap secret file may need to be recreated and seeded again.

## Authentication

```text
kb/backends.py       # NextLabs AD/LDAP login and local login fallback
website/templates/login.html
```

Main login flow:

```text
NextLabs AD login by default
Local Django login available as a secondary fallback link
```

LDAP password is stored in Vault, while non-secret LDAP settings remain in `.env`.

## Article and Admin Features

```text
kb/models.py         # Article and suggestion models
kb/views/            # Article, search, admin, profile, and review views
kb/forms.py          # Django forms
kb/admin.py          # Django admin registration
```

Key features include:

```text
Article browsing and search
User-submitted article suggestions
Pending article review workflow
Pending failed review comments
Admin tools
Stray upload cleanup
```

## OpenKB AI Integration

```text
OpenKB-main/         # Downloaded OpenKB source from https://github.com/VectifyAI/OpenKB
openkb-data/         # Local OpenKB knowledge-base data, not committed
```

DjOpenKB integrates the downloaded OpenKB project source into the website to provide the OpenKB AI/chatbox experience. Local article data is synced into `openkb-data/` for the integrated AI workflow.

## Runtime Folders Not to Commit

These folders are needed at runtime but their real contents should not be pushed to GitHub:

```text
vault/bootstrap/djopenkb.env
vault/file/
vault/keys/
vault/logs/
postgres-data/
openkb-data/
```

Use `.gitkeep` files to keep the empty folder structure in GitHub while ignoring the sensitive/runtime contents.
