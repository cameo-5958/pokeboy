#!/usr/bin/env bash
set -euo pipefail

# Idempotent deployment for the Pokeboy backend and its public Battle Link
# console. Run from this checkout with sudo, or override the values below:
#   sudo DOMAIN=pokeboy.cameo.moe PORT=4000 POKEBOY_USER=cameo ./backend/scripts/setup-battle-link-server.sh

DOMAIN="${DOMAIN:-pokeboy.cameo.moe}"
PORT="${PORT:-4000}"
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
POKEBOY_USER="${POKEBOY_USER:-${SUDO_USER:-$(id -un)}}"
SERVICE_NAME="${SERVICE_NAME:-pokeboy-backend}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
NGINX_SITE="/etc/nginx/sites-available/${DOMAIN}"
NGINX_ENABLED="/etc/nginx/sites-enabled/${DOMAIN}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo so it can configure systemd and nginx." >&2
  exit 1
fi
for command in node npm nginx systemctl curl sudo; do
  command -v "${command}" >/dev/null || { echo "Missing required command: ${command}" >&2; exit 1; }
done
id "${POKEBOY_USER}" >/dev/null 2>&1 || { echo "Unknown service user: ${POKEBOY_USER}" >&2; exit 1; }
[[ -f "${REPO_DIR}/backend/package.json" ]] || { echo "Invalid REPO_DIR: ${REPO_DIR}" >&2; exit 1; }

echo "Building backend in ${REPO_DIR}/backend"
sudo -u "${POKEBOY_USER}" npm --prefix "${REPO_DIR}/backend" ci
sudo -u "${POKEBOY_USER}" npm --prefix "${REPO_DIR}/backend" run build

cat >"${SERVICE_FILE}" <<EOF
[Unit]
Description=Pokeboy backend and Battle Link command server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${POKEBOY_USER}
WorkingDirectory=${REPO_DIR}/backend
Environment=NODE_ENV=production
Environment=PORT=${PORT}
ExecStart=$(command -v node) ${REPO_DIR}/backend/dist/index.js
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

# Preserve an existing vhost: this domain already normally proxies the entire
# Pokeboy backend, which automatically exposes /battle-link after restart.
if ! nginx -T 2>/dev/null | grep -qE "server_name[[:space:]]+${DOMAIN//./\\.}([[:space:]]|;)"; then
  echo "Creating nginx site for ${DOMAIN}"
  mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
  if [[ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
    cat >"${NGINX_SITE}" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};
    return 301 https://\$host\$request_uri;
}
server {
    listen 443 ssl http2;
    server_name ${DOMAIN};
    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    large_client_header_buffers 4 32k;
    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 35s;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
  else
    cat >"${NGINX_SITE}" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};
    large_client_header_buffers 4 32k;
    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 35s;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
    echo "TLS certificate not found. Install one for ${DOMAIN}, then rerun this script." >&2
  fi
  ln -sfn "${NGINX_SITE}" "${NGINX_ENABLED}"
else
  echo "Existing nginx vhost for ${DOMAIN} preserved. Ensure it proxies / to 127.0.0.1:${PORT}."
fi

nginx -t
systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"
systemctl reload nginx

echo "Waiting for Battle Link health check"
for _ in {1..20}; do
  if curl --fail --silent --show-error "http://127.0.0.1:${PORT}/battle-link/health" >/dev/null; then
    echo "Battle Link is ready: https://${DOMAIN}/battle-link"
    exit 0
  fi
  sleep 1
done
echo "Service did not become healthy; inspect: journalctl -u ${SERVICE_NAME} -n 100" >&2
exit 1
