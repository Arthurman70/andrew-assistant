#!/bin/bash
set -euo pipefail
test "$(hostname)" = andrew-pi
test -f /run/andrew/agent.py
# /run is noexec on Raspberry Pi OS. Permit only this bundled native-library directory.
mountpoint -q /run/andrew/python || mount --bind /run/andrew/python /run/andrew/python
mount -o remount,bind,exec /run/andrew/python
install -d /run/systemd/system/andrew-relay.service.d
cat >/run/systemd/system/andrew-relay.service.d/voice-v2.conf <<'EOF'
[Service]
Environment=ANDREW_BASE_DIR=/opt/andrew
Environment=PYTHONPATH=/run/andrew/python
Environment=PYTHONDONTWRITEBYTECODE=1
UnsetEnvironment=ANDREW_SPEAKER_DEVICE ANDREW_PI_SPEAKER
ReadWritePaths=/run/andrew
ExecStart=
ExecStart=/usr/bin/python3 /run/andrew/agent.py
EOF
amixer -c Headphones sset PCM 0dB unmute >/dev/null 2>&1 || true
systemctl daemon-reload
systemctl restart andrew-relay
systemctl is-active andrew-relay
