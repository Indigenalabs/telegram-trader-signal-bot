#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/telegram-trader-signal-bot}"
APP_USER="${APP_USER:-traderbot}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="$APP_DIR/.venv"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

apt-get update
apt-get install -y git python3 python3-venv python3-pip

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /bin/bash "$APP_USER"
fi

mkdir -p "$APP_DIR"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "Clone the repository into $APP_DIR before running setup.sh." >&2
  exit 1
fi

sudo -u "$APP_USER" "$PYTHON_BIN" -m venv "$VENV_DIR"
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install -e "$APP_DIR"

mkdir -p "$APP_DIR/data" "$APP_DIR/logs"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR/data" "$APP_DIR/logs"

cat <<EOF
Setup complete.

Next steps:
1. Copy .env.example to $APP_DIR/.env
2. Fill in TELEGRAM_BOT_TOKEN and other secrets
3. Copy deploy/hetzner/telegram-trader-signal-bot.service to /etc/systemd/system/
4. Run:
   systemctl daemon-reload
   systemctl enable telegram-trader-signal-bot
   systemctl start telegram-trader-signal-bot
   systemctl status telegram-trader-signal-bot
EOF
