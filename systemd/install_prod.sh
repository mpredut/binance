#!/bin/bash
set -euo pipefail

ROOT="${TRADING_ROOT:-/home/predut/binance}"
SYSTEMD_DIR="$ROOT/systemd"

test -d "$ROOT/.git" || { echo "Repo lipsa: $ROOT"; exit 1; }
test "$(id -u)" -eq 0 || { echo "Ruleaza cu sudo."; exit 1; }

install -m 0644 "$SYSTEMD_DIR/binance.service" /etc/systemd/system/binance.service
install -m 0644 "$SYSTEMD_DIR/pia.service" /etc/systemd/system/pia.service
install -m 0644 "$SYSTEMD_DIR/piavpn.service" /etc/systemd/system/piavpn.service
install -m 0644 "$SYSTEMD_DIR/binancedemon.service" /etc/systemd/system/binancedemon.service
install -d -m 0755 /etc/systemd/resolved.conf.d
install -m 0644 "$SYSTEMD_DIR/resolved-20-trading-cache.conf" \
  /etc/systemd/resolved.conf.d/20-trading-cache.conf
install -d -m 0755 /etc/ssh/sshd_config.d
install -m 0644 "$SYSTEMD_DIR/sshd-20-trading.conf" \
  /etc/ssh/sshd_config.d/20-trading.conf

install -d -o predut -g predut -m 0755 "$ROOT/logs"
crontab -u predut "$SYSTEMD_DIR/crontab.prod.txt"

systemctl daemon-reload
sshd -t
systemctl enable piavpn.service pia.service binance.service
systemctl restart systemd-resolved
systemctl reload ssh
systemctl restart piavpn.service pia.service binance.service

systemctl --no-pager --full status binance.service pia.service piavpn.service
crontab -u predut -l
