#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-brettolson@alpha-stack-scheduler}"
REMOTE_REPO="${REMOTE_REPO:-/home/brettolson/quant-daily-report}"
REMOTE_WEB="${REMOTE_WEB:-${REMOTE_REPO}/web/dashboard}"
REMOTE_ENV="${REMOTE_ENV:-/home/brettolson/.caerus/alpaca.env}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[deploy] syncing dashboard assets"
scp \
  "${repo_root}/web/dashboard/index.html" \
  "${repo_root}/web/dashboard/quant_daily_executive.html" \
  "${repo_root}/web/dashboard/quant_daily_executive.css" \
  "${repo_root}/web/dashboard/quant_daily_executive.js" \
  "${repo_root}/web/dashboard/dashboard_data.json" \
  "${REMOTE_HOST}:${REMOTE_WEB}/"

ssh "${REMOTE_HOST}" "python3 - <<'PY'
from pathlib import Path
remote_web = Path('${REMOTE_WEB}')
payload = remote_web / 'dashboard_data.json'
json_alias = remote_web / 'dashboard-data.json'
js_alias = remote_web / 'dashboard-data.js'
text = payload.read_text(encoding='utf-8')
json_alias.write_text(text, encoding='utf-8')
js_alias.write_text('window.DASHBOARD_V1 = ' + text.rstrip() + ';\n', encoding='utf-8')
PY"

echo "[deploy] syncing dashboard refresh scripts"
ssh "${REMOTE_HOST}" "mkdir -p '${REMOTE_REPO}/scripts/research'"
scp "${repo_root}/scripts/refresh_quant_dashboard.py" "${REMOTE_HOST}:${REMOTE_REPO}/scripts/refresh_quant_dashboard.py"
scp "${repo_root}/scripts/build_portfolio_history.py" "${REMOTE_HOST}:${REMOTE_REPO}/scripts/build_portfolio_history.py"
scp "${repo_root}/scripts/export_alpaca_broker_snapshot.py" "${REMOTE_HOST}:${REMOTE_REPO}/scripts/export_alpaca_broker_snapshot.py"
scp "${repo_root}/scripts/research/build_quant_dashboard.py" "${REMOTE_HOST}:${REMOTE_REPO}/scripts/research/build_quant_dashboard.py"
scp "${repo_root}/scripts/research/build_dashboard_v1.py" "${REMOTE_HOST}:${REMOTE_REPO}/scripts/research/build_dashboard_v1.py"

if [[ -f "${HOME}/.caerus/alpaca.env" ]]; then
  echo "[deploy] syncing Alpaca env file to VM"
  ssh "${REMOTE_HOST}" "mkdir -p '${REMOTE_ENV%/*}'"
  scp "${HOME}/.caerus/alpaca.env" "${REMOTE_HOST}:${REMOTE_ENV}"
  ssh "${REMOTE_HOST}" "chmod 600 '${REMOTE_ENV}'"
fi

echo "[deploy] installing nginx config and refresh timer"
scp "${repo_root}/deploy/caerus-dashboard.nginx" "${REMOTE_HOST}:/tmp/caerus-dashboard.nginx"
scp "${repo_root}/deploy/caerus-dashboard-refresh.service" "${REMOTE_HOST}:/tmp/caerus-dashboard-refresh.service"
scp "${repo_root}/deploy/caerus-dashboard-refresh.timer" "${REMOTE_HOST}:/tmp/caerus-dashboard-refresh.timer"
scp "${repo_root}/deploy/root_landing.html" "${REMOTE_HOST}:/tmp/root_landing.html"

ssh "${REMOTE_HOST}" "sudo mkdir -p /var/www/caerus-dashboard /var/www/caerus-dashboard-dev && sudo chown -R brettolson:brettolson /var/www/caerus-dashboard /var/www/caerus-dashboard-dev && sudo chmod 755 /var/www /var/www/caerus-dashboard /var/www/caerus-dashboard-dev && sudo cp -f '${REMOTE_WEB}/index.html' /var/www/caerus-dashboard/index.html && sudo cp -f '${REMOTE_WEB}/quant_daily_executive.html' /var/www/caerus-dashboard/quant_daily_executive.html && sudo cp -f '${REMOTE_WEB}/quant_daily_executive.css' /var/www/caerus-dashboard/quant_daily_executive.css && sudo cp -f '${REMOTE_WEB}/quant_daily_executive.js' /var/www/caerus-dashboard/quant_daily_executive.js && sudo cp -f '${REMOTE_WEB}/dashboard_data.json' /var/www/caerus-dashboard/dashboard_data.json && sudo cp -f '${REMOTE_WEB}/dashboard-data.json' /var/www/caerus-dashboard/dashboard-data.json && sudo cp -f '${REMOTE_WEB}/dashboard-data.js' /var/www/caerus-dashboard/dashboard-data.js && sudo cp -f '${REMOTE_WEB}/index.html' /var/www/caerus-dashboard-dev/index.html && sudo cp -f '${REMOTE_WEB}/quant_daily_executive.html' /var/www/caerus-dashboard-dev/quant_daily_executive.html && sudo cp -f '${REMOTE_WEB}/quant_daily_executive.css' /var/www/caerus-dashboard-dev/quant_daily_executive.css && sudo cp -f '${REMOTE_WEB}/quant_daily_executive.js' /var/www/caerus-dashboard-dev/quant_daily_executive.js && sudo cp -f '${REMOTE_WEB}/dashboard_data.json' /var/www/caerus-dashboard-dev/dashboard_data.json && sudo cp -f '${REMOTE_WEB}/dashboard-data.json' /var/www/caerus-dashboard-dev/dashboard-data.json && sudo cp -f '${REMOTE_WEB}/dashboard-data.js' /var/www/caerus-dashboard-dev/dashboard-data.js && sudo chown -R brettolson:brettolson /var/www/caerus-dashboard /var/www/caerus-dashboard-dev && sudo chmod -R a+rX /var/www/caerus-dashboard /var/www/caerus-dashboard-dev && sudo cp -f /tmp/root_landing.html /var/www/html/index.html && sudo cp -f /tmp/caerus-dashboard.nginx /etc/nginx/sites-available/caerus-dashboard && sudo ln -sf /etc/nginx/sites-available/caerus-dashboard /etc/nginx/sites-enabled/caerus-dashboard && sudo cp -f /tmp/caerus-dashboard-refresh.service /etc/systemd/system/caerus-dashboard-refresh.service && sudo cp -f /tmp/caerus-dashboard-refresh.timer /etc/systemd/system/caerus-dashboard-refresh.timer && sudo systemctl daemon-reload && sudo systemctl enable --now caerus-dashboard-refresh.timer && sudo systemctl restart nginx"

echo "[deploy] dashboard published"
