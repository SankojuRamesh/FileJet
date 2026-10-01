#!/usr/bin/env bash
# Installs the MediaRush server (cloud + signaling) on Linux - no Docker. Ubuntu/Debian (apt) tested layout.
#
#   sudo bash deploy/linux/install.sh                                  # office network, plain HTTP (auto IP)
#   sudo bash deploy/linux/install.sh --address 192.168.1.20
#   sudo bash deploy/linux/install.sh --domain transfer.example.com   # internet, HTTPS via Caddy
#
# What it does: copies the server to /opt/mediarush, creates a venv, writes server.env with random secrets
# (kept on re-run), prepares the database (SQLite in /opt/mediarush/data), installs the systemd service
# "mediarush" (starts at boot, restarts on failure), opens the firewall (ufw if present) and, with --domain,
# installs Caddy for automatic HTTPS. Re-running it updates the files and keeps settings + database.
set -euo pipefail

INSTALL_DIR=/opt/mediarush
ADDRESS=""
DOMAIN=""
CLOUD_PORT=8000
SIGNAL_PORT=8765
REFLECTOR_PORT=8766
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    --address) ADDRESS="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --cloud-port) CLOUD_PORT="$2"; shift 2 ;;
    --signal-port) SIGNAL_PORT="$2"; shift 2 ;;
    --reflector-port) REFLECTOR_PORT="$2"; shift 2 ;;
    *) echo "unknown option $1"; exit 1 ;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo."; exit 1; }
SRC="$(cd "$(dirname "$0")/../.." && pwd)"
step() { printf '\n==> %s\n' "$1"; }
secret() { python3 -c "import secrets; print(secrets.token_urlsafe(48))"; }

step "Installing system packages"
if command -v apt-get >/dev/null; then
  apt-get update -qq
  apt-get install -y -qq python3 python3-venv python3-pip rsync >/dev/null
fi
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'

step "Copying server files to $INSTALL_DIR"
id mediarush >/dev/null 2>&1 || useradd --system --home "$INSTALL_DIR" --shell /usr/sbin/nologin mediarush
mkdir -p "$INSTALL_DIR/data"
for item in cloud server serve.py requirements.txt deploy; do
  rsync -a --delete --exclude __pycache__ --exclude staticfiles --exclude db.sqlite3 "$SRC/$item" "$INSTALL_DIR/"
done

step "Creating the Python environment"
[ -x "$INSTALL_DIR/.venv/bin/python" ] || python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/cloud/requirements.txt" -r "$INSTALL_DIR/server/requirements.txt"

if [ -z "$ADDRESS" ]; then ADDRESS="$(hostname -I 2>/dev/null | awk '{print $1}')"; fi
if [ -n "$DOMAIN" ]; then
  CLOUD_URL="https://$DOMAIN/"; SIGNAL_URL="wss://$DOMAIN/ws"; HOSTS="$DOMAIN,localhost,127.0.0.1"; HTTPS=1
  CLOUD_HOST=127.0.0.1; PUBLIC_HOST="$DOMAIN"; TRUST=1
else
  CLOUD_URL="http://$ADDRESS:$CLOUD_PORT/"; SIGNAL_URL="ws://$ADDRESS:$SIGNAL_PORT/ws"
  HOSTS="$ADDRESS,$(hostname),localhost,127.0.0.1"; HTTPS=0; CLOUD_HOST=0.0.0.0; PUBLIC_HOST="$ADDRESS"; TRUST=0
fi

ENV_FILE="$INSTALL_DIR/server.env"
if [ -f "$ENV_FILE" ]; then
  step "Keeping existing settings: $ENV_FILE"
else
  step "Writing settings: $ENV_FILE"
  cat > "$ENV_FILE" <<EOF
# MediaRush server settings (written by install.sh). After changes: sudo systemctl restart mediarush
DJANGO_SECRET_KEY=$(secret)
P2P_CLOUD_JWT_SECRET=$(secret)
DJANGO_ALLOWED_HOSTS=$HOSTS
DJANGO_CSRF_TRUSTED_ORIGINS=${CLOUD_URL%/}
DJANGO_HTTPS=$HTTPS
DJANGO_SQLITE_PATH=$INSTALL_DIR/data/mediarush.sqlite3
# DATABASE_URL=postgres://user:password@127.0.0.1:5432/mediarush   (optional instead of SQLite)
CLOUD_PUBLIC_URL=$CLOUD_URL
P2P_SIGNALING_URL=$SIGNAL_URL
P2P_PUBLIC_HOST=$PUBLIC_HOST
P2P_TRUST_PROXY=$TRUST
HOST=0.0.0.0
CLOUD_HOST=$CLOUD_HOST
CLOUD_PORT=$CLOUD_PORT
SIGNAL_PORT=$SIGNAL_PORT
REFLECTOR_PORT=$REFLECTOR_PORT
BILLING_PROVIDER=dummy

# E-mail: until SMTP is set, invitations are shown on the web under "Inbox" (and in the log).
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
SHOW_EMAIL_OUTBOX=1
# EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
# EMAIL_HOST=smtp.example.com
# EMAIL_PORT=587
# EMAIL_HOST_USER=
# EMAIL_HOST_PASSWORD=
# EMAIL_USE_TLS=1
# DEFAULT_FROM_EMAIL=MediaRush <no-reply@example.com>
# SHOW_EMAIL_OUTBOX=0
EOF
  chmod 600 "$ENV_FILE"
fi
chown -R mediarush:mediarush "$INSTALL_DIR"

step "Preparing the database"
sudo -u mediarush "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/serve.py" --env "$ENV_FILE" --check

step "Installing the systemd service 'mediarush'"
cat > /etc/systemd/system/mediarush.service <<EOF
[Unit]
Description=MediaRush server (cloud + signaling)
After=network-online.target
Wants=network-online.target

[Service]
User=mediarush
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python $INSTALL_DIR/serve.py --env $ENV_FILE
Restart=always
RestartSec=3
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now mediarush
systemctl restart mediarush

if command -v ufw >/dev/null && ufw status | grep -q active; then
  step "Opening the firewall (ufw)"
  if [ -n "$DOMAIN" ]; then ufw allow 80,443,"$REFLECTOR_PORT"/tcp; else ufw allow "$CLOUD_PORT","$SIGNAL_PORT","$REFLECTOR_PORT"/tcp; fi
fi

if [ -n "$DOMAIN" ]; then
  step "HTTPS with Caddy for $DOMAIN"
  if ! command -v caddy >/dev/null && command -v apt-get >/dev/null; then
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gnupg >/dev/null
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq && apt-get install -y -qq caddy >/dev/null
  fi
  cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
	encode zstd gzip
	reverse_proxy /ws 127.0.0.1:$SIGNAL_PORT
	reverse_proxy /ws/presence 127.0.0.1:$SIGNAL_PORT
	reverse_proxy /healthz 127.0.0.1:$SIGNAL_PORT
	reverse_proxy 127.0.0.1:$CLOUD_PORT
}
EOF
  systemctl reload caddy || systemctl restart caddy
fi

step "Done"
echo "Cloud URL for the MediaRush app:  $CLOUD_URL"
echo "Status:   systemctl status mediarush      Logs: journalctl -u mediarush -f"
echo "Admin:    sudo -u mediarush $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/serve.py --env $ENV_FILE manage createsuperuser"
echo "Check:    curl ${CLOUD_URL}api/config/"
