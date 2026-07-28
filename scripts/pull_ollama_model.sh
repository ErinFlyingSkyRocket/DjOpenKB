#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/DjOpenKB}"
MODEL="${1:-}"

cd "${PROJECT_DIR}"

if [[ -z "${MODEL}" && -f .env ]]; then
  MODEL="$(sed -n 's/^OLLAMA_MODEL=//p' .env | tail -n 1 | tr -d '\r')"
fi
MODEL="${MODEL:-granite4:3b}"

sudo systemctl start docker
sudo systemctl start ollama

DOCKER_HOST_IP="$(ip -4 addr show docker0 | awk '/inet / {print $2}' | cut -d/ -f1 | head -n 1)"
if [[ -z "${DOCKER_HOST_IP}" ]]; then
  echo "Could not determine the docker0 IPv4 address." >&2
  exit 1
fi

API_URL="http://${DOCKER_HOST_IP}:11434"

if ! curl -fsS "${API_URL}/api/tags" >/dev/null 2>&1; then
  echo "Host Ollama is not reachable at ${API_URL}." >&2
  echo "Run scripts/install_host_ollama.sh first." >&2
  exit 1
fi

echo "Downloading model to the host Ollama model store: ${MODEL}"
OLLAMA_HOST="${API_URL}" ollama pull "${MODEL}"

echo "Installed Ollama models:"
OLLAMA_HOST="${API_URL}" ollama list
