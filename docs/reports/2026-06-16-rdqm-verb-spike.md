# RDQM verb spike — live-group findings (#216, Plan B Task 1)

**Date:** 2026-06-16
**Setup:** `distributed-rdqm-rhel` (QMRDQM on the `rdqm_a` 3-node HA group:
rdqm-a1/a2/a3, RHEL 9.6 x86_64 under TCG emulation)
**Goal:** confirm the RDQM arm's `qm-up` / `qm-down` / `qm-status` registry verbs
against a live, formed HA group (the verbs were provisional pending this spike),
and surface any RDQM/Pacemaker behaviours the lab tooling must account for.

## Summary

All three provisional verbs are **confirmed correct** — no registry change was
needed. The spike also surfaced one genuine architectural constraint that *did*
require a design change: **RDQM allows exactly one floating IP per queue manager**,
which the distributed (inter-business) topology must work around. That change is
implemented and the distributed message path now round-trips end-to-end.

## Verbs — confirmed on the live group

The RDQM arm dispatches these as `cmd` verbs, run on the first cluster node
(`rdqm_a[0]` = rdqm-a1) via `ansible … -b -m shell`.

### `qm-status` → `rdqmstatus -m {qm}`  ✓

```
$ rdqmstatus -m QMRDQM        # on rdqm-a1
Node:                    rdqm-a1
Queue manager status:    Running
HA role:                 Primary
HA status:               Normal
HA current location:     This node
HA preferred location:   This node
HA floating IP interface: enp7s0
HA floating IP address:  10.10.1.100
Node: rdqm-a2  /  HA status: Normal
Node: rdqm-a3  /  HA status: Normal
```

`rdqmstatus` is the right status verb: it reports the whole HA group (role, status,
current/preferred location, and the floating IP) from any node. `dspmq -m QMRDQM -s`
per node corroborates the instance model: `Running` on the primary, `Running
elsewhere` on the two secondaries.

### `qm-down` → `endmqm -w {qm}` (as `mqm`)  ✓

```
$ su mqm -c '/opt/mqm/bin/endmqm -w QMRDQM'    # on the primary, rdqm-a1
Replicated data queue manager disabled.
Waiting for queue manager 'QMRDQM' to end.
IBM MQ queue manager 'QMRDQM' ended.

$ dspmq -m QMRDQM -o status   # all three nodes:
QMNAME(QMRDQM)  STATUS(Ended normally)      # a1, a2, AND a3
```

**Key behaviour:** a plain `endmqm` on a HA RDQM is a *controlled, cluster-wide*
stop. RDQM prints **"Replicated data queue manager disabled"** and the QM ends on
**all** nodes — it does **not** fail over / restart elsewhere. This is exactly the
semantics we want from `qm-down`, and it mirrors the Pacemaker arm's `pcs resource
disable mq_group` (controlled down that the cluster respects). The prerequisite is
that `mqm` is in the `haclient` group — the `rdqm-install` role does this, and
without it `endmqm`/`strmqm` are refused with **AMQ7077E**.

### `qm-up` → `strmqm {qm}` (as `mqm`)  ✓

```
$ su mqm -c '/opt/mqm/bin/strmqm QMRDQM'       # on rdqm-a1
IBM MQ queue manager 'QMRDQM' starting.
...
Replicated data queue manager enabled.
IBM MQ queue manager 'QMRDQM' started using V9.4.5.0.
```

A plain `strmqm` after a controlled `endmqm` **re-enables HA** ("Replicated data
queue manager enabled") and restarts the QM. Post-start `rdqmstatus` shows the
group fully restored: rdqm-a1 Primary/Running, HA Normal, floating IP 10.10.1.100
back on `enp7s0`; a2/a3 `Running elsewhere`.

**Net:** the RDQM `qm-up`/`qm-down`/`qm-status` verbs already in
`lab/topology.yaml` are correct as written. The only refinement is documentation
(the topology comment now records the confirmed behaviour instead of "provisional").

### Caveat (noted, not yet addressed)

The `cmd` verbs run on `rdqm_a[0]` (rdqm-a1), the QM's preferred location. If the QM
had failed over to a2/a3, `endmqm`/`strmqm` issued on a1 would act on the node where
the QM is *not* currently running. For the lab's normal case (preferred location =
a1) this is fine; a future refinement could target "the node where the QM runs"
(derivable from `dspmq`/`rdqmstatus`). `qm-status` is unaffected — `rdqmstatus`
reports the whole group from any node.

## Finding: RDQM allows ONE floating IP per queue manager

`mqlab qm create distributed-rdqm-rhel` originally attempted a **second** `rdqmint`
(a partner-facing VIP on net-ext, mirroring the Pacemaker arm which binds both a
data VIP and an ext VIP to its QM resource group). RDQM rejected it:

```
AMQ3877E: Floating IP address already exists for queue manager 'QMRDQM'.
AMQ3873E: Failed to add floating IP address '10.60.0.30' to queue manager 'QMRDQM'.
```

`rdqmstatus` confirms a single `HA floating IP address`. This is a real divergence
between the arms: the Pacemaker arm can attach multiple VIPs to one QM; the RDQM arm
cannot.

### Why it matters for the distributed topology

The distributed setup has **two** consumers on **two isolated L2 networks**:

- the **app** (Business A) on `net-data-a` (10.10.1.0/24), and
- the **partner** QM (QMDTCC) on `net-ext` (10.60.0.0/24, the inter-business WAN).

With one floating IP, only one consumer can ride an HA failover via a floating
address. The other needs a different failover-tolerant mechanism.

### Resolution (implemented)

Spend the single FIP on the **data VIP** (`10.10.1.100`, net-data-a) so the **app**
rides HA exactly as in the Pacemaker arm (same headline "client follows the QM
across nodes" story). Give the **partner** a failover-tolerant reach-back **without
a second FIP**: QMDTCC's SDR channel to QMRDQM carries a **CONNAME list of the three
`rdqm_a` net-ext node IPs**:

```
CONNAME('10.60.0.31(1414),10.60.0.32(1414),10.60.0.33(1414)')
```

MQ tries the list in order and reconnects to whichever node currently runs QMRDQM,
so the inter-business link survives an HA failover too. Our outbound SDR
(QMRDQM→QMDTCC) is unaffected: it originates from the active node and targets
QMDTCC's static net-ext IP (10.60.0.50).

Code changes (all on `feature/216-plan-b-rdqm-distributed`):

- `lab/scripts/rdqm-qm-create.sh` — dropped the second `rdqmint`; now a 3-arg
  contract `(QM, data-VIP, DTCC_CONN)`; documents the single-FIP rationale.
- `ansible/site-rdqm-distributed.yml` — `our_conn` is the 3-node net-ext CONNAME
  list (was the single, unbindable partner VIP 10.60.0.30).
- `lab/topology.yaml` — `distributed-rdqm-rhel` QM drops `vip_ext`; `vip_ext` is now
  optional in `QmConfig` (the Pacemaker arm still uses it).
- `src/mqlab/cli.py` / `src/mqlab/setups.py` — `_qm_script` passes the 3-arg
  contract; `QmConfig.vip_ext` defaults to `""`.

### Acceptance

`lab/scripts/e2e-test.sh 3 QMRDQM '10.10.1.100(1414)'` — the full path round-trips:

```
[0] round-trip 448ms <- REPLY:req-0000      # first call: channel startup
[1] round-trip 39ms  <- REPLY:req-0001
[2] round-trip 41ms  <- REPLY:req-0002
3/3 round-trips OK
```

app → QMRDQM (data VIP) → SDR → QMDTCC → responder → reply → SDR (node-list CONNAME)
→ QMRDQM → app.

## For the user's review

The single-FIP workaround is a deliberate design choice worth a sanity check:

1. **FIP priority.** We gave the FIP to the app (data plane). The alternative —
   FIP on net-ext for the partner, app uses a node-list CONNAME — is equally valid;
   it depends on which side you'd rather keep "VIP-simple". The chosen split matches
   the Pacemaker arm's app-HA story most closely.
2. **Parity note.** This is a true behavioural divergence from the Pacemaker arm
   (multi-VIP per QM). It's a property of RDQM, not of our tooling, and is worth
   capturing in the parity matrix / design as an arm difference rather than a gap.
3. **TCG scope.** Functional only — no timing claims (round-trip ms above are under
   emulation and not representative).
