#!/usr/bin/env bash
# Развернуть или обновить сервер LDE на машине с Docker и nginx (по ssh; повторный запуск — обновление).
#   lde-server/deploy/deploy.sh <ssh-хост> <домен>          например: my-vps lde.203-0-113-10.sslip.io
# Без домена: lde.<ip-через-дефисы>.sslip.io. Сайт nginx и сертификат создаются, только если их ещё нет;
# остальные сайты сервера не трогаются (nginx -t перед каждой перезагрузкой).
set -euo pipefail
HOST="${1:?ssh-хост}"; DOMAIN="${2:-}"
cd "$(dirname "$0")/../.."
if [ -z "$DOMAIN" ]; then
  IP=$(ssh -o BatchMode=yes "$HOST" 'curl -s https://api.ipify.org || hostname -I | cut -d" " -f1')
  DOMAIN="lde.${IP//./-}.sslip.io"
fi
echo "== сервер $HOST, адрес https://$DOMAIN"
tar czf - --exclude=__pycache__ --exclude=.env lde-server lde/studio/static/vendor/mathlive \
    lde/studio/static/index.html lde/studio/static/app.js lde/studio/static/app.css | \
  ssh -o BatchMode=yes "$HOST" 'mkdir -p /opt/lde-server-src && tar xzf - -C /opt/lde-server-src'
ssh -o BatchMode=yes "$HOST" DOMAIN="$DOMAIN" 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/lde-server-src
[ -f lde-server/.env ] || printf "LDE_PUBLIC_URL=https://%s\nLDE_PORT=8790\n" "$DOMAIN" > lde-server/.env
docker compose -f lde-server/compose.behind-proxy.yaml --env-file lde-server/.env up -d --build --remove-orphans 2>&1 | tail -3
for i in $(seq 30); do curl -sf http://127.0.0.1:8790/healthz >/dev/null && break; sleep 1; done
curl -sf http://127.0.0.1:8790/healthz && echo
S=lde-server/deploy
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
  sed "s/lde.EXAMPLE/$DOMAIN/g" $S/nginx-acme.conf > /etc/nginx/sites-available/lde
  ln -sf /etc/nginx/sites-available/lde /etc/nginx/sites-enabled/lde
  nginx -t && systemctl reload nginx
  certbot certonly --webroot -w /var/www/html -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email
fi
sed "s/lde.EXAMPLE/$DOMAIN/g" $S/nginx-site.conf > /tmp/lde-site.conf
if ! cmp -s /tmp/lde-site.conf /etc/nginx/sites-available/lde; then
  cp /tmp/lde-site.conf /etc/nginx/sites-available/lde
  ln -sf /etc/nginx/sites-available/lde /etc/nginx/sites-enabled/lde
  nginx -t && systemctl reload nginx
fi
echo "готово: https://$DOMAIN"
REMOTE
