#!/bin/bash
# Optional after checking Pi free memory. Music Assistant handles accounts and players.
set -euo pipefail
mkdir -p /opt/music-assistant
docker run -d --name music-assistant --restart unless-stopped --network host \
  --memory 600m -v /opt/music-assistant:/data \
  ghcr.io/music-assistant/server:latest
echo 'Open http://andrew-pi.local:8095 to connect Spotify and your speakers.'
