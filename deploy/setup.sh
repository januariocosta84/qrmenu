#!/usr/bin/env bash
# One-shot server setup for Ubuntu 22.04/24.04. Run as root on the VPS:
#
#   git clone <repo-url> /srv/qrmenu/app
#   sudo DOMAIN=qrmenu.timorstore.com EMAIL=you@example.com bash /srv/qrmenu/app/deploy/setup.sh
#
# Safe to run again (e.g. after `git pull`): it updates dependencies,
# migrates the database, collects static files and restarts the app.
set -euo pipefail

DOMAIN="${DOMAIN:-qrmenu.timorstore.com}"
EMAIL="${EMAIL:?Set EMAIL=you@example.com (used for HTTPS certificate notices)}"
APP=/srv/qrmenu/app
MEDIA=/srv/qrmenu/media
DB_NAME=qrmenu
DB_USER=qrmenu

echo "==> Packages"
apt-get update -q
apt-get install -yq python3-venv python3-dev build-essential postgresql redis-server nginx certbot python3-certbot-nginx \
  fonts-noto-color-emoji

echo "==> System user and folders"
id qrmenu >/dev/null 2>&1 || useradd --system --home /srv/qrmenu --shell /usr/sbin/nologin qrmenu
mkdir -p "$MEDIA"
chown -R qrmenu:www-data /srv/qrmenu
chmod 750 /srv/qrmenu

echo "==> PostgreSQL"
if [ ! -f "$APP/.env" ]; then
  DB_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 \
    && sudo -u postgres psql -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';" \
    || sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';"
  sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 \
    || sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"

  echo "==> Writing $APP/.env (edit it later for email/SMTP and billing details)"
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
  cat > "$APP/.env" <<ENV
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=$SECRET
DJANGO_ALLOWED_HOSTS=$DOMAIN
DJANGO_CSRF_TRUSTED_ORIGINS=https://$DOMAIN
PUBLIC_BASE_URL=https://$DOMAIN
DATABASE_URL=postgres://$DB_USER:$DB_PASS@127.0.0.1:5432/$DB_NAME
REDIS_URL=redis://127.0.0.1:6379/0
NUM_PROXIES=1
MEDIA_ROOT=$MEDIA
TIME_ZONE=Asia/Dili
PLATFORM_NAME=QR Menu
SIGNUP_MODE=approval
ADMIN_URL=manage-$(python3 -c 'import secrets; print(secrets.token_hex(4))')/
CASH_DRAWER_NETWORK_ENABLED=false
# SMTP (needed for sign-up confirmation and password reset):
# EMAIL_HOST=smtp.example.com
# EMAIL_PORT=587
# EMAIL_HOST_USER=no-reply@timorstore.com
# EMAIL_HOST_PASSWORD=change-me
# EMAIL_USE_TLS=true
# DEFAULT_FROM_EMAIL=QR Menu <no-reply@timorstore.com>
# SUPPORT_EMAIL=support@timorstore.com
# PLATFORM_ADMINS=Your Name <you@example.com>
ENV
  chown qrmenu:www-data "$APP/.env"
  chmod 640 "$APP/.env"
fi

echo "==> Python dependencies"
cd "$APP"
[ -d .venv ] || sudo -u qrmenu python3 -m venv .venv
sudo -u qrmenu .venv/bin/pip install -q --upgrade pip
sudo -u qrmenu .venv/bin/pip install -q -r requirements.txt

echo "==> Database and static files"
sudo -u qrmenu .venv/bin/python manage.py migrate --noinput
sudo -u qrmenu .venv/bin/python manage.py collectstatic --noinput -v 0
sudo -u qrmenu .venv/bin/python manage.py check --deploy --fail-level ERROR

echo "==> App service"
cp deploy/qrmenu.service /etc/systemd/system/qrmenu.service
systemctl daemon-reload
systemctl enable --now qrmenu
systemctl restart qrmenu

echo "==> Daily renewal invoices (06:00)"
cat > /etc/cron.d/qrmenu <<CRON
0 6 * * * qrmenu cd $APP && .venv/bin/python manage.py generate_invoices >> /var/log/qrmenu-invoices.log 2>&1
CRON
touch /var/log/qrmenu-invoices.log && chown qrmenu /var/log/qrmenu-invoices.log

echo "==> Nginx + HTTPS for $DOMAIN"
if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
  # First run: plain HTTP so certbot can verify the domain, then it adds SSL.
  cat > /etc/nginx/sites-available/qrmenu <<NGINX
server {
    listen 80;
    server_name $DOMAIN;
    location / { proxy_pass http://127.0.0.1:8000; proxy_set_header Host \$host; }
}
NGINX
  ln -sf /etc/nginx/sites-available/qrmenu /etc/nginx/sites-enabled/qrmenu
  rm -f /etc/nginx/sites-enabled/default
  nginx -t && systemctl reload nginx
  certbot certonly --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL"
fi
sed "s/qrmenu.timorstore.com/$DOMAIN/g" deploy/nginx.conf > /etc/nginx/sites-available/qrmenu
ln -sf /etc/nginx/sites-available/qrmenu /etc/nginx/sites-enabled/qrmenu
nginx -t && systemctl reload nginx

ADMIN_PATH="$(grep '^ADMIN_URL=' .env | cut -d= -f2)"
cat <<DONE

Done:  https://$DOMAIN

Next steps:
  1. Create your platform owner account:
       cd $APP && sudo -u qrmenu .venv/bin/python manage.py createsuperuser
  2. Log in at https://$DOMAIN/accounts/login/ and open https://$DOMAIN/platform/
     (Django admin: https://$DOMAIN/$ADMIN_PATH)
  3. Add SMTP settings to $APP/.env, then: systemctl restart qrmenu
  4. Platform console → Billing settings: add your payment instructions.

Update later:  cd $APP && sudo -u qrmenu git pull && sudo EMAIL=$EMAIL bash deploy/setup.sh
DONE
