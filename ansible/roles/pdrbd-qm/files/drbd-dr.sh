#!/bin/bash
# ocf:pdrbd:drbd-dr — the agent of the pcmk-drbd-ubuntu DR clone (drbd_dr_qm, #1660).
# Installed by the pdrbd-qm role as /usr/lib/ocf/resource.d/pdrbd/drbd-dr.
#
# It IS ocf:linbit:drbd: every action runs the LINBIT agent with the same arguments and
# environment, and returns its exit code. It adds ONE rule:
#
#   A DR member that is not already Primary may be promoted only while its DR connection
#   is Connected to a peer in the Secondary role and its own disk is UpToDate, or when
#   the forced DR (site-pdrbd-dr-switch.yml dr_force=true) has granted this node a
#   one-shot promotion: the transient node attribute pdrbd-dr-force-promote-<res> = 1.
#
# Why (#1660, #1430 drill 6). After a forced DR at site B, the lost site A came back and
# promoted its DR resource at boot, before its first DRBD handshake with B: two live
# queue managers and a DR split brain. Site A's Pacemaker still had target-role=Promoted
# (the forced path cannot reach a lost site), so only the promotion score stood between
# a returning site and a second Primary.
#
# Data — ocf:linbit:drbd, drbd-utils 9.35.0 (/usr/lib/ocf/resource.d/linbit/drbd and
# drbd.shellfuncs.sh, read on pdrbd-a1 2026-10-19):
#   - drbd_update_master_score: for an UpToDate disk that is not Primary, a peer-disk
#     DUnknown gives `local_ok` (3rd adjust_master_score value); any KNOWN peer-disk
#     state ("all peer disk states known") gives `as_good_as_it_gets` (4th value). The
#     connection state is not consulted: the status parser leaves `_cstate` commented out.
#   - adjust_master_score must be non-decreasing ("BAD adjust_master_score value $j > $i
#     ; falling back to default"), so "0 10 0 10000" is rejected and silently replaced
#     by the default "0 10 1000 10000".
# Data — DRBD 9.3.4 kernel module source (/usr/src/drbd-9.3.4-1ppa1~resolute1, on the
# box): drbd_nl.c, on creating a peer device, and drbd_state.c restore_outdated_in_pdsk(),
# on losing a connection, set the peer's disk state to D_OUTDATED when the metadata flag
# MDF_PEER_OUTDATED is set. A graceful `drbdadm disconnect` (drill 6's break) outdates the
# peer and records that flag, so a returning old primary reports `peer-disk:Outdated`
# while Connecting: a KNOWN state.
# Judgment — so the issue's candidate (adjust_master_score "0 0 0 10000" for the DR clone)
# would not have stopped drill 6: the returning a1 saw b1 as Outdated, which is the 4th
# value, 10000. No adjust_master_score value separates "connected" from "disconnected
# with a peer remembered as Outdated". DRBD itself would also have allowed the promote:
# the peer is Outdated as far as a1 knows, and drbdsetup(8) documents that "If all
# missing nodes are marked as outdated, a partition always has quorum".
#   The connection state is the one fact a returning site cannot get wrong: it is never
# Connected to a peer that diverged (DRBD detects the split brain at the handshake and,
# with no after-sb-* policy, drops to StandAlone: drbd.conf-9.0(5) after-sb-1pri
# `disconnect`, the default), and while Connected DRBD lets only one side be Primary
# (b1's "Multiple primaries not allowed by config" at drill 6's handshake). This agent
# keys on it.
#
# How. Before running the LINBIT agent for an action that sets the promotion score
# (monitor, notify, reload), it reads `drbdsetup status --verbose <res>`. When the rule
# does not allow a promotion, it runs the agent with adjust_master_score="0 0 0 0" (valid:
# non-decreasing), so the agent itself removes the score ("set_master_score: [[ $1 -le 0
# ]] && remove_master_score") and Pacemaker has nothing to promote. A Primary always runs
# with the configured values, so the DR primary keeps its score through a DR link outage.
# `start` runs with "0 0 0 0" (nothing is known before the resource is up), then waits up
# to PDRBD_DR_CONNECT_WAIT_SECS for the handshake and, if the rule allows, runs the agent's
# monitor once with the configured values, so a healthy start does not wait for the first
# 20 s monitor. `promote` re-checks the rule and refuses (OCF_ERR_GENERIC, with an exit
# reason) without calling the agent: the backstop for a link that drops between the last
# monitor and the promote. A successful promote consumes the forced grant.
#
# Consequences (judgment, also in docs/reference/rdqm-vs-vanilla-deviations.md CL-5/DR-4):
#   - Cold boot with the DR peer reachable and Secondary: promotes, as before.
#   - A first bootstrap promotes site A only once site B's DR member is up and connected.
#   - HA failover at the DR primary site while the DR link is DOWN: the new HA primary
#     does not promote the DR resource (its peer is not connected). Recover with the
#     forced DR toward the same site, or restore the link.
#   - The returning old primary stays Secondary; site-pdrbd-dr-rejoin.yml makes it the
#     DR secondary of record and, for a split brain, resyncs it from the live site.

: "${OCF_ROOT:=/usr/lib/ocf}"
LINBIT_AGENT="$OCF_ROOT/resource.d/linbit/drbd"
OCF_SUCCESS=0
OCF_ERR_GENERIC=1
OCF_ERR_CONFIGURED=6
ZERO_SCORES="0 0 0 0"
CONNECT_WAIT_SECS=${PDRBD_DR_CONNECT_WAIT_SECS:-30}

action=${1-}
res=${OCF_RESKEY_drbd_resource-}
grant_attr="pdrbd-dr-force-promote-$res"

log() {
	# $1: syslog level, $2: message. The journal is where the cluster logs go.
	logger -t "pdrbd-drbd-dr[$res]" -p "daemon.$1" -- "$2"
}

# The DR member's state, parsed from `drbdsetup status --verbose <res>` (the format pinned
# by tests/test_pdrbd_dr_switch.py on live captures): the resource line (column 0), the
# local volume line (2 spaces), the connection line (2 spaces, `connection:` then `role:`).
# A resource that is not up leaves every field empty.
read_state() {
	local st
	st=$(drbdsetup status --verbose "$res" 2>/dev/null) || st=""
	role=$(sed -En '1s/^[^ ]+ node-id:[0-9]+ role:([A-Za-z]+).*/\1/p' <<<"$st")
	disk=$(sed -En 's/^  volume:[0-9]+ minor:[0-9]+ disk:([A-Za-z]+).*/\1/p' <<<"$st")
	conns=$(grep -Ec '^  [^ ]+ node-id:[0-9]+ connection:' <<<"$st")
	conn=$(sed -En 's/^  [^ ]+ node-id:[0-9]+ connection:([A-Za-z]+).*/\1/p' <<<"$st")
	peer_role=$(sed -En 's/^  [^ ]+ node-id:[0-9]+ connection:[A-Za-z]+ role:([A-Za-z]+).*/\1/p' <<<"$st")
}

state_text() {
	echo "role ${role:-unknown}, disk ${disk:-unknown}, connection ${conn:-none}, peer role ${peer_role:-unknown}"
}

connected_to_secondary() {
	[ "$disk" = UpToDate ] && [ "$conns" = 1 ] && [ "$conn" = Connected ] && [ "$peer_role" = Secondary ]
}

# The forced DR's one-shot grant on THIS node. crm_attribute exits 105 when the attribute
# is not set; any other failure is logged and read as "no grant" (never as a grant).
granted() {
	local node value rc
	if ! node=$(crm_node -n); then
		log err "crm_node -n failed; reading the forced-promote grant as absent"
		return 1
	fi
	value=$(crm_attribute --node "$node" --lifetime reboot --name "$grant_attr" --query --quiet 2>/dev/null)
	rc=$?
	case $rc in
	0) [ "$value" = 1 ] ;;
	105) return 1 ;;
	*)
		log err "crm_attribute --query $grant_attr exited $rc; reading the forced-promote grant as absent"
		return 1
		;;
	esac
}

may_promote() {
	[ "$role" = Primary ] || connected_to_secondary || granted
}

consume_grant() {
	local node
	if node=$(crm_node -n) && crm_attribute --node "$node" --lifetime reboot --name "$grant_attr" --delete; then
		log notice "promoted under the forced-DR grant; grant $grant_attr consumed"
	else
		log err "promoted under the forced-DR grant, but deleting $grant_attr failed: delete it by hand"
	fi
}

if [ -z "$res" ] && [ "$action" != meta-data ] && [ "$action" != usage ]; then
	echo "ocf-exit-reason:drbd_resource is not set" >&2
	exit $OCF_ERR_CONFIGURED
fi

case $action in
monitor | notify | reload)
	read_state
	if ! may_promote; then
		export OCF_RESKEY_adjust_master_score="$ZERO_SCORES"
	fi
	"$LINBIT_AGENT" "$action"
	exit $?
	;;
start)
	configured_set=${OCF_RESKEY_adjust_master_score+set}
	configured=${OCF_RESKEY_adjust_master_score-}
	OCF_RESKEY_adjust_master_score="$ZERO_SCORES" "$LINBIT_AGENT" start
	rc=$?
	[ $rc -eq $OCF_SUCCESS ] || exit $rc
	if [ "$configured_set" = set ]; then
		export OCF_RESKEY_adjust_master_score="$configured"
	else
		unset OCF_RESKEY_adjust_master_score
	fi
	deadline=$((SECONDS + CONNECT_WAIT_SECS))
	while :; do
		read_state
		if may_promote; then
			"$LINBIT_AGENT" monitor || log err "post-start monitor exited $?; the next scheduled monitor sets the score"
			break
		fi
		if [ "$conn" = StandAlone ] || [ "$peer_role" = Primary ]; then
			log warning "started without a promotion score: $(state_text) — this site stays DR secondary (#1660)"
			break
		fi
		if [ $SECONDS -ge $deadline ]; then
			log notice "started without a promotion score after ${CONNECT_WAIT_SECS}s: $(state_text); monitors re-check"
			break
		fi
		sleep 2
	done
	exit $OCF_SUCCESS
	;;
promote)
	read_state
	if [ "$role" = Primary ] || connected_to_secondary; then
		"$LINBIT_AGENT" promote
		exit $?
	fi
	if granted; then
		"$LINBIT_AGENT" promote
		rc=$?
		[ $rc -eq $OCF_SUCCESS ] && consume_grant
		exit $rc
	fi
	msg="refusing to promote $res: $(state_text). The DR peer must be Connected as Secondary, or the forced DR must grant it (#1660)"
	log crit "$msg"
	OCF_RESKEY_adjust_master_score="$ZERO_SCORES" "$LINBIT_AGENT" monitor || log err "removing the promotion score failed (monitor exited $?)"
	echo "ocf-exit-reason:$msg" >&2
	exit $OCF_ERR_GENERIC
	;;
meta-data)
	"$LINBIT_AGENT" meta-data | sed 's/<resource-agent name="drbd"/<resource-agent name="drbd-dr"/'
	exit "${PIPESTATUS[0]}"
	;;
*)
	# stop, demote, validate-all, status, usage: the LINBIT agent unchanged.
	"$LINBIT_AGENT" "$action"
	exit $?
	;;
esac
