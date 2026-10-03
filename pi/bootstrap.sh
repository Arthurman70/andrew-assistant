#!/bin/bash
# Run independently of cloud-init so package downloads cannot delay remote access.
set -uo pipefail
mkdir -p /var/log/andrew
exec > >(tee -a /var/log/andrew/bootstrap.log) 2>&1
echo "Andrew setup started: $(date -Is)"
failed=0
if ! bash /opt/andrew/install.sh; then
  echo "Relay/Home Assistant installation failed. See this log before retrying."
  failed=1
fi
# Attempt display installation even when Home Assistant's download failed.
if ! bash /opt/andrew/install-screen.sh; then
  echo "Display installation failed. See this log before retrying."
  failed=1
fi
if [ "$failed" -eq 0 ]; then
  date -Is > /opt/andrew/bootstrap-complete
fi
echo "Andrew setup finished: $(date -Is); exit=$failed"
exit "$failed"
