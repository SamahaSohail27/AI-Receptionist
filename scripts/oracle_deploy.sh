#!/usr/bin/env bash
# AI Receptionist — one-shot Oracle Cloud ARM VM deploy script.
#
# Run this ONCE on a fresh Ubuntu 22.04 Oracle A1 (ARM) VM as the `ubuntu` user.
# It installs Docker, opens host firewall, clones the repo, prompts for .env,
# starts the stack, and registers it as a systemd service so it auto-starts on reboot.
#
# Usage on the VM:
#   curl -fsSL https://raw.githubusercontent.com/SamahaSohail27/AI-Receptionist/main/scripts/oracle_deploy.sh | bash
# or:
#   git clone https://github.com/SamahaSohail27/AI-Receptionist.git && cd AI-Receptionist && bash scripts/oracle_deploy.sh

set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/ai-receptionist}"
REPO_URL="${REPO_URL:-https://github.com/SamahaSohail27/AI-Receptionist.git}"
BRANCH="${BRANCH:-main}"

log() { printf "\033[1;32m[deploy]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[deploy]\033[0m %s\n" "$*"; }
die() { printf "\033[1;31m[deploy]\033[0m %s\n" "$*" >&2; exit 1; }

[[ "$(id -un)" == "ubuntu" ]] || die "Run as the 'ubuntu' user, not root."

# ---------- 1. System packages ----------
log "Updating apt and installing base packages..."
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates curl gnupg lsb-release git ufw

# ---------- 2. Docker ----------
if ! command -v docker >/dev/null 2>&1; then
    log "Installing Docker Engine + Compose plugin..."
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -y
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    sudo usermod -aG docker "$USER"
    warn "Added $USER to docker group. You may need to re-login once."
else
    log "Docker already installed."
fi

# ---------- 3. Host firewall (Oracle has its own Security List too) ----------
log "Configuring host firewall (iptables on Ubuntu 22.04 Oracle image needs explicit allow)..."
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80   -j ACCEPT || true
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443  -j ACCEPT || true
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT || true
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8001 -j ACCEPT || true
sudo netfilter-persistent save 2>/dev/null || sudo apt-get install -y iptables-persistent

# ---------- 4. Clone or update repo ----------
if [[ -d "$APP_DIR/.git" ]]; then
    log "Repo already cloned at $APP_DIR — pulling latest."
    git -C "$APP_DIR" fetch --all
    git -C "$APP_DIR" checkout "$BRANCH"
    git -C "$APP_DIR" pull --ff-only
else
    log "Cloning $REPO_URL into $APP_DIR..."
    git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"

# ---------- 5. .env ----------
if [[ ! -f .env ]]; then
    warn ".env not found. Copying .env.example -> .env"
    cp .env.example .env
    warn "EDIT $APP_DIR/.env NOW with your real API keys, then re-run this script."
    warn "Open it with:  nano $APP_DIR/.env"
    exit 0
fi

# ---------- 6. Build & start ----------
log "Building and starting the stack (this takes a few minutes on first run)..."
sg docker -c "docker compose pull || true"
sg docker -c "docker compose up -d --build"

# ---------- 7. systemd unit for auto-start on reboot ----------
log "Installing systemd unit for auto-start..."
sudo tee /etc/systemd/system/ai-receptionist.service >/dev/null <<EOF
[Unit]
Description=AI Receptionist (docker compose)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0
User=$USER
Group=docker

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ai-receptionist.service

# ---------- 8. Status ----------
log "Done. Stack status:"
sg docker -c "docker compose ps"

PUBLIC_IP="$(curl -fsS https://api.ipify.org || echo 'YOUR-VM-IP')"
log "App should be reachable at:  http://$PUBLIC_IP:8000"
log "If it isn't, check Oracle Console -> VCN -> Security List -> Ingress Rules"
log "  and make sure ports 80, 443, 8000, 8001 are open from 0.0.0.0/0."
