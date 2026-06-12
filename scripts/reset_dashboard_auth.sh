#!/usr/bin/env bash
set -euo pipefail

# Reset the nginx basic-auth credentials that protect /dashboard/ and
# /dashboardDEV/ on the scheduler VM.
#
# This script is intentionally interactive so credentials are not passed on the
# command line, written to the repo, or printed in logs.

AUTH_FILE="${AUTH_FILE:-/etc/nginx/.htpasswd_dashboard}"

if ! command -v htpasswd >/dev/null 2>&1; then
    echo "ERROR: htpasswd is required but not installed." >&2
    exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "ERROR: curl is required but not installed." >&2
    exit 1
fi

read -r -p "Dashboard username: " DASHBOARD_USER
if [[ -z "${DASHBOARD_USER}" ]]; then
    echo "ERROR: username is required." >&2
    exit 2
fi

read -r -s -p "New dashboard password: " DASHBOARD_PASSWORD
echo
read -r -s -p "Confirm password: " DASHBOARD_PASSWORD_CONFIRM
echo

if [[ -z "${DASHBOARD_PASSWORD}" ]]; then
    echo "ERROR: password is required." >&2
    exit 3
fi

if [[ "${DASHBOARD_PASSWORD}" != "${DASHBOARD_PASSWORD_CONFIRM}" ]]; then
    echo "ERROR: passwords do not match." >&2
    exit 4
fi

if [[ ! -f "${AUTH_FILE}" ]]; then
    printf '%s\n' "${DASHBOARD_PASSWORD}" | sudo htpasswd -c -iB "${AUTH_FILE}" "${DASHBOARD_USER}" >/dev/null
else
    printf '%s\n' "${DASHBOARD_PASSWORD}" | sudo htpasswd -iB "${AUTH_FILE}" "${DASHBOARD_USER}" >/dev/null
fi

sudo chown root:www-data "${AUTH_FILE}"
sudo chmod 0640 "${AUTH_FILE}"

sudo nginx -t
sudo systemctl reload nginx

tmp_netrc="$(mktemp)"
trap 'rm -f "${tmp_netrc}"' EXIT
chmod 600 "${tmp_netrc}"
printf 'machine 127.0.0.1 login %s password %s\n' "${DASHBOARD_USER}" "${DASHBOARD_PASSWORD}" > "${tmp_netrc}"

auth_probe="$(curl -I -s -o /dev/null -w '%{http_code}' --netrc-file "${tmp_netrc}" http://127.0.0.1/dashboard/)"
if [[ "${auth_probe}" != "200" ]]; then
    echo "ERROR: dashboard auth validation failed (HTTP ${auth_probe})." >&2
    exit 5
fi

echo "Dashboard auth reset complete."
