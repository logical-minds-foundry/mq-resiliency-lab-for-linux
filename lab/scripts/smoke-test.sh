#!/usr/bin/env bash
# lab/scripts/smoke-test.sh — every node must reach its OWN site's nets and
# the WAN; it must NOT reach the other site's private nets. Exits non-zero
# on any violation. Settle ~2s after any link-state change before running.
#
# Check design (provider-spike findings):
#  - POSITIVE checks may ping the host-side gateway IP of a net the node is
#    attached to (valid on isolated nets).
#  - NEGATIVE (isolation) checks MUST target the other site's NODE IPs.
#    Host gateway IPs are answered via the management default route (weak
#    host model) regardless of bridge isolation, so they prove nothing.
set -euo pipefail
cd "$(dirname "$0")/.."
fail=0
check() { # node target expect(0|1)
  if vagrant ssh "$1" -c "ping -c1 -W2 $2" >/dev/null 2>&1; then got=0; else got=1; fi
  if [ "$got" -ne "$3" ]; then echo "FAIL: $1 -> $2 (expect $3, got $got)"; fail=1
  else echo "ok:   $1 -> $2"; fi
}
for i in 1 2 3; do
  check "node-a$i" 10.10.1.1     0   # own data net gateway
  check "node-a$i" 172.16.1.1    0   # own heartbeat net gateway
  check "node-a$i" 10.99.0.2$i   0   # peer site node over WAN
  check "node-a$i" 172.16.2.2$i  1   # peer NODE on other site's hb net: isolated
  check "node-b$i" 10.10.2.1     0
  check "node-b$i" 172.16.2.1    0
  check "node-b$i" 10.99.0.1$i   0
  check "node-b$i" 172.16.1.1$i  1
done
exit "$fail"
