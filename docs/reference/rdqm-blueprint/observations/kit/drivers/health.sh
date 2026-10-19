#!/usr/bin/env bash
# health gate for #1666: RDQMAPP running at site A, DR Normal on the A HA primary, e2e passes.
# Prints the site-A HA primary node name on the last line (PRIMARY=<node>) and exits 0 only if healthy.
set -uo pipefail
cd "${MQLAB_REPO:?}"
key=~/.vagrant.d/insecure_private_key
s() { timeout 30 ssh -i $key -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o LogLevel=ERROR -o ConnectTimeout=4 vagrant@"$1" "sudo /opt/mqm/bin/rdqmstatus -m RDQMAPP 2>&1"; }
prim=""
for i in 31 32 33; do
  out=$(s 10.50.0.$i) || continue
  if grep -q "HA current location: *This node" <<<"$out"; then prim="rdqm-a${i#3}"; st="$out"; fi
done
[ -n "$prim" ] || { echo "UNHEALTHY: no site-A node reports HA current location: This node"; exit 1; }
grep -E "Queue manager status|HA status|HA blocked|DR role|DR status" <<<"$st" | sed 's/^/  /'
grep -q "Queue manager status: *Running" <<<"$st" || { echo "UNHEALTHY: QM not running"; echo "PRIMARY=$prim"; exit 1; }
grep -q "DR status: *Normal" <<<"$st" || { echo "UNHEALTHY: DR status not Normal"; echo "PRIMARY=$prim"; exit 1; }
uv run mqlab qm e2e rdqm-rhel 2>&1 | tail -2
[ "${PIPESTATUS[0]}" -eq 0 ] || { echo "UNHEALTHY: e2e failed"; echo "PRIMARY=$prim"; exit 1; }
echo "HEALTHY"
echo "PRIMARY=$prim"
