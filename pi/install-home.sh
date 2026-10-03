#!/bin/bash
# Optional: pair Home Assistant after the voice satellite works.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io
systemctl enable --now docker
mkdir -p /opt/homeassistant
if ! docker container inspect homeassistant >/dev/null 2>&1; then
  docker run -d --name homeassistant --restart unless-stopped --network host \
    --memory 1100m -e TZ=America/New_York \
    -v /opt/homeassistant:/config -v /etc/localtime:/etc/localtime:ro \
    ghcr.io/home-assistant/home-assistant:stable
fi
