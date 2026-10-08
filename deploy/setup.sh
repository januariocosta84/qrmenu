#!/usr/bin/env bash
# QR Menu setup for a SHARED Ubuntu/Debian server. It never touches existing apps:
#
#   * installs only MISSING packages (apt --no-upgrade: nothing already installed is upgraded/restarted)
#   * adds its own nginx site file, its own systemd service, user, database, cron file
#   * never removes, edits or restarts anything that isn't QR Menu (nginx is only *reloaded*, after `nginx -t`)
#   * picks a free local port and an empty Redis database number
#   * stops with a clear message if anything with the same name already exists
#
# 1) See what it would do (changes NOTHING):
#      sudo MODE=check DOMAIN=qrmenu.timorstore.com bash deploy/setup.sh
# 2) Install / update:
#      sudo DOMAIN=qrmenu.timorstore.com EMAIL=you@example.com bash deploy/setup.sh
set -euo pipefail

DOMAIN="${DOMAIN:-qrmenu.timorstore.com}"
MODE="${MODE:-install}"
EMAIL="${EMAIL:-}"
BASE=/srv/qrmenu
APP="$BASE/app"
MEDIA="$BASE/media"
MARKER="$BASE/.qrmenu-installed"
NAME=qrmenu                                  # user, database, service, nginx site, cron file
ACME_ROOT=/var/www/qrmenu-acme
SITE=/etc/nginx/sites-available/$NAME
APACHE_SITE=/etc/apache2/sites-available/$NAME.conf
ALLOW_APACHE_MODULES="${ALLOW_APACHE_MODULES:-0}"
UNIT=/etc/systemd/system/$NAME.service
CRON=/etc/cron.d/$NAME
OURS_TAG="QR Menu"                           # text present in every file this script writes

red() { printf '\033[31m%s\033[0m\n' "$*"; }
grn() { printf '\033[32m%s\033[0m\n' "$*"; }
ylw() { printf '\033[33m%s\033[0m\n' "$*"; }
CONFLICTS=()
conflict() { CONFLICTS+=("$*"); red "  ✗ $*"; }
ok() { grn "  ✓ $*"; }
note() { ylw "  ! $*"; }

[ "$(id -u)" = 0 ] || { red "Run as root (sudo)."; exit 1; }
[ -f "$APP/manage.py" ] || { red "Clone the repo to $APP first:  git clone https://github.com/januariocosta84/qrmenu.git $APP"; exit 1; }
INSTALLED=0; [ -f "$MARKER" ] && INSTALLED=1
is_ours_file() { [ -f "$1" ] && grep -q "$OURS_TAG" "$1"; }

echo "== Pre-flight checks for $DOMAIN (nothing is changed in this phase)"

# --- OS and Python
OS_ID="$(. /etc/os-release && echo "$ID")"          # read in a subshell: os-release defines NAME=...
OS_PRETTY="$(. /etc/os-release && echo "$PRETTY_NAME")"
case "$OS_ID" in ubuntu|debian) ok "OS: $OS_PRETTY" ;; *) conflict "Unsupported OS: $OS_PRETTY (Ubuntu/Debian only)";; esac
PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo 0)"
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then ok "Python $PYV"
else conflict "Python $PYV is too old (3.10+ needed). Ask me for the safe side-by-side install option."; fi

# --- Our own names must be free (or already ours)
if [ $INSTALLED = 1 ]; then ok "Existing QR Menu install found (update mode)"; fi
if id "$NAME" >/dev/null 2>&1 && [ $INSTALLED = 0 ]; then conflict "A system user '$NAME' already exists"; else ok "System user '$NAME' available"; fi
OTHER=$(find "$BASE" -mindepth 1 -maxdepth 1 ! -name app ! -name media ! -name .qrmenu-installed ! -name .venv 2>/dev/null | head -3)
[ -n "$OTHER" ] && [ $INSTALLED = 0 ] && conflict "$BASE contains other files: $OTHER" || ok "Folder $BASE is ours"
for f in "$UNIT" "$CRON" "$SITE" "/etc/nginx/sites-enabled/$NAME"; do
  if [ -e "$f" ] && ! is_ours_file "$(readlink -f "$f")"; then conflict "$f already exists and isn't QR Menu's"; fi
done
systemctl list-unit-files "$NAME.service" 2>/dev/null | grep -q "^$NAME.service" && ! is_ours_file "$UNIT" \
  && conflict "A systemd service named '$NAME' already exists"
ok "Service / cron / nginx site names checked"

# --- nginx: who owns ports 80/443, and is our domain already served by another site?
LISTEN80="$(ss -ltnpH 'sport = :80' 2>/dev/null | grep -o 'users:(("[^"]*' | head -1 | cut -d'"' -f2 || true)"
LISTEN443="$(ss -ltnpH 'sport = :443' 2>/dev/null | grep -o 'users:(("[^"]*' | head -1 | cut -d'"' -f2 || true)"
WEB=""
for pair in "80:$LISTEN80" "443:$LISTEN443"; do
  port="${pair%%:*}"; prog="${pair#*:}"
  case "$prog" in
    "") ok "Port $port free" ;;
    nginx|apache2)
      if [ -n "$WEB" ] && [ "$WEB" != "$prog" ]; then conflict "Ports 80/443 are split between $WEB and $prog"
      else WEB=$prog; ok "Port $port served by $prog (we add one site next to the existing ones)"; fi ;;
    *) conflict "Port $port is used by '$prog'. Only nginx or Apache are supported, without touching it." ;;
  esac
done
[ -z "$WEB" ] && { command -v apache2 >/dev/null && WEB=apache2 || WEB=nginx; }
ok "Web server: $WEB"

if [ "$WEB" = apache2 ]; then
  [ -e "$APACHE_SITE" ] && ! is_ours_file "$APACHE_SITE" && conflict "$APACHE_SITE already exists and isn't QR Menu's"
  HITS=$(grep -rlsiE "^\s*Server(Name|Alias)\s.*\b${DOMAIN//./\\.}\b" /etc/apache2 2>/dev/null | grep -v "/$NAME.conf\$" || true)
  [ -n "$HITS" ] && conflict "$DOMAIN is already configured in: $HITS" || ok "$DOMAIN not used by another Apache site"
  AVER="$(apache2 -v 2>/dev/null | sed -n 's#.*Apache/\([0-9.]*\).*#\1#p')"
  if printf '%s\n2.4.46\n' "$AVER" | sort -V -C 2>/dev/null; then conflict "Apache $AVER is too old for WebSockets via upgrade=websocket (2.4.47+)"
  else ok "Apache $AVER"; fi
  MODS="$(apache2ctl -M 2>/dev/null || true)"
  MISSING_MODS=()
  for m in proxy proxy_http ssl; do echo "$MODS" | grep -q " ${m}_module" || MISSING_MODS+=("$m"); done
  if [ ${#MISSING_MODS[@]} -gt 0 ]; then
    # Show every existing config that would start behaving differently once these modules are on.
    PATTERN="$(printf '%s|' "${MISSING_MODS[@]}" | sed 's/|$//')"
    AFFECTED=$(grep -rlsiE "IfModule +!?(mod_)?($PATTERN)(_module|\.c)|^\s*(ProxyPass|ProxyPassMatch|ProxyRequests|SSLEngine)\b" \
      /etc/apache2/sites-enabled /etc/apache2/conf-enabled /var/www --include='*.conf' --include='.htaccess' 2>/dev/null | head -20 || true)
    if [ -n "$AFFECTED" ]; then note "These existing files mention ${MISSING_MODS[*]} and could behave differently:"; echo "$AFFECTED" | sed 's/^/      /'
    else ok "No existing site or .htaccess refers to ${MISSING_MODS[*]}: enabling them won't change your other sites"; fi
  fi
  if [ ${#MISSING_MODS[@]} = 0 ]; then ok "Apache modules proxy, proxy_http, ssl already enabled (no global change)"
  elif [ "$ALLOW_APACHE_MODULES" = 1 ]; then note "Will enable Apache modules: ${MISSING_MODS[*]} (you allowed it; existing sites keep working)"
  else conflict "Apache modules not enabled: ${MISSING_MODS[*]}. Enabling them is a server-wide change. Re-run with ALLOW_APACHE_MODULES=1 to allow it"; fi
fi
if [ "$WEB" = nginx ] && [ -d /etc/nginx ]; then
  HITS=$(grep -rlsE "server_name[^;]*\b${DOMAIN//./\\.}\b" /etc/nginx 2>/dev/null | grep -v "/$NAME\$" || true)
  [ -n "$HITS" ] && conflict "$DOMAIN is already configured in: $HITS" || ok "$DOMAIN not used by another nginx site"
  grep -rqsE '\$qrmenu_connection_upgrade|upstream\s+qrmenu_upstream' /etc/nginx --exclude="$NAME" \
    && conflict "nginx already defines qrmenu_upstream / \$qrmenu_connection_upgrade elsewhere" || true
fi

# --- PostgreSQL: our database/role names
if command -v psql >/dev/null 2>&1 && systemctl is-active -q postgresql 2>/dev/null; then
  PGPORT="$(sudo -u postgres psql -tAc 'show port' 2>/dev/null || echo 5432)"
  HAS_DB=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$NAME'" 2>/dev/null || true)
  HAS_ROLE=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$NAME'" 2>/dev/null || true)
  if [ $INSTALLED = 0 ] && { [ "$HAS_DB" = 1 ] || [ "$HAS_ROLE" = 1 ]; }; then
    conflict "PostgreSQL already has a database or role named '$NAME'"
  else ok "PostgreSQL running (port $PGPORT); '$NAME' database free"; fi
else PGPORT=5432; note "PostgreSQL not installed/running: it will be installed (new service, nothing else affected)"; fi

# --- Redis: need an EMPTY database number so keys never mix with other apps
REDIS_DB=""
if [ -f "$APP/.env" ] && grep -q '^REDIS_URL=' "$APP/.env"; then REDIS_DB="$(grep '^REDIS_URL=' "$APP/.env" | sed 's#.*/##')"; ok "Redis database $REDIS_DB (from existing .env)"
elif command -v redis-cli >/dev/null 2>&1 && redis-cli ping >/dev/null 2>&1; then
  if redis-cli ping 2>&1 | grep -q NOAUTH; then conflict "Redis requires a password; add it to REDIS_URL manually"; fi
  for n in 7 8 9 10 11 12 13 14 15; do
    [ "$(redis-cli -n "$n" dbsize 2>/dev/null)" = 0 ] && { REDIS_DB=$n; break; }
  done
  [ -n "$REDIS_DB" ] && ok "Redis: empty database $REDIS_DB will be used" || conflict "No empty Redis database (7-15) found"
else REDIS_DB=7; note "Redis not installed: it will be installed"; fi

# --- A free local port for the app
APP_PORT=""
if [ -f "$APP/.env" ] && grep -q '^APP_PORT=' "$APP/.env"; then APP_PORT="$(grep '^APP_PORT=' "$APP/.env" | cut -d= -f2)"; ok "App port $APP_PORT (from existing .env)"
else
  for p in $(seq 8170 8199); do ss -ltnH "sport = :$p" | grep -q . || { APP_PORT=$p; break; }; done
  [ -n "$APP_PORT" ] && ok "Free local port $APP_PORT will be used" || conflict "No free port in 8170-8199"
fi

# --- DNS
PUBLIC_IP="$(curl -s -4 --max-time 5 ifconfig.me || hostname -I | awk '{print $1}' || true)"
DNS_IP="$( (getent ahostsv4 "$DOMAIN" || true) | awk 'NR==1{print $1}')"
if [ "$DNS_IP" = "$PUBLIC_IP" ]; then ok "DNS: $DOMAIN → $DNS_IP (this server)"
elif [ -z "$DNS_IP" ]; then note "DNS: $DOMAIN doesn't resolve yet. Add an A record → $PUBLIC_IP (needed for HTTPS)"
else note "DNS: $DOMAIN → $DNS_IP, but this server is $PUBLIC_IP"; fi

# --- Firewall (only reported, never changed)
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw status | grep -qE '(80|443|Nginx)' && ok "Firewall allows web traffic" || note "ufw is active and may block 80/443 (not changed)"
fi

# --- Packages that would be installed (missing only, never upgraded)
WANT=(python3-venv python3-dev build-essential libpq-dev postgresql redis-server certbot git curl gettext)
[ "$WEB" = nginx ] && WANT+=(nginx)
MISSING=(); for p in "${WANT[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p"); done
[ ${#MISSING[@]} = 0 ] && ok "All required packages present" || note "Would install (new only): ${MISSING[*]}"
command -v certbot >/dev/null && [[ " ${MISSING[*]} " == *" certbot "* ]] && MISSING=("${MISSING[@]/certbot}")

echo
echo "== It will ADD only:"
echo "   user '$NAME' · folder $BASE · PostgreSQL db/role '$NAME' · Redis db ${REDIS_DB:-?}"
if [ "$WEB" = apache2 ]; then WEBSITE="$APACHE_SITE (a2ensite)"; else WEBSITE="$SITE (+ link)"; fi
echo "   $UNIT (port ${APP_PORT:-?}) · $WEBSITE · $CRON · $ACME_ROOT · HTTPS certificate for $DOMAIN"
echo "== It will NOT modify, restart or remove any other site, service, database or config."
echo

if [ ${#CONFLICTS[@]} -gt 0 ]; then
  red "Stopped: ${#CONFLICTS[@]} conflict(s) above. Nothing was changed."
  exit 2
fi
if [ "$MODE" = check ]; then grn "Check passed. Nothing was changed. Run again without MODE=check to install."; exit 0; fi
[ -n "$EMAIL" ] || { red "Set EMAIL=you@example.com (used for HTTPS certificate notices)."; exit 1; }
[ "$DNS_IP" = "$PUBLIC_IP" ] || { red "Fix DNS first ($DOMAIN must point to $PUBLIC_IP), then run again."; exit 1; }

# ============================================================== install
echo "== Installing"
if [ ${#MISSING[@]} -gt 0 ]; then
  apt-get update -q
  DEBIAN_FRONTEND=noninteractive apt-get install -yq --no-upgrade ${MISSING[*]}
fi

id "$NAME" >/dev/null 2>&1 || useradd --system --home "$BASE" --shell /usr/sbin/nologin "$NAME"
mkdir -p "$MEDIA" "$ACME_ROOT"
echo "QR Menu install for $DOMAIN, $(date -u +%FT%TZ)" > "$MARKER"
chown -R "$NAME":www-data "$BASE"
chmod 750 "$BASE"; chmod 755 "$ACME_ROOT"

if [ ! -f "$APP/.env" ]; then
  DB_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -q -c "CREATE ROLE $NAME LOGIN PASSWORD '$DB_PASS';"
  sudo -u postgres createdb -O "$NAME" "$NAME"
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
  ADMIN_PATH="manage-$(python3 -c 'import secrets; print(secrets.token_hex(4))')/"
  cat > "$APP/.env" <<ENV
# QR Menu settings (written by deploy/setup.sh)
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=$SECRET
DJANGO_ALLOWED_HOSTS=$DOMAIN
DJANGO_CSRF_TRUSTED_ORIGINS=https://$DOMAIN
PUBLIC_BASE_URL=https://$DOMAIN
DATABASE_URL=postgres://$NAME:$DB_PASS@127.0.0.1:$PGPORT/$NAME
REDIS_URL=redis://127.0.0.1:6379/$REDIS_DB
APP_PORT=$APP_PORT
NUM_PROXIES=1
MEDIA_ROOT=$MEDIA
TIME_ZONE=Asia/Dili
PLATFORM_NAME=QR Menu
SIGNUP_MODE=approval
ADMIN_URL=$ADMIN_PATH
CASH_DRAWER_NETWORK_ENABLED=false
ASSUME_HTTPS=$([ "$WEB" = apache2 ] && echo 1 || echo 0)
# SMTP (needed for sign-up confirmation and password reset):
# EMAIL_HOST=smtp.example.com
# EMAIL_PORT=587
# EMAIL_HOST_USER=no-reply@qrmenu.timorstore.com
# EMAIL_HOST_PASSWORD=change-me
# EMAIL_USE_TLS=true
# DEFAULT_FROM_EMAIL=QR Menu <no-reply@qrmenu.timorstore.com>
# SUPPORT_EMAIL=support@qrmenu.timorstore.com
# PLATFORM_ADMINS=Your Name <you@example.com>
ENV
  chown "$NAME":www-data "$APP/.env"; chmod 640 "$APP/.env"
fi

cd "$APP"
[ -d .venv ] || sudo -u "$NAME" python3 -m venv .venv
sudo -u "$NAME" .venv/bin/pip install -q --upgrade pip
sudo -u "$NAME" .venv/bin/pip install -q -r requirements.txt
sudo -u "$NAME" .venv/bin/python manage.py migrate --noinput
sudo -u "$NAME" .venv/bin/python manage.py compilemessages --ignore=.venv -v 0  # dashboard languages (locale/)
sudo -u "$NAME" .venv/bin/python manage.py collectstatic --noinput -v 0
sudo -u "$NAME" .venv/bin/python manage.py check --deploy --fail-level ERROR

render() { sed -e "s#__DOMAIN__#$DOMAIN#g" -e "s#__PORT__#$APP_PORT#g" -e "s#__APP__#$APP#g" -e "s#__MEDIA__#$MEDIA#g" "$1"; }
render deploy/qrmenu.service > "$UNIT"
systemctl daemon-reload
systemctl enable -q "$NAME"
systemctl restart "$NAME"          # restarts ONLY qrmenu
for i in $(seq 1 20); do curl -s -o /dev/null "http://127.0.0.1:$APP_PORT/" && break; sleep 1; done
curl -s -o /dev/null "http://127.0.0.1:$APP_PORT/" || { red "App didn't start: journalctl -u $NAME -n 50"; exit 1; }
ok "App running on 127.0.0.1:$APP_PORT"

echo "0 6 * * * $NAME cd $APP && .venv/bin/python manage.py generate_invoices >> $BASE/invoices.log 2>&1  # $OURS_TAG" > "$CRON"

if [ "$WEB" = apache2 ]; then
  # Apache: add OUR site only; configtest before every graceful reload; disable our site if anything fails.
  safe_reload() {
    if apache2ctl configtest 2>/tmp/qrmenu-apache-test; then systemctl reload apache2
    else red "Apache config test failed; disabling QR Menu's site so other sites stay untouched:"; cat /tmp/qrmenu-apache-test
         a2dissite -q "$NAME" || true; apache2ctl configtest && systemctl reload apache2; exit 1; fi
  }
  if [ ${#MISSING_MODS[@]} -gt 0 ]; then a2enmod -q "${MISSING_MODS[@]}"; fi
  if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    cat > "$APACHE_SITE" <<APACHE
# $OURS_TAG site (temporary, until the HTTPS certificate exists)
<VirtualHost *:80>
    ServerName $DOMAIN
    Alias /.well-known/acme-challenge/ $ACME_ROOT/.well-known/acme-challenge/
    <Directory $ACME_ROOT>
        Require all granted
    </Directory>
</VirtualHost>
APACHE
    a2ensite -q "$NAME"
    safe_reload
    certbot certonly --webroot -w "$ACME_ROOT" -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" \
      --deploy-hook "systemctl reload apache2"
  fi
  render deploy/apache.conf > "$APACHE_SITE"
  a2ensite -q "$NAME"
  safe_reload
else
  # nginx: add OUR site only; test the whole config before every reload; undo our file if the test fails.
  safe_reload() {
    if nginx -t 2>/tmp/qrmenu-nginx-test; then systemctl reload nginx
    else red "nginx config test failed; removing QR Menu's site so other sites stay untouched:"; cat /tmp/qrmenu-nginx-test
         rm -f "/etc/nginx/sites-enabled/$NAME"; nginx -t && systemctl reload nginx; exit 1; fi
  }
  if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    cat > "$SITE" <<NGINX
# $OURS_TAG site (temporary, until the HTTPS certificate exists)
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root $ACME_ROOT; }
    location / { return 503; }
}
NGINX
    ln -sf "$SITE" "/etc/nginx/sites-enabled/$NAME"
    systemctl is-active -q nginx || systemctl start nginx
    safe_reload
    certbot certonly --webroot -w "$ACME_ROOT" -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" \
      --deploy-hook "systemctl reload nginx"
  fi
  render deploy/nginx.conf > "$SITE"
  ln -sf "$SITE" "/etc/nginx/sites-enabled/$NAME"
  safe_reload
fi

code=$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/" || true)
ADMIN_PATH="$(grep '^ADMIN_URL=' .env | cut -d= -f2)"
echo
grn "Done: https://$DOMAIN  (HTTP $code)"
cat <<DONE

Next steps:
  1. Create your platform owner account:
       cd $APP && sudo -u $NAME .venv/bin/python manage.py createsuperuser
  2. Log in at https://$DOMAIN/accounts/login/ → https://$DOMAIN/platform/
     (Django admin: https://$DOMAIN/$ADMIN_PATH)
  3. Add SMTP settings to $APP/.env, then:  systemctl restart $NAME
  4. Platform console → Billing settings: add your payment instructions.

Update later:   cd $APP && sudo -u $NAME git pull && sudo DOMAIN=$DOMAIN EMAIL=$EMAIL bash deploy/setup.sh
Remove it all:  sudo bash deploy/uninstall.sh   (removes only QR Menu)
DONE
