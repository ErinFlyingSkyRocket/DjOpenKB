#!/usr/bin/env sh
set -eu

# Generate a self-signed development TLS certificate for DjOpenKB Nginx.
#
# Optional arguments:
#   1. Browser-facing server IPv4 address.
#   2. Certificate lifetime in days. Default: 365.
#
# Examples:
#   sh nginx/certs/generate-localhost-cert.sh
#   sh nginx/certs/generate-localhost-cert.sh <INTERNAL_SERVER_IP>
#   sh nginx/certs/generate-localhost-cert.sh <INTERNAL_SERVER_IP> 825
#
# The generated files keep the names used by nginx.conf:
#   nginx/certs/localhost.crt
#   nginx/certs/localhost.key
#
# This certificate is for internal development only. Before public release,
# replace it with a certificate issued for the final public DNS hostname.

TARGET_IP="${1:-}"
DAYS="${2:-365}"

if ! printf '%s' "$DAYS" | grep -Eq '^[0-9]+$' || [ "$DAYS" -lt 1 ]; then
    echo "Error: certificate lifetime must be a positive number of days." >&2
    exit 2
fi

if [ -n "$TARGET_IP" ]; then
    if ! printf '%s\n' "$TARGET_IP" | awk -F. '
        NF != 4 { exit 1 }
        {
            for (i = 1; i <= 4; i++) {
                if ($i !~ /^[0-9]+$/ || $i < 0 || $i > 255) {
                    exit 1
                }
            }
        }
    '; then
        echo "Error: provide a valid IPv4 address, for example <INTERNAL_SERVER_IP>." >&2
        exit 2
    fi
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
DNS.3 = djopenkb.local
IP.1 = 127.0.0.1
IP.2 = 0.0.0.0
$EXTRA_IP_SAN
EOF_CONF

echo "Generating development TLS certificate..."
openssl req -x509 -nodes -days "$DAYS" \
    -newkey rsa:2048 \
    -keyout "$KEY_FILE" \
    -out "$CERT_FILE" \
    -config "$OPENSSL_CNF"

chmod 644 "$CERT_FILE"
chmod 600 "$KEY_FILE"

echo
echo "Certificate generated successfully:"
echo "  $CERT_FILE"
echo "  $KEY_FILE"
if [ -n "$TARGET_IP" ]; then
    echo
    echo "Browser URL:"
    echo "  https://$TARGET_IP:8080"
else
    echo
    echo "Browser URL:"
    echo "  https://localhost:8080"
fi
echo
echo "Trust the certificate on the development browser to avoid its self-signed warning."
