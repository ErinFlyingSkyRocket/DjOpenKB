#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT_DIR="$SCRIPT_DIR/certs"

CERT_FILE="$CERT_DIR/localhost.crt"
KEY_FILE="$CERT_DIR/localhost.key"

mkdir -p "$CERT_DIR"

echo "Generating local self-signed HTTPS certificate..."
echo "Output folder: $CERT_DIR"

openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout "$KEY_FILE" \
  -out "$CERT_FILE" \
  -subj "/C=SG/ST=Singapore/L=Singapore/O=DjOpenKB/OU=Local/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

chmod 600 "$KEY_FILE"
chmod 644 "$CERT_FILE"

echo ""
echo "Certificate generated successfully:"
echo "  $CERT_FILE"
echo "  $KEY_FILE"
echo ""
echo "You can now run:"
echo "  docker-compose up --build"
echo ""
echo "Then open:"
echo "  https://localhost:8080"