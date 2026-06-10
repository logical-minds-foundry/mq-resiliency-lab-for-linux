#!/usr/bin/env bash
# obs-open.sh — print the Grafana URL and a ready tunnel command (#103).
set -euo pipefail
URL="http://10.50.0.2:3000"
echo "Grafana: ${URL}"
echo "Tunnel from your workstation: ssh -L 3000:10.50.0.2:3000 <vergil-vm-session-host>"
