#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

install_timer_override() {
    local timer_name="$1"
    local source_path="${repo_root}/deploy/systemd/${timer_name}.d/caerus-schedule.conf"
    local target_dir="/etc/systemd/system/${timer_name}.d"

    test -f "${source_path}"
    sudo install -d -o root -g root -m 0755 "${target_dir}"
    sudo install -o root -g root -m 0644 \
        "${source_path}" "${target_dir}/caerus-schedule.conf"
}

install_timer_override apt-daily.timer
install_timer_override apt-daily-upgrade.timer

sudo systemctl daemon-reload
sudo systemctl enable --now apt-daily.timer apt-daily-upgrade.timer
sudo systemctl restart apt-daily.timer apt-daily-upgrade.timer

systemctl list-timers apt-daily.timer apt-daily-upgrade.timer --no-pager
