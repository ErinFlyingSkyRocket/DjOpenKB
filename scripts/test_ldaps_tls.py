#!/usr/bin/env python3
"""Test LDAPS DNS resolution and TLS validation from the Django container.

What this script does:
    Resolves the configured Domain Controller hostname, connects to the LDAPS
    port, and validates the server certificate with the mounted AD CA file. It
    does not use or display the LDAP bind password and does not test a user
    login.

Recommended server command (through the wrapper script):
    cd /opt/DjOpenKB
    sudo docker compose exec -T web sh /app/scripts/test_ldaps.sh

Equivalent direct command inside the web container:
    cd /opt/DjOpenKB
    sudo docker compose exec -T web python /app/scripts/test_ldaps_tls.py

No container restart or rebuild is required for this diagnostic.
"""
import os
import socket
import ssl
from urllib.parse import urlparse

uri = os.getenv("LDAP_SERVER_URI", "").strip()
ca_file = os.getenv("LDAP_CA_CERT_FILE", "/etc/ssl/certs/djopenkb-ldap/ad-ca.crt").strip()

if not uri:
    raise SystemExit("LDAP_SERVER_URI is empty.")

parsed = urlparse(uri)
host = parsed.hostname
port = parsed.port or (636 if parsed.scheme == "ldaps" else 389)

print(f"LDAP_SERVER_URI = {uri}")
print(f"Host            = {host}")
print(f"Port            = {port}")
print(f"CA file         = {ca_file}")

if parsed.scheme != "ldaps":
    raise SystemExit("This TLS test expects LDAP_SERVER_URI to start with ldaps://")

print("Resolving hostname...")
print(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))

ctx = ssl.create_default_context(cafile=ca_file)
ctx.check_hostname = True
ctx.verify_mode = ssl.CERT_REQUIRED

print("Opening TLS connection...")
with socket.create_connection((host, port), timeout=8) as sock:
    with ctx.wrap_socket(sock, server_hostname=host) as ssock:
        cert = ssock.getpeercert()
        print("TLS handshake OK")
        print("TLS version:", ssock.version())
        print("Peer subject:", cert.get("subject"))
        print("Peer issuer:", cert.get("issuer"))

print("LDAPS DNS + TLS certificate validation looks good.")
