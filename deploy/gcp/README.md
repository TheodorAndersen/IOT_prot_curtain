# GCP Production Deployment (Docker Compose + Nginx + Let's Encrypt)

This setup deploys the IoT Smart Curtain backend on a single GCP Compute Engine VM using:
- **`postgres:19-alpine`** on an isolated `internal: true` Docker bridge network (`db_internal`) with **zero** published host ports (never reachable from the internet or even from Nginx).
- **FastAPI backend** (`python:3.15-slim`) connected to `db_internal` (Postgres) and `app_net` (Nginx), with **zero** published host ports.
- **`nginx:1.31.9-alpine`** (pinned to `1.31.9+` / `1.30.7+` to avoid CVE-2026-123634) terminating TLS 1.2/1.3 on ports `80` / `443`.
- **`certbot/certbot:latest`** issuing and renewing Let's Encrypt certificates automatically every 12 hours.

---

## Option A: Scripted Component-by-Component Deployment (`deploy/gcp/scripts/`, Windows PowerShell)

All scripts read their settings from your local gitignored `.env` file (never committing private project IDs or domains). Fill in the `GCP_*`, `DEPLOY_*`, `DOMAIN`, and `CERTBOT_EMAIL` keys in `.env` first, then run and verify each component one step at a time:

| Step | Script | Purpose & What to Verify |
| :--- | :--- | :--- |
| 1 | `.\deploy\gcp\scripts\01-static-ip.ps1` | Reserves the regional static external IPv4 and writes `GCP_STATIC_IP` into `.env`. |
| 2 | `.\deploy\gcp\scripts\02-firewall.ps1` | Opens **only** `tcp:80,tcp:443` for tag `GCP_NETWORK_TAG` (keeps `5432` & `8000` closed). |
| 3 | `.\deploy\gcp\scripts\03-vm.ps1` | Creates the Ubuntu 24.04 VM (`e2-small`, 20 GB disk) with `vm-bootstrap.sh` (2 GB swap + Docker Engine + Compose). Wait 1-2 min and run the printed `gcloud compute ssh` check until `/var/log/curtain-bootstrap.done` exists. |
| 4 | `.\deploy\gcp\scripts\04-dns.ps1` | Creates/updates the Cloud DNS `A` record for `DOMAIN` → `GCP_STATIC_IP` and checks resolution. |
| 6 | `.\deploy\gcp\scripts\06-deploy-app.ps1` | Clones/pulls `DEPLOY_REPO_URL` into `DEPLOY_APP_DIR`, uploads `.env` (`chmod 600`), issues the Let's Encrypt cert on first run (`init-letsencrypt.sh`) or rebuilds on redeploy, and enables `curtain-backend.service`. |
| 7 | `.\deploy\gcp\scripts\07-verify.ps1` | Verifies DNS, VM swap, `docker compose ps` (zero published ports on Postgres/Backend), `systemd`, and `https://$DOMAIN/health`, `/api/docs`, and `/`. |
| 99 *(teardown)* | `.\deploy\gcp\scripts\99-teardown.ps1` | Dry-run by default. With `-ConfirmTeardown`, deletes the subdomain `A` record, VM (+ boot disk), firewall rule, and static IP while **preserving** the domain registration, Cloud DNS managed zone, and GCP project. |

**USB link (Settings → Connection → USB cable):** nothing to set. Step 6 starts the `mosquitto` service, and the PC with the board plugged in runs `python usb_bridge/mqtt_usb_bridge.py --server wss://$DOMAIN/mqtt` (see [`usb_bridge/README.md`](../../usb_bridge/README.md)). It goes over 443, so nothing new to open in the firewall. No login by default (the broker only carries the curtain's topics); to require one, set `USB_MQTT_PASSWORD` in `.env` here and in the bridge PC's `.env`.

---

## Option B: Manual Steps on the VM

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
