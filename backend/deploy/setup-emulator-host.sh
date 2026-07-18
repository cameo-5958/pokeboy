#!/usr/bin/env bash
# One-time host setup for emulator.cameo.moe. Run with sudo; idempotent.
#
# Prerequisite: a Cloudflare A record `emulator` -> this host's public IP,
# set to "DNS only" (grey cloud, like pokeboy.cameo.moe) — certbot validates
# over plain HTTP on port 80.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
domain=emulator.cameo.moe
conf_src="$here/$domain.conf"
conf_dst="/etc/nginx/sites-available/$domain.conf"

[[ $EUID -eq 0 ]] || { echo "error: run with sudo" >&2; exit 1; }
[[ -f $conf_src ]] || { echo "error: $conf_src missing" >&2; exit 1; }

if [[ ! -d /etc/letsencrypt/live/$domain ]]; then
    # Stage A: install only the port-80 server block. The 8443 block references
    # certificate files that don't exist yet, and nginx refuses to load a conf
    # with missing certs — so the cert has to be minted first.
    awk '/^server \{/ { block++ } block == 1' "$conf_src" > "$conf_dst"
    ln -sf "$conf_dst" "/etc/nginx/sites-enabled/$domain.conf"
    nginx -t
    systemctl reload nginx
    # Webroot challenge: the certbot nginx plugin isn't installed on this host.
    mkdir -p /var/www/letsencrypt
    certbot certonly --webroot -w /var/www/letsencrypt -d "$domain" --non-interactive --keep
fi

install -m 644 "$conf_src" "$conf_dst"
ln -sf "$conf_dst" "/etc/nginx/sites-enabled/$domain.conf"
nginx -t
systemctl reload nginx

echo "OK — https://$domain is live (backend must be running on :4000)"
