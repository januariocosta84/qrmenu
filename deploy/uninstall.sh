#!/usr/bin/env bash
# Removes QR Menu and ONLY QR Menu from the server (other apps are not touched).
#   sudo bash /srv/qrmenu/app/deploy/uninstall.sh            # keeps database + uploaded images (backup in /root)
#   sudo PURGE=1 bash /srv/qrmenu/app/deploy/uninstall.sh    # also deletes the database, files and certificate
set -euo pipefail
NAME=qrmenu; BASE=/srv/qrmenu; DOMAIN="${DOMAIN:-qrmenu.timorstore.com}"
[ "$(id -u)" = 0 ] || { echo "Run as root."; exit 1; }
[ -f "$BASE/.qrmenu-installed" ] || { echo "No QR Menu install found in $BASE."; exit 1; }

STAMP=$(date +%Y%m%d-%H%M%S)
if command -v pg_dump >/dev/null && sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$NAME'" | grep -q 1; then
  sudo -u postgres pg_dump "$NAME" | gzip > "/root/qrmenu-db-$STAMP.sql.gz" && echo "Database backup: /root/qrmenu-db-$STAMP.sql.gz"
fi
[ -d "$BASE/media" ] && tar czf "/root/qrmenu-media-$STAMP.tgz" -C "$BASE" media && echo "Images backup: /root/qrmenu-media-$STAMP.tgz"

rm -f "/etc/nginx/sites-enabled/$NAME" "/etc/nginx/sites-available/$NAME"
nginx -t && systemctl reload nginx
systemctl disable --now "$NAME" 2>/dev/null || true
rm -f "/etc/systemd/system/$NAME.service" "/etc/cron.d/$NAME"
systemctl daemon-reload

if [ "${PURGE:-0}" = 1 ]; then
  sudo -u postgres dropdb --if-exists "$NAME"
  sudo -u postgres psql -q -c "DROP ROLE IF EXISTS $NAME;"
  REDIS_DB=$(grep '^REDIS_URL=' "$BASE/app/.env" 2>/dev/null | sed 's#.*/##' || true)
  [ -n "$REDIS_DB" ] && redis-cli -n "$REDIS_DB" flushdb >/dev/null || true   # only QR Menu's own Redis database
  certbot delete --cert-name "$DOMAIN" --non-interactive 2>/dev/null || true
  rm -rf "$BASE" /var/www/qrmenu-acme
  userdel "$NAME" 2>/dev/null || true
  echo "QR Menu removed completely (backups are in /root)."
else
  echo "QR Menu stopped and unlinked. Database, Redis data, files and certificate were kept."
fi
