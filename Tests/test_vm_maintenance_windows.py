from pathlib import Path


def test_apt_metadata_timer_runs_in_quiet_window():
    override = Path(
        "deploy/systemd/apt-daily.timer.d/caerus-schedule.conf"
    ).read_text(encoding="utf-8")

    assert "OnCalendar=\n" in override
    assert "OnCalendar=*-*-* 01:15:00 America/New_York" in override
    assert "RandomizedDelaySec=15m" in override
    assert "Persistent=true" in override


def test_apt_upgrade_timer_runs_in_quiet_window():
    override = Path(
        "deploy/systemd/apt-daily-upgrade.timer.d/caerus-schedule.conf"
    ).read_text(encoding="utf-8")

    assert "OnCalendar=\n" in override
    assert "OnCalendar=*-*-* 02:15:00 America/New_York" in override
    assert "RandomizedDelaySec=15m" in override
    assert "Persistent=true" in override


def test_maintenance_installer_only_reloads_and_restarts_timers():
    script = Path("scripts/install_vm_maintenance_windows.sh").read_text(
        encoding="utf-8"
    )

    assert "systemctl daemon-reload" in script
    assert "systemctl enable --now apt-daily.timer apt-daily-upgrade.timer" in script
    assert "systemctl restart apt-daily.timer apt-daily-upgrade.timer" in script
    assert "apt-daily.service" not in script
    assert "apt-daily-upgrade.service" not in script
