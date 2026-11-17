#!/usr/bin/env bash
# Installs Data Architect Mentor on an Ubuntu server (Oracle Cloud Always Free) and runs it as a service.
# Run as the default 'ubuntu' user from the folder that holds mentor.tar.gz:   bash setup_server.sh
set -euo pipefail
APP=/opt/data-mentor

echo "== System packages"
sudo apt-get update -y -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3 python3-venv python3-pip curl tzdata

echo "== ngrok (official apt repository)"
if ! command -v ngrok >/dev/null; then
  curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
  echo "deb https://ngrok-agent.s3.amazonaws.com bookworm main" | sudo tee /etc/apt/sources.list.d/ngrok.list >/dev/null
  sudo apt-get update -y -q && sudo apt-get install -y -q ngrok
fi

echo "== Memory safety net (small servers have 1 GB)"
if [ "$(free -m | awk '/Mem:/{print $2}')" -lt 2000 ] && [ ! -f /swapfile ]; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== Mentor files"
sudo mkdir -p "$APP" && sudo chown "$USER":"$USER" "$APP"
tar -xzf mentor.tar.gz -C "$APP"
find "$APP" -maxdepth 1 -name '*.sh' -exec sed -i 's/\r$//' {} \;
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

echo "== Service (starts on boot, restarts if it stops)"
sudo tee /etc/systemd/system/data-mentor.service >/dev/null <<EOF
[Unit]
Description=Data Architect Mentor (website, Telegram bot, reminders, public link)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/python app.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
sudo timedatectl set-timezone Asia/Kolkata || true
sudo systemctl daemon-reload
sudo systemctl enable --now data-mentor
sleep 20
systemctl is-active data-mentor && echo "== Mentor is running"
grep -E "PUBLIC LINK|Website:|ERROR" "$APP/data/mentor.log" | tail -5 || true
