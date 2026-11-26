#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f ".env" ]]; then
  echo "ERROR: .env not found in $ROOT_DIR" >&2
  exit 1
fi

while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%$'\r'}"
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
  if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
    key="${BASH_REMATCH[1]}"
    val="${BASH_REMATCH[2]}"
    if [[ "$val" =~ ^\"(.*)\"$ || "$val" =~ ^\'(.*)\'$ ]]; then
      val="${BASH_REMATCH[1]}"
    fi
    export "${key}=${val}"
  fi
done < ".env"

: "${DOMAIN:?DOMAIN must be set in .env}"
: "${CERTBOT_EMAIL:?CERTBOT_EMAIL must be set in .env}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set in .env}"

BOOTSTRAP_COMPOSE="$(mktemp)"
cleanup() {
  rm -f "$BOOTSTRAP_COMPOSE"
}
trap cleanup EXIT

cat > "$BOOTSTRAP_COMPOSE" <<'EOF'
services:
  postgres:
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set in .env}
  backend:
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set in .env}
  nginx:
    ports: !override
      - "80:80"
    volumes: !override
      - ./deploy/nginx/default.http.conf:/etc/nginx/conf.d/default.conf:ro
      - certbot_www:/var/www/certbot:ro
volumes:
  certbot_www:
EOF

echo "[1/3] Starting HTTP-only stack for ACME HTTP-01 challenge..."
docker compose -f docker-compose.yml -f "$BOOTSTRAP_COMPOSE" up -d --build postgres backend nginx

echo "[2/3] Requesting Let's Encrypt certificate for $DOMAIN..."
docker compose -f docker-compose.yml -f deploy/gcp/docker-compose.prod.yml run --rm --no-deps \
  certbot certonly --webroot -w /var/www/certbot \
  -d "$DOMAIN" \
  --email "$CERTBOT_EMAIL" \
  --agree-tos --no-eff-email --non-interactive

echo "[3/3] Starting full HTTPS production stack..."
docker compose -f docker-compose.yml -f deploy/gcp/docker-compose.prod.yml up -d --build --force-recreate nginx certbot

echo "Verifying HTTPS health endpoint..."
curl -fsS "https://${DOMAIN}/health"
echo
echo "Done! Backend is live at https://${DOMAIN}"
