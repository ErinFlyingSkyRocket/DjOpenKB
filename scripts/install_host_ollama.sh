#!/usr/bin/env bash
set -euo pipefail

MODEL="${1:-granite4:3b}"

if ! command -v sudo >/dev/null 2>&1; then
  echo "sudo is required." >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is not installed; installing it with apt..."
  sudo apt-get update
  sudo apt-get install -y curl
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker must already be installed because DjOpenKB uses Docker Compose." >&2
  exit 1
fi

sudo systemctl start docker

DOCKER_HOST_IP="$(ip -4 addr show docker0 | awk '/inet / {print $2}' | cut -d/ -f1 | head -n 1)"
if [[ -z "${DOCKER_HOST_IP}" ]]; then
  echo "Could not determine the docker0 IPv4 address." >&2
  exit 1
fi

echo "Docker host-gateway address: ${DOCKER_HOST_IP}"

if ! command -v ollama >/dev/null 2>&1; then
  echo "Installing Ollama system-wide using the official Linux installer..."
  curl -fsSL https://ollama.com/install.sh | sh
else
  echo "Ollama is already installed: $(ollama --version 2>/dev/null || true)"
fi

# Bind Ollama only to the Docker bridge address. This lets the ai-worker
# container reach it without exposing TCP 11434 to the normal LAN interface.
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/djopenkb.conf >/dev/null <<EOF_OVERRIDE
[Unit]
Requires=docker.service
After=docker.service network-online.target

[Service]
Environment="OLLAMA_HOST=${DOCKER_HOST_IP}:11434"
Environment="OLLAMA_CONTEXT_LENGTH=8192"
Environment="OLLAMA_KEEP_ALIVE=5m"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
EOF_OVERRIDE

sudo systemctl daemon-reload
sudo systemctl enable ollama
sudo systemctl restart ollama

API_URL="http://${DOCKER_HOST_IP}:11434"
echo "Waiting for Ollama at ${API_URL}..."
for _ in $(seq 1 30); do
  if curl -fsS "${API_URL}/api/tags" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if ! curl -fsS "${API_URL}/api/tags" >/dev/null 2>&1; then
  echo "Ollama did not become ready." >&2
  echo "Check: sudo systemctl status ollama --no-pager" >&2
  echo "Check: sudo journalctl -u ollama -n 100 --no-pager" >&2
  exit 1
fi

echo "Downloading model to the host Ollama model store: ${MODEL}"
OLLAMA_HOST="${API_URL}" ollama pull "${MODEL}"

echo "Installed models:"
OLLAMA_HOST="${API_URL}" ollama list

echo
echo "Host Ollama setup completed."
echo "DjOpenKB should use: OLLAMA_API_BASE=http://host.docker.internal:11434"
echo "Model files are maintained by the host Ollama service, outside Docker Compose."
