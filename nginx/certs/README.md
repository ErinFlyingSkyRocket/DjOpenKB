# OpenKB Local HTTPS Certificate Generator

This pack contains two placement options.

## Option A: Put scripts in `DjOpenKB\nginx\`

Copy these two files:

```text
nginx\generate-localhost-cert.ps1
nginx\generate-localhost-cert.bat
```

Then double-click:

```text
nginx\generate-localhost-cert.bat
```

It will generate:

```text
nginx\certs\localhost.crt
nginx\certs\localhost.key
```

## Option B: Put scripts directly in `DjOpenKB\nginx\certs\`

Copy these two files:

```text
nginx\certs\generate-localhost-cert.ps1
nginx\certs\generate-localhost-cert.bat
```

Then double-click:

```text
nginx\certs\generate-localhost-cert.bat
```

It will generate:

```text
nginx\certs\localhost.crt
nginx\certs\localhost.key
```

## Docker Compose mount example

Use this in your nginx service:

```yaml
volumes:
  - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
  - ./nginx/certs:/etc/nginx/certs:ro
ports:
  - "8080:8080"
```

Then inside nginx config:

```nginx
ssl_certificate     /etc/nginx/certs/localhost.crt;
ssl_certificate_key /etc/nginx/certs/localhost.key;
```
