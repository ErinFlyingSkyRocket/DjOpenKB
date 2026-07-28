# DjOpenKB Host-Installed Ollama Setup

## Purpose

This procedure configures DjOpenKB to use an Ollama model installed directly on the Ubuntu host instead of running Ollama as a Docker Compose service.

The selected pilot model is:

```text
granite4:3b
```

It is a small tool-capable model suitable for initial testing on a constrained **2 vCPU and 8 GB RAM** VM. It is still expected to be much slower than Gemini on CPU-only hardware, so the AI worker remains limited to one request at a time and the request timeout remains 300 seconds.

## Resulting design

```text
Browser
   |
   v
Nginx :443 -> Django -> Redis/Celery -> ai-worker container
                                             |
                                             | host.docker.internal:11434
                                             v
                                  Ollama systemd service on Ubuntu
                                             |
                                             v
                        /usr/share/ollama/.ollama/models
```

The Ollama model is not stored in the project directory, GitHub, or a Docker volume. Normal commands such as `docker compose down` and `docker compose up -d` do not delete or redownload it.

## Important change from the earlier Docker-Ollama pilot

The new incremental update:

- removes the `ollama` service from `docker-compose.yml`;
- removes the `ollama_data` named volume and Ollama-only networks;
- maps `host.docker.internal` to the Linux host using `host-gateway` for the `ai-worker` container;
- attaches the AI worker to the existing routable bridge so it can reach the host; this is not the same network-level no-egress isolation provided by the earlier container-only design;
- changes `OLLAMA_API_BASE` to `http://host.docker.internal:11434`;
- keeps the model and Ollama runtime under the Ubuntu system service.

The prior code changes in `OpenKB-main/openkb/agent/query.py` remain valid and do not need to be replaced again.

## 1. Apply the incremental files

Copy only the new or modified files into their listed project paths under:

```text
/opt/DjOpenKB
```

The runtime file must be named:

```text
/opt/DjOpenKB/.env
```

Do not copy the LLM model into `/opt/DjOpenKB`.

## 2. Install Ollama on the Ubuntu host

The Ollama installation is system-wide. It is not installed into a particular project directory and is not installed using `sudo apt install ollama`.

You may run the helper from the project directory:

```bash
cd /opt/DjOpenKB
sudo chmod +x scripts/install_host_ollama.sh
./scripts/install_host_ollama.sh
```

The helper performs the following actions:

1. installs `curl` through apt only when it is missing;
2. installs or updates Ollama using its official Linux installer;
3. detects the host `docker0` bridge address;
4. creates a systemd override for the 2-vCPU/8-GB pilot limits;
5. binds Ollama only to the Docker bridge address rather than the normal LAN address;
6. downloads `granite4:3b` into the host Ollama model store.

The equivalent manual installation command is:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

This command may be run from any directory.

## 3. Host Ollama systemd settings

The helper creates:

```text
/etc/systemd/system/ollama.service.d/djopenkb.conf
```

Its effective settings are equivalent to:

```ini
[Unit]
Requires=docker.service
After=docker.service network-online.target

[Service]
Environment="OLLAMA_HOST=<DOCKER0_IP>:11434"
Environment="OLLAMA_CONTEXT_LENGTH=8192"
Environment="OLLAMA_KEEP_ALIVE=5m"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

`<DOCKER0_IP>` is detected from:

```bash
ip -4 addr show docker0
```

It is commonly `172.17.0.1`, but the helper does not assume that value.

Binding only to the Docker bridge means users on the normal network should not be able to connect directly to Ollama port 11434.

## 4. Verify the host service

```bash
sudo systemctl status ollama --no-pager
sudo systemctl is-enabled ollama
sudo journalctl -u ollama -n 100 --no-pager
```

Determine the endpoint used on the host:

```bash
DOCKER_HOST_IP=$(ip -4 addr show docker0 | awk '/inet / {print $2}' | cut -d/ -f1)
echo "$DOCKER_HOST_IP"
curl "http://${DOCKER_HOST_IP}:11434/api/tags"
```

Confirm Ollama is not listening on the normal server IP:

```bash
sudo ss -lntp | grep ':11434'
```

The listener should show the Docker bridge address, not `0.0.0.0:11434` and not `10.23.58.201:11434`.

## 5. Confirm or download the model

```bash
cd /opt/DjOpenKB
./scripts/pull_ollama_model.sh
```

Manual equivalent:

```bash
DOCKER_HOST_IP=$(ip -4 addr show docker0 | awk '/inet / {print $2}' | cut -d/ -f1)
OLLAMA_HOST="http://${DOCKER_HOST_IP}:11434" ollama pull granite4:3b
OLLAMA_HOST="http://${DOCKER_HOST_IP}:11434" ollama list
```

Test a direct response:

```bash
DOCKER_HOST_IP=$(ip -4 addr show docker0 | awk '/inet / {print $2}' | cut -d/ -f1)
OLLAMA_HOST="http://${DOCKER_HOST_IP}:11434" \
  ollama run granite4:3b 'Reply with exactly: OLLAMA_OK'
```

## 6. Required DjOpenKB runtime values

The relevant values in `/opt/DjOpenKB/.env` are:

```env
OLLAMA_MODEL=granite4:3b
OPENKB_AI_MODEL=ollama_chat/granite4:3b
OLLAMA_API_BASE=http://host.docker.internal:11434
OLLAMA_DUMMY_API_KEY=ollama-local
OPENKB_AI_MAX_TURNS=12
OPENKB_AI_TIMEOUT_SECONDS=300
OPENKB_AI_CONCURRENCY_LIMIT=1
OPENKB_AI_CONCURRENCY_LOCK_SECONDS=390
OPENKB_AI_WORKER_CONCURRENCY=1
```

Do not set `OLLAMA_API_BASE` to either of these:

```text
http://localhost:11434
http://ollama:11434
```

Inside the `ai-worker` container, `localhost` means the container itself, while the old `ollama` Docker service no longer exists.

The `OLLAMA_CONTEXT_LENGTH`, `OLLAMA_KEEP_ALIVE`, `OLLAMA_NUM_PARALLEL`, and `OLLAMA_MAX_LOADED_MODELS` values are now controlled by the host systemd override, not by the project `.env` file.

## 7. Validate and recreate DjOpenKB

```bash
cd /opt/DjOpenKB
sudo docker compose config --quiet
sudo docker compose up -d --build --remove-orphans
sudo docker compose ps
```

`--remove-orphans` removes the old Ollama container if it was created using the previous Compose file. It does not affect the new host Ollama systemd service.

Do not use `docker compose down -v` as a normal update command because it also deletes unrelated DjOpenKB named volumes.

## 8. Test connectivity from the AI worker

```bash
sudo docker compose exec -T ai-worker python - <<'PY'
import json
import urllib.request

url = "http://host.docker.internal:11434/api/tags"
with urllib.request.urlopen(url, timeout=10) as response:
    data = json.load(response)

print([item.get("name") for item in data.get("models", [])])
PY
```

The output should include:

```text
granite4:3b
```

Confirm the container mapping:

```bash
sudo docker compose exec ai-worker getent hosts host.docker.internal
sudo docker compose exec ai-worker printenv OLLAMA_API_BASE
```

## 9. Test through DjOpenKB

Open:

```text
https://10.23.58.201/
```

Ask a simple question whose answer exists in a short Published article.

Monitor the worker and host model together:

```bash
sudo docker compose logs -f ai-worker
```

In a second terminal:

```bash
sudo journalctl -f -u ollama
```

## 10. Persistence

The standard Linux installation stores Ollama models under:

```text
/usr/share/ollama/.ollama/models
```

Therefore, these commands do not remove the model:

```bash
sudo docker compose down
sudo docker compose up -d --build
sudo systemctl restart ollama
sudo reboot
```

The model is downloaded again only if it is manually deleted, the host is rebuilt, or a different model is pulled.

## 11. Expected performance

For a 2-vCPU and 8-GB CPU-only instance:

- only one AI request should run at a time;
- CPU usage may stay near 100% during inference;
- the first request after model unload is slower;
- multi-step OpenKB retrieval may take one to several minutes;
- a small local model may be less reliable than Gemini at choosing retrieval tools;
- normal page response should be monitored while inference is active.

Monitor:

```bash
free -h
uptime
top
sudo systemctl status ollama --no-pager
```

Stop the pilot if the VM swaps heavily or normal website functions become unresponsive.

## 12. Troubleshooting

### The worker cannot resolve `host.docker.internal`

```bash
sudo docker compose config | grep -A3 -B3 host.docker.internal
sudo docker compose up -d --force-recreate ai-worker
sudo docker compose exec ai-worker getent hosts host.docker.internal
```

### Ollama is running only on 127.0.0.1

```bash
sudo systemctl cat ollama
sudo systemctl daemon-reload
sudo systemctl restart ollama
sudo ss -lntp | grep ':11434'
```

Confirm that `/etc/systemd/system/ollama.service.d/djopenkb.conf` exists.

### The model is missing

```bash
cd /opt/DjOpenKB
./scripts/pull_ollama_model.sh granite4:3b
```

### The answer times out

Keep concurrency at one. First try a simpler article question. If the model repeatedly exceeds 300 seconds, test the lighter model:

```bash
cd /opt/DjOpenKB
./scripts/pull_ollama_model.sh llama3.2:1b
```

Then update:

```env
OLLAMA_MODEL=llama3.2:1b
OPENKB_AI_MODEL=ollama_chat/llama3.2:1b
```

Recreate the settings-reading services:

```bash
sudo docker compose up -d --force-recreate web ai-worker
```

The 1B model is faster and lighter, but its retrieval-tool accuracy may be lower.

## 13. Returning to Gemini

Restore the cloud checkpoint branch and its cloud `.env` values, then recreate the application services. The host Ollama service can remain installed without being used, or it can be stopped:

```bash
sudo systemctl disable --now ollama
```

Re-enable it later with:

```bash
sudo systemctl enable --now ollama
```
