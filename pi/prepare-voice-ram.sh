#!/bin/bash
set -euo pipefail
test "$(hostname)" = andrew-pi
install -d -o andrew -g andrew /run/andrew
# Give native dependencies their own executable, bounded RAM mount.
if [ ! -f /run/andrew/python/.independent-runtime ]; then
  systemctl stop andrew-relay || true
  mountpoint -q /run/andrew/python && umount /run/andrew/python || true
  if [ -d /run/andrew/python ]; then
    test "$(realpath /run/andrew/python)" = /run/andrew/python
    rm -rf -- /run/andrew/python
  fi
  mkdir -p /run/andrew/python
  mount -t tmpfs -o size=160M,mode=755,nosuid,nodev tmpfs /run/andrew/python
  chown andrew:andrew /run/andrew/python
  touch /run/andrew/python/.independent-runtime
fi
