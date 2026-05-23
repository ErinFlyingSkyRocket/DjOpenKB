# DjOpenKB app token policy for Vault KV v2.
# Allows Django/Postgres to read exactly: secret/djopenkb
# Vault KV v2 API path for that secret is: secret/data/djopenkb

path "secret/data/djopenkb" {
  capabilities = ["read"]
}

path "secret/metadata/djopenkb" {
  capabilities = ["read", "list"]
}
