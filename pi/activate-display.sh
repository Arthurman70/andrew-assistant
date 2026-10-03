#!/bin/bash
set -euo pipefail
test "$(hostname)" = andrew-pi
test -x /run/andrew/display/root/usr/bin/chromium
install -d -o andrew -g andrew -m 700 /run/andrew/xdg /run/andrew/browser-home /run/andrew/display/browser-profile
if [ -d /run/andrew/browser-profile ] && [ ! -L /run/andrew/browser-profile ]; then
  cp -a /run/andrew/browser-profile/. /run/andrew/display/browser-profile/
  test "$(realpath /run/andrew/browser-profile)" = /run/andrew/browser-profile
  rm -rf -- /run/andrew/browser-profile
fi
test -e /run/andrew/browser-profile || ln -s /run/andrew/display/browser-profile /run/andrew/browser-profile
cat >/run/systemd/system/andrew-screen-web.service <<'EOF'
[Unit]
Description=Andrew local screen
[Service]
User=andrew
Environment=ANDREW_BASE_DIR=/opt/andrew
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 /run/andrew/screen.py
Restart=on-failure
RestartSec=3
EOF
cat >/run/systemd/system/andrew-display.service <<'EOF'
[Unit]
Description=Andrew Pi avatar and browser
After=andrew-screen-web.service
Conflicts=getty@tty1.service
[Service]
User=andrew
Group=andrew
SupplementaryGroups=audio video render input
PAMName=login
TTYPath=/dev/tty1
StandardInput=tty
TTYReset=yes
TTYVHangup=yes
RootDirectory=/run/andrew/display/root
Environment=HOME=/run/andrew/browser-home
Environment=XDG_RUNTIME_DIR=/run/andrew/xdg
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=LIBSEAT_BACKEND=logind
ExecStart=/usr/bin/python3 /run/andrew/display.py
Restart=on-failure
RestartSec=3
EOF
systemctl daemon-reload
systemctl restart andrew-screen-web
systemctl restart andrew-display
systemctl is-active andrew-display
