# LDAP / LDAPS Production Setup for DjOpenKB

This guide is for connecting DjOpenKB to an existing production Active Directory environment.

For building a Windows Server 2022 AD/LDAPS lab from scratch, use:

```text
documentations/WINDOWS_SERVER_2022_AD_LDAPS_SETUP.md
```

For production, use **LDAPS** on TCP port `636` so credentials and directory traffic are protected with TLS.

---

## 1. Obtain the Active Directory Details

Get the following information from the Active Directory administrator:

```text
AD DNS domain:              <AD_DOMAIN>
NetBIOS domain:             <AD_NETBIOS_DOMAIN>
Domain Controller FQDN:     <DOMAIN_CONTROLLER_FQDN>
Domain Controller IP:       <AD_DC_IP>
LDAP bind/service account:  <LDAP_BIND_ACCOUNT>
LDAP user search base:      <LDAP_USER_SEARCH_BASE>
Accepted login/email domains: <ALLOWED_DOMAINS>
```

Use a low-privilege service account that only needs permission to bind to Active Directory and search for users.

The Domain Controller hostname used in `LDAP_SERVER_URI` should match the hostname covered by the LDAPS certificate.

---

## 2. Obtain the LDAPS CA Certificate

Obtain the public CA certificate that issued the Domain Controller's LDAPS certificate from the Active Directory or certificate administrator.

If exporting it from Windows:

1. Open the Windows certificate manager or the organisation's Certificate Authority console.
2. Locate the Root CA certificate that issued the Domain Controller certificate.
3. If an Intermediate/Issuing CA is also used, export that certificate as well.
4. Export **without the private key**.
5. Select **Base-64 encoded X.509 (.CER)** when available.

Copy the certificate to the DjOpenKB server and place it in:

```text
/opt/DjOpenKB/ldap-certs/ad-ca.crt
```

A Base-64 `.cer` file can simply be copied or renamed to `ad-ca.crt`.

From the project directory:

```bash
cd /opt/DjOpenKB
head -n 2 ldap-certs/ad-ca.crt
```

A readable certificate should begin with:

```text
-----BEGIN CERTIFICATE-----
```

If the exported certificate is binary DER format, convert it once:

```bash
openssl x509 -inform DER \
  -in ldap-certs/ad-ca.cer \
  -out ldap-certs/ad-ca.crt
```

Verify it:

```bash
openssl x509 -in ldap-certs/ad-ca.crt -noout -subject -issuer -dates
```

If the Domain Controller certificate uses an Intermediate/Issuing CA, combine the required CA chain into the same file, with the issuing certificate first and the root certificate after it:

```bash
cat issuing-ca.crt root-ca.crt > ldap-certs/ad-ca.crt
```

Do not export or copy any private key.

DjOpenKB uses the certificate file directly through the Docker mount; it does not need to be installed into the Linux system-wide certificate store.

---

## 3. Configure LDAPS in `.env`

Edit:

```bash
cd /opt/DjOpenKB
sudo nano .env
```

Configure the production values:

```env
LDAP_ENABLED=true
LDAP_PLACEHOLDER_ENABLED=false
LDAP_PLACEHOLDER_AUTO_CREATE_USERS=false

LDAP_DC_IP=<AD_DC_IP>
LDAP_SERVER_URI=ldaps://<DOMAIN_CONTROLLER_FQDN>:636
LDAP_START_TLS=false
LDAP_CA_CERT_FILE=/etc/ssl/certs/djopenkb-ldap/ad-ca.crt
LDAP_TLS_REQUIRE_CERT=demand
LDAP_ALLOW_INSECURE=false

LDAP_AD_DOMAIN=<AD_DOMAIN>
LDAP_NETBIOS_DOMAIN=<AD_NETBIOS_DOMAIN>
LDAP_ALLOWED_EMAIL_DOMAINS=<ALLOWED_DOMAINS>

LDAP_USER_SEARCH_BASE=<LDAP_USER_SEARCH_BASE>
LDAP_USER_FILTER=(|(sAMAccountName=%(user)s)(userPrincipalName=%(user)s)(mail=%(user)s))

LDAP_BIND_DN=<LDAP_BIND_ACCOUNT>
```

Example search-base conversion:

```text
example.local
-> DC=example,DC=local
```

Every valid Active Directory user returned by `LDAP_USER_SEARCH_BASE` and `LDAP_USER_FILTER` can authenticate. Narrow the search base or filter only when the organisation requires a smaller login scope.

---

## 4. Store the LDAP Bind Password in Vault

Do not place the LDAP bind password in `.env`.

For a first-time deployment, add only the required secret to the Vault bootstrap file before the initial Vault setup:

```env
LDAP_BIND_PASSWORD=<LDAP_BIND_PASSWORD>
```

The file is:

```text
/opt/DjOpenKB/vault/bootstrap/djopenkb.env
```

After Vault has been seeded successfully, remove the bootstrap file as described in the Deployment Guide.

For an existing deployment where the LDAP bind password must be changed, follow:

```text
documentations/UPDATE_AND_MAINTENANCE_GUIDE.md
```

---

## 5. Start or Recreate the Application

For a fresh deployment, continue with the normal Deployment Guide.

If LDAPS configuration or the certificate was added to an existing deployment:

```bash
cd /opt/DjOpenKB
sudo docker compose down
sudo docker compose up -d --build
```

The project mounts:

```text
./ldap-certs/
```

into the Django container as:

```text
/etc/ssl/certs/djopenkb-ldap/
```

---

## 6. Test the LDAPS Connection

Run:

```bash
cd /opt/DjOpenKB
sudo docker compose exec web sh scripts/test_ldaps.sh
```

A successful test should confirm that:

```text
- the Domain Controller hostname resolves;
- TCP port 636 is reachable;
- the TLS handshake succeeds; and
- the Domain Controller certificate is trusted by ad-ca.crt.
```

You can also verify that the certificate is visible inside the container:

```bash
sudo docker compose exec web \
  ls -l /etc/ssl/certs/djopenkb-ldap/ad-ca.crt
```

---

## 7. Test Active Directory Login

After the LDAPS test succeeds, sign in to DjOpenKB using a normal Active Directory account.

Depending on the configured domain values, supported formats may include:

```text
username
username@<AD_DOMAIN>
<AD_NETBIOS_DOMAIN>\username
```

A valid Active Directory account should be mapped to one Django user account instead of creating separate users for different login formats.

---

## 8. Quick Troubleshooting

### Certificate file not found

Check the host file:

```bash
ls -l /opt/DjOpenKB/ldap-certs/ad-ca.crt
```

Check the container mount:

```bash
sudo docker compose exec web \
  ls -l /etc/ssl/certs/djopenkb-ldap/ad-ca.crt
```

### Certificate validation fails

Confirm that:

```text
- ad-ca.crt contains the CA that issued the Domain Controller LDAPS certificate;
- any required Intermediate/Issuing CA is included;
- LDAP_SERVER_URI uses the correct Domain Controller FQDN; and
- LDAP_TLS_REQUIRE_CERT remains set to demand.
```

Do not bypass production certificate validation by setting insecure LDAP options.

### Domain Controller hostname does not resolve

Confirm DNS from the Django container:

```bash
sudo docker compose exec web python -c \
  "import socket; print(socket.getaddrinfo('<DOMAIN_CONTROLLER_FQDN>', 636))"
```

If the deployment network does not provide the required DNS record, configure the approved `LDAP_DC_IP`/Compose hostname mapping used by the project.

### Authentication fails after TLS succeeds

Check:

```text
- LDAP_BIND_DN is correct;
- the Vault LDAP bind password is current;
- LDAP_USER_SEARCH_BASE matches the production AD structure; and
- LDAP_USER_FILTER returns the intended users.
```

Do not require a separate AD access group unless the organisation intentionally adds that restriction to the search base or filter.
