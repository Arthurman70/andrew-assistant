#!/bin/bash
# Keep this recovery runtime entirely off the read-only SD filesystem.
set -euo pipefail
test "$(hostname)" = andrew-pi
base=/run/andrew/display
mkdir -p "$base"
if ! mountpoint -q "$base"; then mount -t tmpfs -o size=1200M,mode=755 tmpfs "$base"; fi
mkdir -p "$base/lower" "$base/upper" "$base/work" "$base/root"
mountpoint -q "$base/lower" || mount --bind / "$base/lower"
if [ "${1:-}" = --restore ] && ! mountpoint -q "$base/root"; then
  tar -xzf - -C "$base/upper"
fi
mountpoint -q "$base/root" || mount -t overlay overlay -o "lowerdir=$base/lower,upperdir=$base/upper,workdir=$base/work" "$base/root"
for item in dev proc sys run tmp; do
  mkdir -p "$base/root/$item"
  mountpoint -q "$base/root/$item" || mount --rbind "/$item" "$base/root/$item"
  mount --make-rslave "$base/root/$item"
done
if [ ! -f "$base/root/usr/bin/cage" ]; then
  printf '#!/bin/sh\nexit 101\n' > "$base/root/usr/sbin/policy-rc.d"
  chmod 755 "$base/root/usr/sbin/policy-rc.d"
  chroot "$base/root" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends cage chromium chromium-sandbox mpv ffmpeg
  chroot "$base/root" apt-get clean
fi
if [ ! -x "$base/root/usr/bin/ffmpeg" ]; then
  chroot "$base/root" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg
fi
echo 'Display runtime prepared in RAM.'
