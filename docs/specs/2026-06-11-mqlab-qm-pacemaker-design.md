# mqlab qm — Pacemaker QM-HA lifecycle (Layer A, Ubuntu arm)

**Issue:** #109
**Date:** 2026-06-11
**Status:** Design — awaiting review
**Scope:** Layer A, **Pacemaker/SAN (Ubuntu) arm only**. RDQM arm and Layer B
(operational HA/DR experiments) are deferred to separate specs.

## 1. Goal

Add `mqlab qm` — the queue-manager-service lifecycle on top of the now-complete
net/vm/obs lifecycle and the cluster-infra `vm provision`. It manages the IBM MQ
queue manager and its **HA configuration** as a symmetric, transparent verb set, for
the Pacemaker/SAN arm.

The deliverable is **deep, reproducible understanding of the Pacemaker QM-HA
procedure.** Today that procedure lives in the imperative, discovery-grade
`lab/scripts/pcmk-qm-create.sh` (shell fired over `ansible -m shell`, one step at a
time — exactly what was needed to *discover* the steps). This work **crystallizes it
into a declarative, idempotent Ansible role (`mq-pcmk-qmgr`)** — the artifact a client
could re-implement in their own automation — and drives it through `mqlab qm` verbs
whose streamed output **is** the documented step-by-step.

This completes, over the QM service, the same methodology the lab already applies up
to the point of bringing up the third-party product (IBM MQ): transparent management,
full visibility, reproducibility.

## 2. Verbs

Pacemaker arm; each takes a **setup selector** (e.g. `pcmk_san_ha`):

| `mqlab` | action | mechanism |
|---|---|---|
| `qm create <setup>` | build the QM **and** its HA | run the `mq-pcmk-qmgr` role via `site-pcmk-qm.yml`, streamed |
| `qm up <setup>` | start the cluster-managed QM | `pcs resource enable mq_group` (direct, streamed) |
| `qm down <setup>` | clean stop without tearing down HA | `pcs resource disable mq_group` |
| `qm destroy <setup>` | remove the QM + HA resources | teardown play `site-pcmk-qm-down.yml` |
| `qm status <setup>` | who owns the QM + resource-group state | `pcs status` / `dspmq` on the cluster |

Same shape as vm/net: **create/destroy go through the reproducible playbook/role;
up/down are direct one-liner `pcs` ops** mqlab streams verbatim (exactly like
`vm up` = `virsh start`). `create` builds the group and Pacemaker auto-starts it, so
after `create` the QM is already up; `up`/`down` are the post-create on/off.

**Arm-aware, Pacemaker-only implementation.** The `qm` namespace is designed so the
RDQM arm slots in behind the same verbs later (the eventual verb-parity comparison);
this spec implements only the Pacemaker path.

## 3. Per-setup QM config (topology)

The scripts hardcode `QMPCMK` / `10.10.1.200`. Move that into the declarative source —
`setups` gain an optional `qm:` block:

```yaml
pcmk_san_ha:
  groups: [san_a, pcmk_a]
  provision: ansible/site-pcmk.yml
  secrets: [pcmk_hacluster_password]
  qm: { name: QMPCMK, vip: 10.10.1.200 }
```

`mqlab qm` reads `setup.qm` and passes it to the playbooks as extra-vars
(`-e qm_name=… -e qm_vip=…`); the role/teardown use `{{ qm_name }}` / `{{ qm_vip }}`.
A setup with no `qm:` block → `qm` verbs error in mqlab's own voice (exit 2). The
`Setup` dataclass gains a `qm: dict | None` field (parsed in `lab_setups()`); a small
typed accessor exposes `name`/`vip`.

## 4. The `mq-pcmk-qmgr` role — the crystallized procedure

A new `ansible/roles/mq-pcmk-qmgr/` re-expresses `pcmk-qm-create.sh` as declarative,
idempotent tasks, targeting the setup's `pcmk_a` group. Task groups, mirroring the
script (each step visible when streamed):

1. **QM on the LUN** (creation node only — `run_once`/first `pcmk_a` host): mount
   `MQSHARED`, `crtmqm -md /mqshared/qmgrs -ld /mqshared/log {{ qm_name }}` (idempotent
   via a `dspmq` guard / `creates`).
2. **MQSC config**: listener `L1414`/1414, `APP.SVRCONN` channel (lab posture:
   `MCAUSER('mqm')`, `HBINT(15) KAINT(15)`, CHLAUTH disabled, blank CONNAUTH),
   `QLOCAL(HA.TEST) DEFPSIST(YES)`; then `endmqm`.
3. **Teach the other nodes** the definition (`dspmqinf -o command` → `addmqinf` on the
   remaining `pcmk_a` hosts), idempotent; unmount the LUN on the creation node
   (Pacemaker owns it afterward).
4. **Disabled systemd unit** `mq-{{ qm_name }}.service` on every cluster node
   (`ExecStart=strmqm`, `ExecStop=endmqm -w`, disabled — Pacemaker is the only
   starter).
5. **Resource stickiness** `resource-stickiness=1000` (no auto-failback — the #64
   lesson; a controlled move-back runs `endmqm` and drops reconnected clients).
6. **The resource group** `mq_group`: `mq_fs` (Filesystem on `MQSHARED`, monitor
   `OCF_CHECK_LEVEL=20`, `on-fail=fence`) → `mq_vip` (IPaddr2 `{{ qm_vip }}`) →
   `mq_qm` (`systemd:mq-{{ qm_name }}`), ordered + colocated (idempotent via
   `pcs resource status` guards).

`site-pcmk-qm.yml` is a thin playbook: `hosts: pcmk_a`, `become: true`, `roles:
[mq-pcmk-qmgr]`.

**Teardown** (`site-pcmk-qm-down.yml`): `pcs resource delete mq_group` → stop + `dltmqm
{{ qm_name }}` (and `rmvmqinf` on the others) → remove the systemd units. Idempotent
(guards so a partial teardown re-runs cleanly).

## 5. State / idempotency

Unlike vm/net (where `virsh` ops aren't idempotent → the #99 probe-classify-plan
machinery was required), the `qm` ops are **already idempotent**: an Ansible role
(re-run converges) and `pcs enable/disable` (no-ops if already in state). So `qm`
**leans on that** — **no probe-classify-plan**. Instead:

- **Light pre-flight** (reuse #99 `_probe_states`/`classify`): the setup's members must
  be `RUNNING`. If not, advise `mqlab vm up <setup> first` and exit 3 (the
  `vm provision` precondition pattern). If the cluster isn't *provisioned* (members up
  but `site-pcmk.yml` not run), the role fails loud with Ansible's own clear error — we
  do not pre-check cluster formation.
- **`qm status`** for visibility (`pcs status resources` + `dspmq` on the owner),
  streamed pass-through.

## 6. Components

- **`ansible/roles/mq-pcmk-qmgr/`** + **`ansible/site-pcmk-qm.yml`** +
  **`ansible/site-pcmk-qm-down.yml`** — the reproducible procedure + teardown.
- **`lab/topology.yaml`** — `qm:` block on `pcmk_san_ha` (and later `pcmk_san_dr`).
- **`src/mqlab/setups.py`** — `Setup.qm` field + accessor.
- **`src/mqlab/cli.py`** — a `qm_app` Typer group; `qm_create`/`qm_destroy` run their
  playbooks (reusing the `vm provision` pattern: pre-flight → render the static
  inventory → `ansible-playbook` with the `qm_*` extra-vars); `qm_up`/`qm_down`/
  `qm_status` run a single direct `pcs` command over `ansible` on a cluster node,
  streamed. **No secret sourcing** — the QM role runs `pcs` against an already-authed
  cluster (the `pcmk_hacluster_password` auth happened during `vm provision`).

## 7. Data flow

```
topology.yaml (setup.qm, secrets)
        │  mqlab qm create <setup>
        ▼
pre-flight (members RUNNING?) ──no──▶ advise mqlab vm up, exit 3
        │ yes
        ▼  render the static inventory (#101)
uv run ansible-playbook site-pcmk-qm.yml -e qm_name=… -e qm_vip=…   (streamed)
        ▼
mq-pcmk-qmgr role applies the 6 task groups → Pacemaker mq_group up
```

`up`/`down`/`status` skip the playbook and run a single streamed `pcs` op on a cluster
node.

## 8. Error handling (fail-loud)

| condition | exit | behavior |
|---|---|---|
| unknown setup / no `qm:` block | 2 | mqlab error naming `mqlab vm status` |
| members not `RUNNING` | 3 | advisory note + `mqlab vm up <setup>` |
| cluster not provisioned | role's code | Ansible fails loud; mqlab propagates |
| playbook / pcs op fails | its code | `StepFailedError` → propagate; the tool's own error already streamed |
| created OK | 0 | success |

No swallowed failures. Re-runs are safe (Ansible/pcs idempotency).

## 9. Transparency / the ledger

`qm create`'s playbook output streams every task — the step-by-step the human watches
and the client reproduces. The RDQM-vs-Pacemaker *step-count comparison* (the "ledger")
lands when the RDQM arm exists; it is **out of scope** here. This spec establishes the
hard arm and the verb structure.

## 10. Testing

- `setups.py`: `Setup.qm` parse (present + defaulted-None).
- `qm` verb flow (CommandRunner fake, no live lab): pre-flight members-down → exit 3,
  no playbook; happy path → `ansible-playbook site-pcmk-qm.yml` with `-e qm_name`/`qm_vip`;
  `up`/`down`/`status` → the right single `pcs` op; unknown
  setup / no-`qm` → exit 2; playbook failure → propagate.
- `vrg-container-run -- vrg-validate` green at 100% branch (Python). The role itself is
  validated by a **live `qm create pcmk_san_ha`** on a provisioned cluster (Ansible
  roles aren't pytest-unit-tested).

## 11. Scope boundary

**In:** `mqlab qm create/destroy/up/down/status` (Pacemaker); the `mq-pcmk-qmgr` role +
`site-pcmk-qm.yml` + `site-pcmk-qm-down.yml`; per-setup `qm:` config; CLI tests; a live
`qm create pcmk_san_ha` acceptance.

**Out:** the RDQM arm (`mqlab qm` for `rdqm_*`); Layer B (drive + observe HA/DR
experiments); message-path/client testing. The existing `pcmk-qm-create.sh` stays as
the hand-run reference until the role supersedes it.
