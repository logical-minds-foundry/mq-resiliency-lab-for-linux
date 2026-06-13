# Lab bootstrap runbook

Cold-start bring-up of the lab from a freshly-rebuilt Vergil VM. Copy-paste,
in order. Stop re-deriving this from the playbooks every session.

> **Source of truth.** This is a convenience layer over `mqlab` + the
> `ansible/site-*.yml` playbooks + `lab/scripts/`. If a command here ever
> disagrees with reality, the code wins — and fix this doc in the same commit.

## Prerequisites (once per VM)

The VM is ephemeral. After a `vrg-vm rebuild`, the host-mounted repo and
`build/` survive, but the Python env and libvirt/Vagrant state do not.

```bash
uv sync          # creates .venv and puts `mqlab` on PATH (so it's `mqlab …`, not `uv run mqlab …`)
```

Run every `mqlab` command from the **repo root** (the main `develop` checkout).
A from-scratch run usually means `lab/.vagrant` was removed, so every guest is
ABSENT and `vm create` will boot them fresh.

Secrets and the fence key are **auto-generated and persisted** under the
gitignored `build/secrets/` and `build/fence_key` — they survive VM rebuilds and
regenerate on a full `build/` wipe. There is no password to remember or export
(`lab/scripts/lab-secret.sh`).

---

## 1. Networks (always first)

```bash
mqlab net create all      # define + autostart + start every libvirt network
mqlab net status          # confirm: all the lab nets active
```

Networks must exist before any guest boots (the guests attach to them).

---

## 2. Observability (independent — any time after networks)

```bash
mqlab obs up                       # boot obs + mon-probe, provision Prometheus + Grafana (+ Loki/Alloy once #143 lands)
mqlab obs instrument <setup>       # install node_exporter (+ net-reach) on a running setup's guests
mqlab obs open                     # print the Grafana URL + the SSH-tunnel one-liner
```

`obs up` only stands up the monitoring pair. `obs instrument <setup>` is what
makes a given arm (e.g. `pcmk_san_ha`) start reporting — run it **after** that
setup's guests are up.

---

## 3. PCMK single-site HA — site A (`pcmk_san_ha`)

Three cluster nodes + one iSCSI SAN, one site. QMPCMK on the shared LUN as a
Pacemaker resource group (`mq_fs → mq_vip → mq_vip_ext → mq_qm`).

```bash
mqlab net create all                 # (if not already done)
mqlab vm create pcmk_san_ha          # boot san-a + pcmk-a1..3 (vagrant up — bare guests, no provisioning yet)
mqlab vm provision pcmk_san_ha       # site-pcmk.yml: iSCSI target/initiators, LUN format, Corosync/Pacemaker, STONITH, mq-install
mqlab qm create pcmk_san_ha          # create QMPCMK + its HA resource group (vip 10.10.1.200 / ext 10.60.0.10)
mqlab qm status pcmk_san_ha          # pcs status of the mq resource group
```

- `vm provision` **auto-sources** the `hacluster` secret — no env var to set.
- `vm create` then `vm provision` back-to-back is safe: the playbook waits for
  every SAN-arm host to be SSH-ready before any storage work (cold-boot guard,
  #151). If a node still drops, just re-run `vm provision` (idempotent).
- The QM is a **separate step** (`qm create`) — the provision playbook builds the
  cluster + storage but never the QM.

---

## 4. PCMK cross-site DR — sites A + B (`pcmk_san_dr`)

The full 8-node topology: san-a/b + pcmk-a1..3 + pcmk-b1..3, DRBD-async under the
SAN, DR-ready from the start. Site A runs live; site B receives on cutover.

```bash
mqlab net create all
mqlab vm create pcmk_san_dr          # boot all 8: san-a, san-b, pcmk-a1..3, pcmk-b1..3
mqlab vm provision pcmk_san_dr       # site-pcmk-dr.yml: DRBD (a→b), iSCSI, BOTH clusters (mqpcmk-a / mqpcmk-b), STONITH, mq-install
                                     #   equivalent wrapper: lab/scripts/dr-provision.sh
mqlab qm create pcmk_san_ha          # create QMPCMK on the LIVE site (A). NOTE: the qm config lives on the
                                     #   pcmk_san_ha setup; pcmk_san_dr carries no `qm:` field.
lab/scripts/pcmk-dr-seed-peer.sh     # teach site B about the QM (addmqinf + disabled unit) so a cutover can start it;
                                     #   QM DATA travels via DRBD, this seeds only the definition
```

DR exercises (after the above):

```bash
lab/scripts/pcmk-dr-cutover.sh a2b   # controlled cutover site A → B (quiesce → confirm replication → flip DRBD → bring B up)
lab/scripts/pcmk-dr-cutover.sh b2a   # failback B → A (mirror)
```

Both partner VIPs (`mq_vip` + `mq_vip_ext`) float together on HA failover and
move together on DR cutover. DRBD/cutover internals and recovery:
`docs/reference/drbd-operations.md` and `docs/reference/lab-gotchas.md`.

---

## Status & lifecycle

```bash
mqlab vm status [<setup>|all]        # topology × live virsh state
mqlab qm up      <setup>             # pcs resource enable  mq_group  (start the QM, HA intact)
mqlab qm down    <setup>             # pcs resource disable mq_group  (stop cleanly, HA intact)
mqlab qm status  <setup>             # pcs status resources
mqlab vm down    <setup>             # graceful guest shutdown
mqlab vm destroy <setup>             # remove guests + overlay disks (base box untouched)
```

## Setups (selector for `vm`/`qm`/`obs instrument`)

| setup | what it is | provision playbook | QM step |
|---|---|---|---|
| `pcmk_san_ha` | Pacemaker/SAN, site-A 3-node HA | `site-pcmk.yml` | `qm create pcmk_san_ha` |
| `pcmk_san_dr` | Pacemaker/SAN 3+3 cross-site DR | `site-pcmk-dr.yml` | `qm create pcmk_san_ha` + `pcmk-dr-seed-peer.sh` |
| `rdqm_ha` / `rdqm_dr` | RDQM/RHEL HA / 3+3 DR | `site-rdqm.yml` | `lab/scripts/rdqm-qm-create.sh` |
| `standalone` | Phase-B single QM + DTCC sim + client | `site.yml` | (QM comes up in provision) |
| `distributed` | QMPCMK ⇄ QMDTCC over net-ext (epic #145) | `site-distributed.yml` | *(emerging — Plans 2–4; document once stable)* |
| `monitoring` | Prometheus/Grafana + MQ probe | `site-obs.yml` | n/a (use `mqlab obs up`) |

## Notes / gotchas

- **One-pass cold rebuild is the acceptance gate.** `net → vm create → vm
  provision → qm create` should succeed from a clean state in a single pass
  (#151/#152). A second `vm provision` is always safe (idempotent).
- **Selectors** accept a setup name (→ its members in bring-up order), `all`, or
  a regex over guest names. Destructive verbs require an explicit selector — no
  accidental "wipe everything."
- **`vm create` is the only Vagrant verb;** everything else is virsh/Ansible. The
  Vagrantfile does no provisioning (Ansible owns file transport).
- **Don't drive the lab from a feature worktree** without the copied-`.vagrant` /
  symlinked-`build/` dance — run bring-up from the main `develop` checkout.
