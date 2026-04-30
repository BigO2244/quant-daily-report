#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="/Users/brettolson/Documents/Caerus/quant-daily-report-main"
LABEL="com.brett.caerus.auto_shadow_health_recovery"
TEMPLATE="${REPO_ROOT}/scripts/launchd/${LABEL}.plist"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
INSTALLED_PLIST="${LAUNCH_AGENTS_DIR}/${LABEL}.plist"

usage() {
    cat <<EOF
Usage:
  bash scripts/install_auto_shadow_health_recovery_launchd.sh [install|uninstall|status]

Commands:
  install     Install and bootstrap the LaunchAgent. Default.
  uninstall   Boot out and remove the LaunchAgent.
  status      Print launchctl status for ${LABEL}.

Manual test:
  launchctl kickstart -k gui/\$(id -u)/${LABEL}
EOF
}

command_name="${1:-install}"

mkdir -p "${REPO_ROOT}/logs"

case "${command_name}" in
    install)
        mkdir -p "${LAUNCH_AGENTS_DIR}"
        cp "${TEMPLATE}" "${INSTALLED_PLIST}"
        chmod 644 "${INSTALLED_PLIST}"
        chmod +x "${REPO_ROOT}/scripts/auto_shadow_health_recovery.sh"
        chmod +x "${REPO_ROOT}/scripts/hydrate_shadow_locally_and_sync.sh"

        if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
            launchctl bootout "gui/$(id -u)" "${INSTALLED_PLIST}" >/dev/null 2>&1 || true
        fi
        launchctl bootstrap "gui/$(id -u)" "${INSTALLED_PLIST}"
        launchctl enable "gui/$(id -u)/${LABEL}"
        echo "Installed ${LABEL}"
        echo "Schedule: weekdays at 08:00 America/New_York"
        echo "Logs:"
        echo "  ${REPO_ROOT}/logs/auto_shadow_health_recovery.out"
        echo "  ${REPO_ROOT}/logs/auto_shadow_health_recovery.err"
        echo "  ${REPO_ROOT}/logs/auto_shadow_health_recovery.log"
        echo "Manual test:"
        echo "  launchctl kickstart -k gui/$(id -u)/${LABEL}"
        ;;
    uninstall)
        launchctl bootout "gui/$(id -u)" "${INSTALLED_PLIST}" >/dev/null 2>&1 || true
        rm -f "${INSTALLED_PLIST}"
        echo "Uninstalled ${LABEL}"
        ;;
    status)
        launchctl print "gui/$(id -u)/${LABEL}"
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        echo "Unknown command: ${command_name}" >&2
        usage >&2
        exit 2
        ;;
esac
