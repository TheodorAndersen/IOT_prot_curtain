# GCP Production Deployment (Docker Compose + Nginx + Let's Encrypt)

This setup deploys the IoT Smart Curtain backend on a single GCP Compute Engine VM using:
- **`postgres:19-alpine`** on an isolated `internal: true` Docker bridge network (`db_internal`) with **zero** published host ports (never reachable from the internet or even from Nginx).
- **FastAPI backend** (`python:3.15-slim`) connected to `db_internal` (Postgres) and `app_net` (Nginx), with **zero** published host ports.
- **`nginx:1.31.9-alpine`** (pinned to `1.31.9+` / `1.30.7+` to avoid CVE-2026-123634) terminating TLS 1.2/1.3 on ports `80` / `443`.
- **`certbot/certbot:latest`** issuing and renewing Let's Encrypt certificates automatically every 12 hours.

---

## 1. Provision GCP VM & Firewall Rules

1. Create an Ubuntu/Debian Compute Engine VM (e.g., `e2-micro` or `e2-small`) with a static external IP, tagged e.g. `curtain-https`, and point your DNS `A` record (`curtain.example.com`) to that IP.
2. Open **only** ports `80/tcp` and `443/tcp` in the GCP VPC firewall (keep `5432` and `8000` closed):

```bash
gcloud compute firewall-rules create curtain-allow-http-https \
  --allow=tcp:80,tcp:443 \
  --target-tags=curtain-https \
  --source-ranges=0.0.0.0/0
```

## 2. Install Docker & Configure `.env` on the VM

1. Install Docker Engine and the Docker Compose plugin (`docker compose version`).
2. Copy the repository onto the VM (e.g., `/opt/iot_prot_curtain`).
3. Create `.env` from `.env.example` and lock down permissions:

```bash
cp .env.example .env
chmod 600 .env
```

Set at minimum in `.env`:
- `MQTT_API_KEY`, `TTN_APPID`, `TTN_DEVID`, `TTN_REGION`
- `POSTGRES_USER=curtain`
- `POSTGRES_PASSWORD=<strong-unique-password>`
- `POSTGRES_DB=curtain`
- `DOMAIN=curtain.example.com`
- `CERTBOT_EMAIL=you@example.com`

## 3. Issue SSL Certificate & Start Production Stack

```bash
chmod +x deploy/gcp/init-letsencrypt.sh
./deploy/gcp/init-letsencrypt.sh
```

`init-letsencrypt.sh` starts PostgreSQL, the backend, and Nginx on port 80, obtains the Let's Encrypt certificate via HTTP-01, switches Nginx to the HTTPS template (`deploy/nginx/default.https.conf.template`), starts the automatic renewal container, and verifies `https://$DOMAIN/health`.

## 4. Enable Auto-Start on Reboot (`systemd`)

```bash
sudo cp deploy/gcp/curtain-backend.service /etc/systemd/system/curtain-backend.service
sudo sed -i "s|/opt/iot_prot_curtain|$(pwd)|g" /etc/systemd/system/curtain-backend.service
sudo systemctl daemon-reload
sudo systemctl enable --now curtain-backend.service
```

## 5. Verify & Connect Frontend

- Confirm PostgreSQL has **zero** host bindings (`docker compose ps` shows no ports on `postgres` or `backend`, only `0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp` on `nginx`).
- Confirm HTTPS works from outside: `curl -fsS https://curtain.example.com/health`.
- Open the React web/PWA frontend at `https://<your-domain>/` (`/api/*` is proxied to `backend:8000` and the protocol inspector lives at `https://<your-domain>/debug`).
- For Capacitor native mobile builds, set `VITE_API_BASE_URL=https://<your-domain>/api` when building `FrontEnd/` before running `npx cap sync`.
