#!/usr/bin/env bash
# Run once on a fresh Hostinger Ubuntu VPS as root.
# Usage: bash deploy/setup-vps.sh
set -euo pipefail

APP_DIR=/var/www/reviewhub
DOMAIN=reviewhub.technobuzzsystems.com
DB_NAME=googlereviews
DB_USER=reviewhub
DB_PASS="${DB_PASS:-$(openssl rand -hex 12)}"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip python3-dev \
  postgresql postgresql-contrib nginx certbot python3-certbot-nginx \
  build-essential libpq-dev git rsync

mkdir -p "$APP_DIR"
chown -R www-data:www-data "$APP_DIR"

sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASS}';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};"

if [[ ! -d "${APP_DIR}/.venv" ]]; then
  sudo -u www-data python3 -m venv "${APP_DIR}/.venv"
fi
sudo -u www-data "${APP_DIR}/.venv/bin/pip" install --upgrade pip
if [[ -f "${APP_DIR}/requirements.txt" ]]; then
  sudo -u www-data "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
fi

if [[ ! -f "${APP_DIR}/.env" ]]; then
  if [[ -f "${APP_DIR}/deploy/env.production.example" ]]; then
    cp "${APP_DIR}/deploy/env.production.example" "${APP_DIR}/.env"
    sed -i "s|CHANGE_DB_PASSWORD|${DB_PASS}|g" "${APP_DIR}/.env"
    sed -i "s|replace-with-a-long-random-string|$(openssl rand -hex 32)|g" "${APP_DIR}/.env"
    chown www-data:www-data "${APP_DIR}/.env"
    chmod 600 "${APP_DIR}/.env"
    echo "Created ${APP_DIR}/.env — edit it and add Razorpay / Gemini / S3 keys."
    echo "PostgreSQL user ${DB_USER} password: ${DB_PASS}"
  fi
fi

cp "${APP_DIR}/deploy/reviewhub.service" /etc/systemd/system/reviewhub.service
cp "${APP_DIR}/deploy/nginx-reviewhub.conf" /etc/nginx/sites-available/reviewhub
ln -sfn /etc/nginx/sites-available/reviewhub /etc/nginx/sites-enabled/reviewhub
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable reviewhub
systemctl restart reviewhub
systemctl reload nginx

echo
echo "App should answer on http://${DOMAIN} once DNS A record points to this VPS."
echo "Then enable SSL:"
echo "  certbot --nginx -d ${DOMAIN}"
echo "Restart after editing .env:"
echo "  systemctl restart reviewhub"
