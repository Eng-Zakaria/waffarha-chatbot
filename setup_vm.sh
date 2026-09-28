#!/usr/bin/env bash
# VM bootstrap for the Waffarha chatbot (Ubuntu 22.04/24.04).
# Idempotent: safe to re-run. Installs Docker, clones/updates the repo,
# builds images (models baked), and starts the public stack.
#
# Usage on a fresh VM:
#   curl -fsSL <raw-url>/setup_vm.sh -o setup_vm.sh && bash setup_vm.sh
# Or: git clone <repo> && cd waffarha-chatbot && bash setup_vm.sh
#
# After it finishes: open https://<your-domain> (Caddy provisions TLS).
set -euo pipefail

REPO_URL="${REPO_URL:-}"
APP_DIR="${APP_DIR:-$HOME/waffarha-chatbot}"

echo "=== 1/5 apt + docker ==="
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -y
  sudo apt-get install -y ca-certificates curl git ufw
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER" || true
fi
docker --version
docker compose version

echo "=== 2/5 firewall (ssh + http/https only) ==="
sudo ufw allow OpenSSH || true
sudo ufw allow 80/tcp || true
sudo ufw allow 443/tcp || true
sudo ufw --force enable || true

echo "=== 3/5 repo ==="
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only || true
else
  if [ -z "$REPO_URL" ]; then
    echo "No repo found at $APP_DIR and REPO_URL is unset."
    echo "Clone manually: git clone <repo-url> $APP_DIR"
    exit 1
  fi
  git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

echo "=== 4/5 env check ==="
if [ ! -f .env ]; then
  echo "ERROR: .env missing. Copy from .env.example and fill secrets first:"
  echo "  cp .env.example .env && nano .env"
  echo "Required: CLICKHOUSE_*, AUTH_SIGNING_SECRET (if personal on), PUBLIC_URL, ALLOWED_ORIGINS."
  exit 1
fi
# Fail fast on the dangerous default: public + static identity leaks user data.
if grep -q "^PERSONAL_QUERIES_ENABLED=true" .env && grep -q "^IDENTITY_BACKEND=static" .env; then
  echo "ERROR: PERSONAL_QUERIES_ENABLED=true with IDENTITY_BACKEND=static exposes one user's"
  echo "coupons to everyone. Set IDENTITY_BACKEND=auth (plus AUTH_SIGNING_SECRET) or turn personal off."
  exit 1
fi

echo "=== 5/5 build + up (public stack) ==="
docker compose -f docker-compose.yml -f docker-compose.prod.yml build
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
sleep 5
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
curl -fsS http://localhost:8000/api/health || echo "app not healthy yet -- check: docker compose logs -f app"
echo "Done. Public URL: $(grep -E '^PUBLIC_URL=' .env || echo 'PUBLIC_URL not set')"
