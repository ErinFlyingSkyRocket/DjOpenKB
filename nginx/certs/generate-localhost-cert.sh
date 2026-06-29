#!/usr/bin/env sh
set -eu

# Generate a self-signed development TLS certificate for DjOpenKB Nginx.
#
# Optional first argument: browser-facing server IPv4 address.  Example:
#   sudo sh nginx/certs/generate-localhost-cert.sh 10.23.58.201
#
# The generated files intentionally keep the existing names used by nginx.conf:
#   nginx/certs/localhost.crt
#   nginx/certs/localhost.key
#
# This certificate is only for internal development. Before public release,
# replace it with a certificate issued for the final public DNS hostname.

TARGET_IP="${1:-}"

if [ -n "$TARGET_IP" ]; then
    case "$TARGET_IP" in
        *[!0-9.]*|*..*|.*|*.)
            echo "Error: provide a valid IPv4 address, for example 10.23.58.201." >&2
            exit 2
            ;;
    esac
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
CERT_FILE="$SCRIPT_DIR/localhost.crt"
KEY_FILE="$SCRIPT_DIR/localhost.key"
OPENSSL_CNF="$(mktemp)"
trap 'rm -f "$OPENSSL_CNF"' EXIT

CN="localhost"
EXTRA_IP_SAN=""
if [ -n "$TARGET_IP" ]; then
    CN="$TARGET_IP"
    EXTRA_IP_SAN="IP.3 = $TARGET_IP"
fi

cat > "$OPENSSL_CNF" <<EOF_CONF
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
O = DjOpenKB Development
OU = Internal Development
CN = $CN

[v3_req]
subjectAltName = @alt_names
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
basicConstraints = critical, CA:FALSE

[alt_names]
DNS.1 = localhost
DNS.2 = nginx
IP.1 = 127.0.0.1
IP.2 = 0.0.0.0
$EXTRA_IP_SAN
EOF_CONF

echo "Generating development TLS certificate..."
openssl req -x509 -nodes -days 365 \
    -newkey rsa:2048 \
    -keyout "$KEY_FILE" \
    -out "$CERT_FILE" \
    -config "$OPENSSL_CNF"

chmod 644 "$CERT_FILE"
chmod 600 "$KEY_FILE"

echo
echo "Certificate generated: $CERT_FILE"
if [ -n "$TARGET_IP" ]; then
    echo "It is valid for: https://$TARGET_IP:8080"
fi
echo "Trust the certificate on the development browser to avoid its self-signed warning."
