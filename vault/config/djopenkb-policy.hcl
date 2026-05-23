# DjOpenKB application policy
# Allows the app token to read the exact KV v2 secret used by Django/Postgres.

# Main DjOpenKB secret bundle: secret/djopenkb
path "secret/data/djopenkb" {
  capabilities = ["read"]
}

# Optional future nested secrets under secret/djopenkb/...
path "secret/data/djopenkb/*" {
  capabilities = ["read"]
}

# Metadata access is needed by Vault CLI/helpers for KV v2 path discovery/listing.
path "secret/metadata/djopenkb" {
  capabilities = ["read", "list"]
}

path "secret/metadata/djopenkb/*" {
  capabilities = ["read", "list"]
}

# Allows `vault kv get secret/djopenkb` to resolve the KV mount without 403.
# This does not grant broad secret read access; actual secret reads are controlled above.
path "sys/internal/ui/mounts/secret/djopenkb" {
  capabilities = ["read"]
}

path "sys/internal/ui/mounts/secret/djopenkb/*" {
  capabilities = ["read"]
}
