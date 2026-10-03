#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
cd /opt/andrew
apt-get update
apt-get install -y python3-venv python3-pip alsa-utils fswebcam ffmpeg avahi-daemon curl
# The Pi 4's analog connector feeds the attached powered speakers.
amixer -c Headphones sset PCM 0dB unmute || true
alsactl store || true
python3 -m venv /opt/andrew/venv
/opt/andrew/venv/bin/pip install numpy webrtcvad-wheels==2.0.14 sherpa-onnx==1.13.8 sentencepiece==0.2.2
mkdir -p /opt/andrew/wake
curl --fail --location 'https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2' -o /tmp/andrew-wake.tar.bz2
tar -xjf /tmp/andrew-wake.tar.bz2 --strip-components=1 -C /opt/andrew/wake
chown -R andrew:andrew /opt/andrew
chmod 600 /opt/andrew/relay-config.json
systemctl enable --now avahi-daemon
systemctl daemon-reload
systemctl enable --now andrew-relay.service
# Home Assistant is optional and must not block basic satellite voice setup.
date -Is > /opt/andrew/installation-complete
