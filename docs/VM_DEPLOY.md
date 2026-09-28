# VM deploy + public URL runbook

## 0. What I need from you (SSH)

- VM IP/hostname, SSH user, and auth: public key (best) or temporary password.
- VM OS (expect Ubuntu 22.04/24.04), vCPU/RAM, GPU? (`nvidia-smi` output if any).
- Domain for the public URL (e.g. `chat.waffarha.com`) with an A record -> VM IP,
  or confirm IP-only testing first (`http://<ip>`).
- Repo URL + branch to deploy, and the `.env` secrets (never paste secrets in
  chat — put them on the VM via `nano .env` after first SSH).

With that I can: `ssh` via the shell tool, run `setup_vm.sh`, build, deploy,
and verify from here. No secrets are stored outside your VM.

## 1. Files added for this phase

- `docker-compose.prod.yml` — restart policies, memory limits, JSON log
  rotation, + `caddy` service (automatic HTTPS).
- `Caddyfile` — reverse proxy `-> app:8000`. Replace `:80` with your domain
  before going public so Caddy provisions TLS.
- `setup_vm.sh` — idempotent bootstrap: Docker, firewall (22/80/443),
  repo pull, `.env` safety check (refuses public + `IDENTITY_BACKEND=static`
  with personal on), build + up.
- `core/config.py` — `PUBLIC_URL` + `ALLOWED_ORIGINS` (comma-separated extras).
- `core/app.py`, `core/agent_server.py` — CORS now reads `config.ALLOWED_ORIGINS`.
- `.env.example` — documented `PUBLIC_URL` / `ALLOWED_ORIGINS`.

## 2. Deploy sequence (on the VM)

```bash
git clone <repo> ~/waffarha-chatbot && cd ~/waffarha-chatbot
cp .env.example .env && nano .env   # CLICKHOUSE_*, PUBLIC_URL, ALLOWED_ORIGINS,
                                    # PERSONAL_QUERIES_ENABLED + IDENTITY_BACKEND=auth
bash setup_vm.sh
# Caddyfile: set your domain, then:
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
curl -fsS http://localhost:8000/api/health
curl -fsS http://localhost:8001/api/health
```

Public checks: `https://<domain>` loads widget, `/api/chat` answers,
`https://<domain>/api/health` is `ok`. If DNS isn't ready, test via
`http://<vm-ip>` first (TLS comes after the domain resolves).

## 3. Safety before opening to people

- `IDENTITY_BACKEND=auth` + `AUTH_SIGNING_SECRET` when personal/support
  per-user queries are on. `static` = everyone's data visible to everyone.
- Keep `data/` as a mounted volume (already in compose), back up `.env`
  separately. `CLICKHOUSE_*` user should be read-only.
- Logs: `docker compose logs -f app`, resources: `docker stats`.
