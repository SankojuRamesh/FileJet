#!/usr/bin/env bash
# Installs the FileJet server (cloud + signaling) on Linux - no Docker. Ubuntu/Debian (apt) tested layout.
#
#   sudo bash deploy/linux/install.sh                                  # office network, plain HTTP (auto IP)
#   sudo bash deploy/linux/install.sh --address 192.168.1.20
#   sudo bash deploy/linux/install.sh --domain filejet.live        # internet, plain HTTP via nginx (or Caddy)
#   sudo bash deploy/linux/install.sh --domain filejet.live --https   # optional: HTTPS certificate (certbot)
#
# What it does: copies the server to /opt/mediarush, creates a venv, writes server.env with random secrets
# (kept on re-run), prepares the database (SQLite in /opt/mediarush/data), installs the systemd service
# "mediarush" (starts at boot, restarts on failure), opens the firewall (ufw if present) and, with --domain,
# sets up HTTPS (nginx + certbot when nginx is installed, else Caddy). Re-running it updates the files and keeps settings + database.
set -euo pipefail

INSTALL_DIR=/opt/mediarush
ADDRESS=""
DOMAIN=""
CLOUD_PORT=8000
SIGNAL_PORT=8765
REFLECTOR_PORT=8766
WANT_HTTPS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    --address) ADDRESS="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --cloud-port) CLOUD_PORT="$2"; shift 2 ;;
    --signal-port) SIGNAL_PORT="$2"; shift 2 ;;
    --reflector-port) REFLECTOR_PORT="$2"; shift 2 ;;
    --https) WANT_HTTPS=1; shift ;;
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
  CLOUD_URL="http://$DOMAIN/"; SIGNAL_URL="ws://$DOMAIN/ws"; HOSTS="$DOMAIN,www.$DOMAIN,localhost,127.0.0.1"; HTTPS=0
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
# FileJet server settings (written by install.sh). After changes: sudo systemctl restart mediarush
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
# DEFAULT_FROM_EMAIL=FileJet <no-reply@example.com>
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
Description=FileJet server (cloud + signaling)
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
  if [ -n "$DOMAIN" ]; then ufw allow 80,"$REFLECTOR_PORT"/tcp; [ "$WANT_HTTPS" = 1 ] && ufw allow 443/tcp; else ufw allow "$CLOUD_PORT","$SIGNAL_PORT","$REFLECTOR_PORT"/tcp; fi
fi

set_env() {   # set_env KEY VALUE  -> update server.env
  if grep -q "^$1=" "$ENV_FILE"; then sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"; else echo "$1=$2" >> "$ENV_FILE"; fi
}

if [ -n "$DOMAIN" ] && command -v nginx >/dev/null; then
  step "nginx found - configuring nginx for $DOMAIN"
  # other enabled sites for the same domain would shadow this one: disable them (backup kept)
  for f in /etc/nginx/sites-enabled/*; do
    [ -e "$f" ] || continue
    [ "$(basename "$f")" = "mediarush" ] && continue
    if grep -qE "server_name[^;]*\b$DOMAIN\b" "$f"; then
      echo "  disabling $f (it also serves $DOMAIN); backup: $f.disabled-by-mediarush"
      mv "$f" "$f.disabled-by-mediarush"
    fi
  done
  sed -e "s/filejet\.live/$DOMAIN/g" -e "s/127\.0\.0\.1:8000/127.0.0.1:$CLOUD_PORT/" \
      -e "s/127\.0\.0\.1:8765/127.0.0.1:$SIGNAL_PORT/g" "$INSTALL_DIR/deploy/nginx/mediarush.conf" \
      > /etc/nginx/sites-available/mediarush
  ln -sf /etc/nginx/sites-available/mediarush /etc/nginx/sites-enabled/mediarush
  nginx -t && systemctl reload nginx
  if command -v ufw >/dev/null && ufw status | grep -q active; then ufw allow 'Nginx HTTP' >/dev/null || true; fi
  set_env DJANGO_HTTPS 0
  set_env CLOUD_PUBLIC_URL "http://$DOMAIN/"
  set_env P2P_SIGNALING_URL "ws://$DOMAIN/ws"

  if [ "$WANT_HTTPS" = 1 ]; then
  step "HTTPS certificate (Let's Encrypt)"
  if command -v apt-get >/dev/null && ! command -v certbot >/dev/null; then
    apt-get install -y -qq certbot python3-certbot-nginx >/dev/null
  fi
  CERT_ARGS=(-d "$DOMAIN")
  if getent hosts "www.$DOMAIN" >/dev/null; then CERT_ARGS+=(-d "www.$DOMAIN"); fi
  if [ -n "${CERT_EMAIL:-}" ]; then CERT_ARGS+=(-m "$CERT_EMAIL"); else CERT_ARGS+=(--register-unsafely-without-email); fi
  if certbot --nginx "${CERT_ARGS[@]}" --non-interactive --agree-tos --redirect; then
    set_env DJANGO_HTTPS 1
    set_env CLOUD_PUBLIC_URL "https://$DOMAIN/"
    set_env P2P_SIGNALING_URL "wss://$DOMAIN/ws"
    CLOUD_URL="https://$DOMAIN/"
  else
    echo "!! Could not get a certificate (DNS must point to this server and ports 80/443 must be open)." >&2
    echo "!! Running on plain HTTP for now - re-run this script once that is fixed." >&2
    set_env DJANGO_HTTPS 0
    set_env CLOUD_PUBLIC_URL "http://$DOMAIN/"
    set_env P2P_SIGNALING_URL "ws://$DOMAIN/ws"
    CLOUD_URL="http://$DOMAIN/"
  fi
  fi
  systemctl restart mediarush

elif [ -n "$DOMAIN" ]; then
  step "Caddy reverse proxy for $DOMAIN"
  if ! command -v caddy >/dev/null && command -v apt-get >/dev/null; then
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gnupg >/dev/null
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq && apt-get install -y -qq caddy >/dev/null
  fi
  SITE="http://$DOMAIN"; [ "$WANT_HTTPS" = 1 ] && SITE="$DOMAIN"
  cat > /etc/caddy/Caddyfile <<EOF
$SITE {
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
echo "Cloud URL for the FileJet app:  $CLOUD_URL"
echo "Status:   systemctl status mediarush      Logs: journalctl -u mediarush -f"
echo "Admin:    sudo -u mediarush $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/serve.py --env $ENV_FILE manage createsuperuser"
echo "Check:    curl ${CLOUD_URL}api/config/"
