#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-brettolson@34.61.147.38}"
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
  "${repo_root}/web/dashboard/dashboard-data.json" \
  "${REMOTE_HOST}:${REMOTE_WEB}/"

echo "[deploy] syncing dashboard refresh scripts"
ssh "${REMOTE_HOST}" "mkdir -p '${REMOTE_REPO}/scripts/research'"
scp "${repo_root}/scripts/refresh_quant_dashboard.py" "${REMOTE_HOST}:${REMOTE_REPO}/scripts/refresh_quant_dashboard.py"
scp "${repo_root}/scripts/export_alpaca_broker_snapshot.py" "${REMOTE_HOST}:${REMOTE_REPO}/scripts/export_alpaca_broker_snapshot.py"
scp "${repo_root}/scripts/research/build_quant_dashboard.py" "${REMOTE_HOST}:${REMOTE_REPO}/scripts/research/build_quant_dashboard.py"

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

ssh "${REMOTE_HOST}" "sudo mkdir -p /var/www/caerus-dashboard && sudo cp -f '${REMOTE_WEB}/index.html' /var/www/caerus-dashboard/index.html && sudo cp -f '${REMOTE_WEB}/quant_daily_executive.html' /var/www/caerus-dashboard/quant_daily_executive.html && sudo cp -f '${REMOTE_WEB}/quant_daily_executive.css' /var/www/caerus-dashboard/quant_daily_executive.css && sudo cp -f '${REMOTE_WEB}/quant_daily_executive.js' /var/www/caerus-dashboard/quant_daily_executive.js && sudo cp -f '${REMOTE_WEB}/dashboard_data.json' /var/www/caerus-dashboard/dashboard_data.json && sudo cp -f '${REMOTE_WEB}/dashboard-data.json' /var/www/caerus-dashboard/dashboard-data.json && sudo cp -f /tmp/caerus-dashboard.nginx /etc/nginx/sites-available/caerus-dashboard && sudo ln -sf /etc/nginx/sites-available/caerus-dashboard /etc/nginx/sites-enabled/caerus-dashboard && sudo rm -f /etc/nginx/sites-enabled/default && sudo cp -f /tmp/caerus-dashboard-refresh.service /etc/systemd/system/caerus-dashboard-refresh.service && sudo cp -f /tmp/caerus-dashboard-refresh.timer /etc/systemd/system/caerus-dashboard-refresh.timer && sudo systemctl daemon-reload && sudo systemctl enable --now caerus-dashboard-refresh.timer && sudo systemctl restart nginx"
ssh "${REMOTE_HOST}" "sudo mkdir -p /var/www/caerus-dashboard && sudo chown -R brettolson:brettolson /var/www/caerus-dashboard && sudo chmod 755 /var/www /var/www/caerus-dashboard && sudo cp -f '${REMOTE_WEB}/index.html' /var/www/caerus-dashboard/index.html && sudo cp -f '${REMOTE_WEB}/quant_daily_executive.html' /var/www/caerus-dashboard/quant_daily_executive.html && sudo cp -f '${REMOTE_WEB}/quant_daily_executive.css' /var/www/caerus-dashboard/quant_daily_executive.css && sudo cp -f '${REMOTE_WEB}/quant_daily_executive.js' /var/www/caerus-dashboard/quant_daily_executive.js && sudo cp -f '${REMOTE_WEB}/dashboard_data.json' /var/www/caerus-dashboard/dashboard_data.json && sudo cp -f '${REMOTE_WEB}/dashboard-data.json' /var/www/caerus-dashboard/dashboard-data.json && sudo chown -R brettolson:brettolson /var/www/caerus-dashboard && sudo chmod -R a+rX /var/www/caerus-dashboard && sudo cp -f /tmp/caerus-dashboard.nginx /etc/nginx/sites-available/caerus-dashboard && sudo ln -sf /etc/nginx/sites-available/caerus-dashboard /etc/nginx/sites-enabled/caerus-dashboard && sudo rm -f /etc/nginx/sites-enabled/default && sudo cp -f /tmp/caerus-dashboard-refresh.service /etc/systemd/system/caerus-dashboard-refresh.service && sudo cp -f /tmp/caerus-dashboard-refresh.timer /etc/systemd/system/caerus-dashboard-refresh.timer && sudo systemctl daemon-reload && sudo systemctl enable --now caerus-dashboard-refresh.timer && sudo systemctl restart nginx"

echo "[deploy] dashboard published"
