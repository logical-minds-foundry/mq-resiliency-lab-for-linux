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

Everything else a bring-up needs — the baked boxes, the arch-correct MQ tarball,
the Ansible galaxy collections, and the PKI CA + keystores — is **ensured
automatically** by `mqlab bootstrap` before the phase that needs it (and only for
the phases actually selected). There is nothing else to install by hand.

Run every `mqlab` command from the **repo root** (the main `develop` checkout).

Secrets and the fence key are **auto-generated and persisted** under the
gitignored `build/state/secrets/` — they survive VM rebuilds and regenerate on a
full `build/` wipe. There is no password to remember or export
(`lab/scripts/lab-secret.sh`).

### Where state lives (and how to actually wipe it)

Three independent layers — know which one you're touching:

| Layer | Lives on | `rm build/state/vagrant`? | `vrg-vm rebuild`? | Wiped by |
|---|---|---|---|---|
| Repo + `build/` (secrets, fence key, rendered configs) | virtiofs mount from your Mac | survives | **survives** (host, not VM) | `rm -rf build/` |
| Lab VM disks (`lab_*.img`, `lab_san-*-vdb.qcow2`, incl. the SAN `/dev/vdb` with DRBD md) | `/var/lib/libvirt/images` on the Vergil VM's root disk | **survives** | not reliably | **`mqlab teardown <stack>`** |
| Vagrant bookkeeping (`build/state/vagrant/`) | shared `build/state` bucket | n/a | survives | that dir |

**Removing Vagrant's bookkeeping wipes nothing real.** It is only Vagrant's
per-machine metadata (`libvirt/id` = the domain UUID, box metadata). It makes
Vagrant *forget* which libvirt domains are "its" — the domains and disks stay,
and the next bootstrap then collides with the orphans. It is **not** a reset.

**The only true wipe is `mqlab teardown`** — `virsh undefine
--remove-all-storage --nvram`, which deletes the domain, **both** disks (`vda`
*and* the SAN `vdb` carrying stale DRBD metadata), and the UEFI nvram. Stale
`/dev/vdb` md is what breaks a DR build (#158/#160), so a clean DR build **must**
go through `teardown`.

**Cold-reset a stack (the real one):**

```bash
mqlab teardown pcmk-ubuntu        # destroys ALL member VMs + their disks (+ commons when last stack out)
virsh -c qemu:///system vol-list --pool default | grep -E 'lab_(san|pcmk)' || echo clean
mqlab bootstrap pcmk-ubuntu       # net → vms → provision → observe, from scratch
```

Recreating only *some* VMs leaves the rest carrying stale state (e.g. a prior
Pacemaker cluster config → `pcs cluster setup` fails), so tear the whole stack
down. `build/` (secrets/keys) survives, so the rebuild stays reproducible; to
reset those too, `rm -rf build/`.

---

## The one command: `mqlab bootstrap <stack>`

`bootstrap` stands a whole stack up by running four idempotent phases in order,
each gated by a live "satisfied?" probe so a re-run **resumes from the first
incomplete phase**:

| phase | what it does |
|-------|--------------|
| `net` | define + autostart + start every lab libvirt network (guests attach to them) |
| `vms` | `vagrant up` the stack members + the shared commons VMs (obs/probe/svc/app/infra) |
| `provision` | NIC + DNS bring-up, the stack's `site-*.yml`, and the queue manager |
| `observe` | render + provision Prometheus/Grafana targets and instrument the nodes |

```bash
mqlab doctor                 # pre-flight the host (arch, KVM, required tools)
mqlab bootstrap pcmk-ubuntu  # the whole stack, every step streamed
mqlab bootstrap pcmk-ubuntu --from provision   # force a starting phase
mqlab bootstrap pcmk-ubuntu --only observe     # run a single phase
mqlab bootstrap pcmk-ubuntu --step             # pause after each step
```

The QM comes up **inside the `provision` phase** — there is no separate
"create the QM" bring-up step anymore. Use `mqlab qm` (below) to drive its
lifecycle afterward.

---

## The four stacks

`bootstrap` / `teardown` / `qm` / `status` all take a **stack** name. The
canonical registry (`lab/topology.yaml → stacks:`):

| stack | what it is | provision playbook | mechanism |
|---|---|---|---|
| `pcmk-ubuntu` | Pacemaker/SAN, full HA + cross-site DR (san-a/b + pcmk-a1..3 + pcmk-b1..3) | `site-pcmk.yml` | pacemaker-san |
| `rdqm-rhel` | RDQM/RHEL HA + 3+3 DR (rdqm-a1..3 + rdqm-b1..3) | `site-rdqm.yml` | rdqm |
| `nativeha-rhel` | Native HA (RHEL) — raft-log replication + 3+3 CRR | `site-nativeha.yml` | native-ha |
| `nativeha-ubuntu` | Native HA (Ubuntu) — the OS-as-only-variable peer of `nativeha-rhel` | `site-nativeha-ubuntu.yml` | native-ha |

`mqlab parity` prints the live cross-arm capability matrix (which verb each arm
supports).

---

## Networks & observability

Both were once separate bring-up steps; they are now **phases of `bootstrap`**:

- The **`net` phase** brings the libvirt fabric up first (nothing to run by
  hand). The groomed `lab/scripts/net-up.sh` / `net-down.sh` remain as a
  hand-run reference for the whole fabric in one shot.
- The **`observe` phase** renders the scrape targets/dashboards and provisions
  the obs pair + this stack's exporters + node instrumentation.

To stand the shared observability VMs up **independently of any stack**:

```bash
mqlab commons up          # boot obs + probe + svc + app + infra, provision Prometheus/Grafana/Loki
mqlab commons status      # commons health (topology × live virsh state)
mqlab obs open            # print the Grafana URL + the (automatic) forward recipe
```

---

## Per-stack bring-up

### Pacemaker/SAN — `pcmk-ubuntu`

The full 8-node topology: san-a/b + pcmk-a1..3 + pcmk-b1..3, DRBD-async under the
SAN, DR-ready from the start. Site A runs live; site B receives on cutover.
QM `PCMKAPP` on the shared LUN as a Pacemaker resource group
(`mq_fs → mq_vip → mq_vip_ext → mq_qm`).

```bash
mqlab bootstrap pcmk-ubuntu          # net → vms → provision (iSCSI/DRBD, Corosync/Pacemaker, STONITH, QM) → observe
mqlab qm status pcmk-ubuntu          # pcs status of the mq resource group
```

DR exercises (after the stack is up):

```bash
lab/scripts/pcmk-dr-seed-peer.sh     # teach site B about the QM (addmqinf + disabled unit) so a cutover can start it
lab/scripts/pcmk-dr-cutover.sh a2b   # controlled cutover site A → B (quiesce → confirm replication → flip DRBD → bring B up)
lab/scripts/pcmk-dr-cutover.sh b2a   # failback B → A (mirror)
```

Both partner VIPs (`mq_vip` + `mq_vip_ext`) float together on HA failover and
move together on DR cutover. DRBD/cutover internals and recovery:
`docs/reference/drbd-operations.md` and `docs/reference/lab-gotchas.md`.

### RDQM — `rdqm-rhel`

```bash
mqlab bootstrap rdqm-rhel            # net → vms → provision (site-rdqm.yml, RDQM HA/DR) → observe
mqlab qm status rdqm-rhel            # /opt/mqm/bin/rdqmstatus
mqlab dr cutover  rdqm-rhel          # cross-site cutover A → B (a2b)
mqlab dr failback rdqm-rhel          # failback B → A (b2a); add --rpo0-drill to assert no message loss
```

### Native HA — `nativeha-rhel` / `nativeha-ubuntu`

Shared-nothing raft-log replication (no SAN, no extra disk). The two arms are
OS-as-only-variable peers and can coexist on one host.

```bash
mqlab bootstrap nativeha-ubuntu      # net → vms → provision (site-nativeha-ubuntu.yml, raft HA + CRR) → observe
mqlab qm status nativeha-ubuntu      # dspmq -o nativeha -x
```

---

## Status & lifecycle

```bash
mqlab status [<stack>]               # phase completion (net/vms/provision/observe, ✓/✗) — one stack or all
mqlab qm up      <stack>             # start the QM (pcs enable / strmqm / systemctl start, per mechanism)
mqlab qm down    <stack>             # stop the QM cleanly (HA intact)
mqlab qm status  <stack>             # the QM's HA resource / instance state
mqlab teardown   <stack>             # remove the stack's guests + overlay disks (base box untouched)
mqlab teardown   <stack> --commons   # …and also reclaim the shared commons VMs
```

## Notes / gotchas

- **One-pass cold rebuild is the acceptance gate.** `mqlab bootstrap <stack>`
  should succeed from a clean state in a single pass (#151/#152); if a phase
  fails, re-running resumes from it (idempotent).
- **`bootstrap` / `teardown` / `qm` require a stack name** — no accidental "wipe
  everything." Bring-up order (SANs first, site-A before site-B) is fixed by the
  stack's `groups:` in `topology.yaml`.
- **Vagrant is used only to boot the guests** (the `vms` phase); everything else
  is virsh/Ansible. The Vagrantfile does no provisioning (Ansible owns file
  transport).
- **Drive the lab from the main `develop` checkout**, not a feature worktree —
  the shared `build/state` bucket carries the one canonical Vagrant/libvirt
  state.
