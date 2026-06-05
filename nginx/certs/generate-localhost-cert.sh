#!/usr/bin/env sh
set -eu

# Generate a local self-signed HTTPS certificate for DjOpenKB Nginx.
#
# Output files:
#   nginx/certs/localhost.crt
#   nginx/certs/localhost.key
#
# These paths match nginx/nginx.conf:
#   ssl_certificate     /etc/nginx/certs/localhost.crt;
#   ssl_certificate_key /etc/nginx/certs/localhost.key;
#
# Run from the project root:
#   chmod +x nginx/certs/generate-localhost-cert.sh
#   ./nginx/certs/generate-localhost-cert.sh

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
CERT_FILE="$SCRIPT_DIR/localhost.crt"
KEY_FILE="$SCRIPT_DIR/localhost.key"
OPENSSL_CNF="$SCRIPT_DIR/localhost-openssl.cnf"

echo "Generating local self-signed HTTPS certificate..."
echo "Output folder: $SCRIPT_DIR"

cat > "$OPENSSL_CNF" <<'EOF'
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req

[dn]
C = SG
ST = Singapore
L = Singapore
O = DjOpenKB Local
OU = Development
CN = localhost

[v3_req]
subjectAltName = @alt_names
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
basicConstraints = critical, CA:FALSE

[alt_names]
DNS.1 = localhost
DNS.2 = nginx
DNS.3 = djopenkb.local
IP.1 = 127.0.0.1
IP.2 = 0.0.0.0
EOF

openssl req -x509 -nodes -days 825 \
    -newkey rsa:2048 \
    -keyout "$KEY_FILE" \
    -out "$CERT_FILE" \
    -config "$OPENSSL_CNF"

chmod 644 "$CERT_FILE"
chmod 600 "$KEY_FILE"

rm -f "$OPENSSL_CNF"

echo
echo "Certificate generated successfully:"
echo "  $CERT_FILE"
echo "  $KEY_FILE"
echo
echo "These files match the Nginx container paths:"
echo "  /etc/nginx/certs/localhost.crt"
echo "  /etc/nginx/certs/localhost.key"
echo
echo "You can now run:"
echo "  sudo docker compose up -d --build"
echo
echo "Then open:"
echo "  https://<linux-server-ip>:8080"
