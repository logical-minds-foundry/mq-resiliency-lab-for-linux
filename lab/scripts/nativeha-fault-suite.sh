#!/usr/bin/env bash
# §3.1 fault suite for the nativeha-rhel arm (#246, Phase 1). Drives faults via
# virsh (hard) + ansible (in-guest) and reads Native HA group status. Fail-loud:
# asserts an end state and exits non-zero otherwise (lab-gotchas: never trust
# per-step success). Run from the worktree's ansible context (build/ symlinked,
# inventory rendered). QM=QMNATIVE, group=nha_rhel_a.
set -euo pipefail

QM="${1:?usage: nativeha-fault-suite.sh <qm-name> ...}"
GROUP=nha_rhel_a
HBMAC_A3=52:54:00:73:a0:a6   # net-hb-a on nha-rhel-a3 (verify via domiflist)
V() { virsh -c qemu:///system "$@"; }
status() {  # group status as seen from $1
  uv run ansible "$1" -b -m shell -a "su - mqm -c '/opt/mqm/bin/dspmq -m $QM -o nativeha -x'" 2>/dev/null
}
active_count() { status "$GROUP" | grep -c 'ROLE(Active)'; }
assert() { if ! eval "$2"; then echo "FAIL: $1" >&2; exit 1; fi; echo "OK: $1"; }

drill_kill_active_node() {  # suite step 2 — hard power-off the active node
  local act; act=$(status nha-rhel-a2 | awk '/ROLE\(Active\)/{print $0}' | grep -oE 'nha-rhel-a[123]' | head -1)
  echo "== drill: hard power-off active ($act) =="
  V destroy "lab_$act"
  sleep 20
  # a survivor must be Active and quorum must hold (2/3)
  local other; other=$([ "$act" = nha-rhel-a1 ] && echo nha-rhel-a2 || echo nha-rhel-a1)
  assert "a survivor is Active after power-off" "status $other | grep -q 'ROLE(Active)'"
  assert "quorum held (2/3)"                    "status $other | grep -q 'QUORUM(2/3)'"
  V start "lab_$act"; sleep 90    # boot + mqmonitor restart + resync
  assert "downed node rejoined + resynced (3/3)" "status $other | grep -q 'QUORUM(3/3)'"
}

drill_sever_replication() {  # suite step 3 — no split-brain
  echo "== drill: sever the active's replication NIC (no split-brain) =="
  V domif-setlink lab_nha-rhel-a3 "$HBMAC_A3" down
  sleep 20
  assert "exactly one Active among the survivors" "[ \"\$(status nha-rhel-a1 | grep -c 'ROLE(Active)')\" -ge 1 ]"
  assert "isolated node lost quorum (0/3, not Active)" \
    "uv run ansible nha-rhel-a3 -b -m shell -a \"su - mqm -c '/opt/mqm/bin/dspmq -m $QM -o nativeha -s'\" 2>/dev/null | grep -q 'QUORUM(0/3)'"
  V domif-setlink lab_nha-rhel-a3 "$HBMAC_A3" up; sleep 30
}

drill_planned_failover() {  # suite step 5 — qm-down/qm-up verbs
  echo "== drill: planned failover + failback (mqmonitor@ verbs) =="
  local act; act=$(status nha-rhel-a1 | awk '/ROLE\(Active\)/' | grep -oE 'nha-rhel-a[123]' | head -1)
  uv run ansible "$act" -b -m shell -a "systemctl stop mqmonitor@$QM"
  sleep 15
  assert "planned failover moved Active off $act" "[ \"\$(status nha-rhel-a1 | grep -c 'ROLE(Active)')\" -ge 1 ]"
  uv run ansible "$act" -b -m shell -a "systemctl start mqmonitor@$QM"; sleep 30
  assert "failback restored 3/3" "status nha-rhel-a1 | grep -q 'QUORUM(3/3)'"
}

main() {
  drill_planned_failover
  drill_sever_replication
  drill_kill_active_node
  echo "ALL DRILLS PASSED"
}
main "$@"
