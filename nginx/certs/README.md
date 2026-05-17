# OpenKB Local HTTPS Certificate Generator

This project uses a local self-signed SSL certificate for HTTPS access through Nginx.

The generated certificate files are:

```text
nginx/certs/localhost.crt
nginx/certs/localhost.key
````

These files are mounted into the Nginx Docker container and used for:

```text
https://localhost:8080
```

---

## Windows Certificate Generation

This project includes Windows scripts for generating the local HTTPS certificate.

### Option A: Scripts in `DjOpenKB\nginx\`

Recommended location:

```text
DjOpenKB\nginx\
```

Copy these two files into the `nginx` folder:

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

---

### Option B: Scripts in `DjOpenKB\nginx\certs\`

Alternative location:

```text
DjOpenKB\nginx\certs\
```

Copy these two files into the `certs` folder:

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

---

## Linux Certificate Generation

For Linux machines, use the Bash script:

```text
nginx/generate-localhost-cert.sh
```

Make it executable:

```bash
cd nginx
chmod +x generate-localhost-cert.sh
```

Run it:

```bash
./generate-localhost-cert.sh
```

It will generate:

```text
nginx/certs/localhost.crt
nginx/certs/localhost.key
```

If OpenSSL is missing, install it first.

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install openssl -y
```

CentOS/RHEL/Fedora:

```bash
sudo dnf install openssl -y
```

---

## Docker Compose Nginx Mount

Use this in the `nginx` service inside `docker-compose.yml`:

```yaml
volumes:
  - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
  - ./nginx/certs:/etc/nginx/certs:ro
ports:
  - "8080:8080"
```

This mounts the local cert folder:

```text
./nginx/certs
```

into the Nginx container as:

```text
/etc/nginx/certs
```

---

## Nginx SSL Configuration

Inside `nginx/nginx.conf`, use:

```nginx
ssl_certificate     /etc/nginx/certs/localhost.crt;
ssl_certificate_key /etc/nginx/certs/localhost.key;
```

Nginx will then serve the Django/OpenKB website using HTTPS on:

```text
https://localhost:8080
```

---

## Browser Warning

Because this is a self-signed certificate, the browser may show a warning such as:

```text
Your connection is not private
```

This is normal for local development.

Continue to:

```text
https://localhost:8080
```

after accepting the browser warning.

```
