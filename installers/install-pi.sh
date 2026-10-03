#!/bin/bash
# Run from the extracted PRIVATE pairing package on 64-bit Raspberry Pi OS.
set -euo pipefail
cd "$(dirname "$0")"
test -f andrew/relay-config.json || { echo 'Generate the private pairing package on the host PC first.'; exit 1; }
test "$(uname -m)" = aarch64 || { echo 'Use 64-bit Raspberry Pi OS.'; exit 1; }
id andrew >/dev/null 2>&1 || sudo useradd -m -s /bin/bash -G audio,video,render,input andrew
sudo install -d -o andrew -g andrew /opt/andrew
sudo cp -r andrew/. /opt/andrew/
sudo install -d /opt/andrew/assets
sudo cp andrew/upgrade.js andrew/upgrade.css /opt/andrew/assets/
sudo cp andrew/andrew-relay.service andrew/andrew-screen.service /etc/systemd/system/
sudo chown -R andrew:andrew /opt/andrew
sudo install -d -m 700 -o andrew -g andrew /home/andrew/.ssh
sudo install -m 600 -o andrew -g andrew andrew/paired-host.pub /home/andrew/.ssh/authorized_keys
printf 'andrew ALL=(root) NOPASSWD: /usr/bin/systemctl restart andrew-relay andrew-screen andrew-display\n' | sudo tee /etc/sudoers.d/andrew-updates >/dev/null
sudo chmod 440 /etc/sudoers.d/andrew-updates
sudo visudo -cf /etc/sudoers.d/andrew-updates
sudo bash /opt/andrew/install.sh
sudo bash /opt/andrew/install-screen.sh
echo 'Pi installed. Keep the host PC awake and Andrew running. No SD card was erased.'
