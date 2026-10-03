#!/bin/bash
# Fresh, writable Raspberry Pi OS installation. The current Pi uses RAM recovery.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends cage chromium chromium-sandbox mpv
cat >/etc/systemd/system/andrew-display.service <<'EOF'
[Unit]
Description=Andrew avatar, browser and video
After=andrew-screen.service
Wants=andrew-screen.service
Conflicts=getty@tty1.service
[Service]
User=andrew
Group=andrew
SupplementaryGroups=audio video render input
RuntimeDirectory=andrew
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes
PAMName=login
TTYPath=/dev/tty1
StandardInput=tty
TTYReset=yes
TTYVHangup=yes
Environment=HOME=/run/andrew/browser-home
Environment=XDG_RUNTIME_DIR=/run/andrew/xdg
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=LIBSEAT_BACKEND=logind
ExecStart=/usr/bin/python3 /opt/andrew/display.py
Restart=on-failure
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now andrew-screen.service andrew-display.service
