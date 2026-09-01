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


def test_selfheal_is_part_of_reproducible_root_cron():
    cron = _text("systemd/crontab.root.prod.txt")
    installer = _text("systemd/install_prod.sh")
    assert "pia_selfheal.sh" in cron
    assert 'crontab -u root "$SYSTEMD_DIR/crontab.root.prod.txt"' in installer
