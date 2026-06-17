# Floating IPs per queue manager: Pacemaker arm vs RDQM — a multi-VIP constraint

**Date:** 2026-06-17
**Context:** found during the RDQM distributed-parity build (#216); tracked as #223.
**Bottom line:** the Pacemaker/Corosync arm can attach **multiple** virtual IPs to one
HA queue manager; **RDQM allows exactly one floating IP per queue manager.** If the
architecture assumed a single HA queue manager could present several VIPs that all
fail over together, that assumption holds for the Pacemaker arm but **does not hold
for RDQM.**

This is a property of the **RDQM product**, not of our tooling — so no amount of
tooling work changes it. The rest of this note is the precise *why*, with evidence
separated into **observed data** and **judgment** so each can be checked
independently.

---

## 1. What each arm actually does

### Pacemaker arm (our `mq-pcmk-qmgr` role) — multiple VIPs, by construction

We build the HA queue manager as an ordinary Pacemaker **resource group** and put as
many virtual-IP resources in it as we want. Today the group is:

```
mq_fs (Filesystem) → mq_vip (IPaddr2) → mq_vip_ext (IPaddr2) → mq_qm (systemd:mq-<QM>)
```

— **two** `ocf:heartbeat:IPaddr2` resources (`mq_vip` on the data plane, `mq_vip_ext`
on the partner/net-ext plane) colocated and ordered with the queue manager, so both
addresses float together on every HA (node) and DR (site) move. Adding a third or
fourth VIP is one more line:

```bash
pcs resource create mq_vip_admin ocf:heartbeat:IPaddr2 ip=<addr> cidr_netmask=24 \
    --group mq_group --after mq_vip_ext
```

*(Observed data: `ansible/roles/mq-pcmk-qmgr/tasks/main.yml`, the `mq_vip` /
`mq_vip_ext` / `mq_qm` resource-create tasks.)*

### RDQM arm — exactly one floating IP

RDQM exposes a single floating-IP control, `rdqmint`, which adds or deletes **the**
floating IP for a queue manager. Attempting to add a second one is refused:

```
$ rdqmint -m QMRDQM -a -f 10.60.0.30 -l <iface>      # 10.10.1.100 already added
AMQ3877E: Floating IP address already exists for queue manager 'QMRDQM'.
AMQ3873E: Failed to add floating IP address '10.60.0.30' to queue manager 'QMRDQM'.
```

`rdqmstatus` confirms a single `HA floating IP address` field per queue manager.

*(Observed data: this build, on the live `rdqm_a` group.)*

---

## 2. The precise *why*

The two arms differ in **who owns the Pacemaker cluster**, and that is the whole
story.

- **Pacemaker arm — we own the cluster.** We stand up Corosync/Pacemaker ourselves and
  define the resources. A virtual IP is just an `ocf:heartbeat:IPaddr2` resource; a
  queue manager is just a `systemd:` resource. Putting *N* IPaddr2 resources in the
  QM's group is a configuration choice with no product-imposed ceiling. Multi-homing a
  single QM behind several floating VIPs is therefore natural and unlimited.

- **RDQM — IBM owns the cluster.** RDQM ("replicated data queue manager") is IBM's
  *packaged* HA solution: the three-node group configuration is handled by Pacemaker
  and the synchronous replication by DRBD, but both are **managed by RDQM** and driven
  only through IBM's commands — `crtmqm -sx` to create the replicated QM, `rdqmadm` to
  administer the cluster, `rdqmstatus` to inspect it, and `rdqmint` to manage its
  floating IP. You don't write the Pacemaker config at all: per IBM, you *define the
  Pacemaker cluster by editing `/var/mqm/rdqm.ini` and running `rdqmadm`* — not by
  `pcs resource create`. RDQM's model associates **one** floating IP with the queue
  manager (IBM's wording: the instances "can optionally share *a* floating IP
  address"), surfaced through `rdqmint` — and there is no RDQM command to add a second.

So the constraint is not "DRBD/Pacemaker can't do multiple VIPs" — the very same
Pacemaker *can*, and our Debian arm proves it. The constraint is that **RDQM wraps and
owns its Pacemaker cluster and only exposes a single floating IP through `rdqmint`.**
You cannot reach past the wrapper to add your own `IPaddr2` resources without leaving
the supported RDQM configuration.

**Data vs judgment.** Observed data: the `AMQ3877E` rejection of a second `rdqmint`;
the single `HA floating IP address` in `rdqmstatus`; our two-VIP Pacemaker group in
code; and IBM's documented "a floating IP address" (singular) plus the
Pacemaker-handles-grouping / DRBD-handles-replication architecture. Judgment (well
supported, but verify against the live docs if this drives a decision): that one FIP
per QM is a **hard product limit** of RDQM rather than a soft default — the singular
documentation wording and the empirical refusal together make this the only
defensible reading, but I did not find an IBM sentence that says verbatim "only one."

---

## 3. Why this matters for our architecture

If the design assumed a single HA queue manager could carry **separate floating VIPs
per network/role** (e.g. an internal data-plane VIP, an external partner-plane VIP,
and perhaps an admin/REST VIP) that all move together on failover, then:

- On the **Pacemaker arm** it works as assumed.
- On the **RDQM arm** it does not. One QM gets one floating address. Every other
  consumer that needs a stable, failover-surviving endpoint must get it some other
  way.

That is a material difference for any topology that multi-homes a queue manager across
isolated networks — which is exactly our distributed (inter-business) shape, where the
app sits on the data plane and the partner QM sits on the net-ext plane.

---

## 4. Options under the one-FIP limit (and what we did)

1. **Single FIP on one plane + client-side multi-endpoint elsewhere (what we proved).**
   Spend the one FIP on the data-plane VIP (the app rides HA exactly as on the
   Pacemaker arm), and let the partner reach the QM via an MQ **CONNAME list of the
   per-node net-ext IPs** — the sender channel reconnects to whichever node currently
   runs the QM, so the partner link also survives failover, without a second FIP.
   Functional and proven end-to-end (`e2e` 5/5). Trade-off: the non-FIP plane relies on
   client/sender reconnection logic rather than a single stable address.
2. **Put the one FIP on the plane that most needs a fixed address** (e.g. the
   cross-organisation partner link) and give the *internal* app the node-list instead.
   Same mechanism, opposite assignment.
3. **Separate queue managers** if two planes each genuinely require an independent
   floating address with full HA — at the cost of more QMs to run and route between.
4. **Network-layer indirection** (a load balancer / DNAT in front of the FIP) to fan
   one floating address out to multiple advertised addresses — adds infrastructure
   outside MQ and is likely overkill for the lab.

We took option 1 for the lab. Which option is right for production depends on which
planes truly need a fixed, failover-surviving address versus which can use
client/sender reconnection.

---

## 5. Recommendation

- Record this as an explicit **arm difference** in the parity matrix: *Pacemaker arm —
  multiple VIPs per QM; RDQM arm — one floating IP per QM.* It is a capability gap to
  state plainly, not a tooling defect to close.
- Decide the per-plane connectivity model for RDQM deliberately (the options above)
  rather than carrying the multi-VIP assumption forward.
- If a decision hinges on the verbatim limit, confirm directly against the IBM
  `rdqmint` reference and the RDQM HA page (linked below) — these are summarised here,
  not quoted in full (the live pages blocked automated fetch).

## Sources

IBM links are pinned to **9.4** (the lab runs MQ 9.4.5); each was confirmed to resolve
to the 9.4.x page.

- RDQM high availability (architecture; Pacemaker for grouping, DRBD for replication;
  the instances "can optionally share a floating IP address") —
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=configurations-rdqm-high-availability>
- Creating and deleting a floating IP address — `rdqmint` (add/delete *the* floating IP
  of an RDQM) —
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=availability-creating-deleting-floating-ip-address>
- Defining the Pacemaker cluster (HA group) — the cluster is defined by editing
  `/var/mqm/rdqm.ini` and running `rdqmadm`, **not** by direct `pcs` configuration —
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=availability-defining-pacemaker-cluster-ha-group>
- `rdqmadm` (administer the RDQM Pacemaker cluster) —
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-rdqmadm-administer-replicated-data-queue-manager-cluster>
- IBM MQ RDQM network interface best practices (three node IPs for replication +
  Pacemaker/Corosync health checks; version-agnostic support page) —
  <https://www.ibm.com/support/pages/ibm-mq-rdqm-network-interface-best-practices/stub>
- Our Pacemaker arm's two-VIP resource group: `ansible/roles/mq-pcmk-qmgr/tasks/main.yml`.
- Empirical `AMQ3877E`/`AMQ3873E` and single-FIP `rdqmstatus`: the #216 verb spike,
  `docs/reports/2026-06-16-rdqm-verb-spike.md`.
