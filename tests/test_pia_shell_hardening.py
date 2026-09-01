from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_all_runtime_piactl_calls_are_bounded():
    for name, timeout_var in (
        ("pia_start.sh", "CLI_TIMEOUT"),
        ("flota_start.sh", "PIA_CLI_TIMEOUT"),
    ):
        text = _text(name)
        assert f'timeout "${timeout_var}" piactl "$@"' in text
        runtime = "\n".join(
            line for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        assert runtime.count("piactl") == 1, name


def test_alert_transports_reject_http_errors():
    assert "--fail-with-body" in _text("deadman_switch.sh")
    assert "--fail-with-body" in _text("pia_selfheal.sh")
    assert "--fail-with-body" in _text("healthcheck.sh")


def test_installer_is_verified_with_pinned_sha256():
    text = _text("pia_selfheal.sh")
    assert "INSTALLER_SHA256=" in text
    assert 'sha256sum "$tmp/pia.run"' in text
    assert 'actual_sha256" != "$INSTALLER_SHA256' in text


def test_spool_is_drained_one_confirmed_alert_at_a_time():
    text = _text("pia_selfheal.sh")
    assert 'IFS= read -r line < "$file"' in text
    assert 'ntfy_push "PIA: alerta intarziata' in text
    assert 'tail -n +2 "$file"' in text


def test_healthcheck_probes_the_required_vpn_path():
    text = _text("healthcheck.sh")
    assert 'timeout "$PIA_CLI_TIMEOUT" piactl "$@"' in text
    assert "ip link show dev tun0" in text
    assert "resolvectl query -i tun0 api.binance.com" in text
    assert "--interface tun0" in text
    assert "https://api.binance.com/api/v3/time" in text
    assert 'VPN($vpn)' in text


def test_selfheal_is_part_of_reproducible_root_cron():
    cron = _text("systemd/crontab.root.prod.txt")
    installer = _text("systemd/install_prod.sh")
    assert "pia_selfheal.sh" in cron
    assert 'crontab -u root "$SYSTEMD_DIR/crontab.root.prod.txt"' in installer
