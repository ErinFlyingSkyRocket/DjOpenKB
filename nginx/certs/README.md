# Local HTTPS Certificate Generator

The project includes synchronized certificate-generation scripts for Linux,
PowerShell, and Windows Batch.

Generated files:

```text
nginx/certs/localhost.crt
nginx/certs/localhost.key
```

These files match the Nginx container configuration:

```text
/etc/nginx/certs/localhost.crt
/etc/nginx/certs/localhost.key
```

> These self-signed certificates are for internal development only. For a
> production/public deployment, use a certificate issued for the final DNS
> hostname.

## Linux

From the project root:

```bash
chmod +x nginx/certs/generate-localhost-cert.sh
sh nginx/certs/generate-localhost-cert.sh <INTERNAL_SERVER_IP>
```

Optional second argument: certificate lifetime in days.

```bash
sh nginx/certs/generate-localhost-cert.sh <INTERNAL_SERVER_IP> 825
```

Without an IP argument:

```bash
sh nginx/certs/generate-localhost-cert.sh
```

the certificate is generated for local development using `localhost`.

## Windows PowerShell

From the project root:

```powershell
powershell -ExecutionPolicy Bypass -File nginx/certs/generate-localhost-cert.ps1 <INTERNAL_SERVER_IP>
```

Optional second argument:

```powershell
powershell -ExecutionPolicy Bypass -File nginx/certs/generate-localhost-cert.ps1 <INTERNAL_SERVER_IP> 825
```

## Windows Batch

From the project root:

```bat
nginx\certs\generate-localhost-cert.bat <INTERNAL_SERVER_IP>
```

Optional second argument:

```bat
nginx\certs\generate-localhost-cert.bat <INTERNAL_SERVER_IP> 825
```

The Batch file passes all arguments to the PowerShell implementation.

## Shared behaviour

All three entry points now use the same defaults and SAN set:

- Default certificate lifetime: `365` days.
- DNS SANs: `localhost`, `nginx`, `djopenkb.local`.
- IP SANs: `127.0.0.1`, `0.0.0.0`.
- When an IPv4 address is supplied, it is added as another SAN and used as the
  certificate common name.

After generating or replacing the certificate, recreate/restart Nginx as
appropriate for the deployment.

For the standard full deployment:

```bash
sudo docker compose up -d --build
```

A browser warning is expected until the self-signed certificate is trusted on
the client device.
