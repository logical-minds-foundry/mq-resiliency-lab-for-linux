#!/usr/bin/env bash
# obs-open.sh — print the Grafana URL and the workstation tunnel recipe (#103).
# obs is a libvirt guest inside the Vergil VM, so reaching it from the host means
# forwarding a local port through the Vergil VM. Lima turns the instance's dots
# into hyphens for the ssh.config 'Host' alias.
set -euo pipefail
URL="http://10.50.0.2:3000"
echo "Grafana:   ${URL}  (directly reachable inside the Vergil VM)"
echo "Dashboard: ${URL}/d/lab-fleet-node  (Fleet — Node Health)"
echo
echo "From your workstation:"
echo "  1. limactl list   # find the instance whose DIR is this repo"
echo "  2. ssh -F ~/.lima/<instance>/ssh.config -L 3000:10.50.0.2:3000 <host-alias>"
echo "  3. browse http://localhost:3000/d/lab-fleet-node   (admin / admin)"
