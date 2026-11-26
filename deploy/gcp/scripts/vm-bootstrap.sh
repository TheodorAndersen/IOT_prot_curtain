#!/usr/bin/env bash
set -euo pipefail

LOG_FILE="/var/log/curtain-bootstrap.log"
DONE_FILE="/var/log/curtain-bootstrap.done"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "[curtain-bootstrap] Starting VM bootstrap at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [[ ! -f /swapfile ]]; then
  echo "[curtain-bootstrap] Creating 2G swapfile..."
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  if ! grep -q '^/swapfile ' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
  sysctl -w vm.swappiness=10
  if ! grep -q '^vm.swappiness=' /etc/sysctl.conf; then
    echo 'vm.swappiness=10' >> /etc/sysctl.conf
  fi
else
  echo "[curtain-bootstrap] /swapfile already present."
  swapon /swapfile || true
fi

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "[curtain-bootstrap] Installing Docker Engine, Compose plugin, git, and curl..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg git

  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
  fi

  CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME}")"
  ARCH="$(dpkg --print-architecture)"
  echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list

  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
else
  echo "[curtain-bootstrap] Docker and Compose plugin already installed."
fi

date -u +%Y-%m-%dT%H:%M:%SZ > "$DONE_FILE"
echo "[curtain-bootstrap] Bootstrap complete."
