# mqlab CLI Namespace Rationalization — Design

> **Status:** approved design, ready for planning.
> **Date:** 2026-06-25
> **Author:** Phillip Moore (with Claude)
> **Issue:** #350
> **Related:** #345 (globally-unique per-QM ports for concurrency);
> partly supersedes `2026-06-17-pcmk-rhel-third-arm-design.md` (the
> `pcmk-rhel` arm is dropped); builds on
> `2026-06-16-parallel-lab-bootstrap-design.md` and
> `2026-06-17-nativeha-crr-vm-arms-design.md`.

---

## 1. Problem

The CLI namespace grew incoherent as features were bolted on. The concrete
symptoms:

- **Nine `setups` with inconsistent shapes.** For one technology
  (`pcmk-ubuntu`) there are three setups — `pcmk_san_ha` (HA only),
  `pcmk_san_dr` (adds site B, no svc/app), and `distributed-pcmk-ubuntu`
  (HA + svc/app but **no** site B). "Distributed" means HADR + svc/app for
  `nativeha` but HA-only + svc/app for `pcmk` and `rdqm`. The shapes don't
  agree even within one tool.
- **Two overlapping concepts.** `arms` (the per-mechanism *behavior* —
  `qm-create/up/down/status`) and `setups` (the *shape* — node groups, QM
  config, provision playbook) are separate, so a "stack" has no single
  definition.
- **~40 commands across 6 groups** (`net`, `vm`, `obs`, `qm`, `pki`,
  `build`), with `create` vs `provision` ambiguous.
- **`bootstrap` does the job halfway and differently per stack.** It runs
  host-gate → `net create` → `vm create` (bare `vagrant up`, no
  provisioning) → `obs up`. It never provisions the cluster or instruments
  the guests, so the SUT is never built and Grafana shows no data — yet the
  command reports success. (Root-caused 2026-06-25: `_bootstrap_run` stops at
  `obs_up()`; the cluster daemons were never started — `cluster_daemon_up=0`
  on every node.)

The goal is a coherent model a new operator can drive end-to-end: clone or
download the tarball, `uv sync`, `mqlab bootstrap <stack>`, and the entire
stack — cluster, queue manager, observability, instrumentation — comes up in
one pass, identically for every stack.

## 2. Goals & Non-Goals

**Goals**

1. Exactly **four canonical stacks**, each a single full-HADR shape.
2. **One unified `stacks` concept** replacing both `setups` and `arms`.
3. A **rationalized verb surface** centered on `bootstrap`, with the four
   lifecycle phases as named, resumable steps rather than competing verbs.
4. **Idempotent, resumable, fail-loud bootstrap** — never a silent
   half-state.
5. **Concurrent co-location**: two stacks run on one libvirt host, sharing a
   per-host commons (OBS + multi-instance svc/app).
6. **Documented semantics** — a command reference stating exactly what each
   verb and phase does.

**Non-goals (deferred to their own specs)**

- Building the net-new `nativeha-ubuntu` stack (its config block and
  address/port reservations are defined here; the mechanism implementation
  is separate).
- Cross-host (Mac ↔ cloud) shared observability. Each host has its own
  commons; cross-host stacks are independent libvirt environments.

## 3. The Stack Model

The `setups` block (9) and `arms` block (4) merge into a single `stacks`
block with **exactly four** entries. Every stack is the **same canonical
shape**: a 3-node HA cluster at site A, a 3-node DR cluster at site B, SAN
nodes where the mechanism needs them, plus references to the shared commons
(which the stack does **not** own).

| stack | HA mechanism (site A) | DR mechanism (→ site B) | os | status |
|---|---|---|---|---|
| `pcmk-ubuntu` | Pacemaker/corosync + SAN (iSCSI) | DRBD async cross-site | Ubuntu | implemented |
| `rdqm-rhel` | RDQM HA (DRBD sync, 3-node) | RDQM DR (DRBD async → site B) | RHEL | implemented |
| `nativeha-rhel` | Native HA (quorum, 3-node) | CRR | RHEL | implemented |
| `nativeha-ubuntu` | Native HA (quorum, 3-node) | CRR | Ubuntu | **defined, build deferred** |

Each stack entry carries, in one place:

- `mechanism` (`pacemaker-san` | `rdqm` | `native-ha`) and `os`.
- `verbs` — the runtime QM controls (`qm-create/up/down/status`, plus
  mechanism extras like `dr-cutover`), exactly as today's `arms` block.
- **shape** — the node groups for site A, site B, and SAN.
- `qm` — QM name and VIP(s).
- **explicit address/port allocations** (see §6) — cluster node IPs, QM
  VIP(s), svc QM name + port, app instance id, exporter port range — so a
  stack's full footprint is readable in one block and co-located stacks
  provably don't overlap.
- `provision` — the single full-HADR playbook for the stack (see §7).

**Deleted:** the 8 non-canonical setups (`pcmk_san_ha`, `pcmk_san_dr`,
`distributed-pcmk-ubuntu`, `pcmk_san_rhel_ha`, `rdqm_ha`, `rdqm_dr`,
`distributed-rdqm-rhel`), the `monitoring` setup (becomes `commons`), and the
entire `pcmk-rhel` arm (not one of the four target stacks).

### 3.1 Commons (shared, not a stack)

OBS (Prometheus + Grafana), the `mon-probe` exporter host, the `svc` VM, and
the `app` VM are modeled as a separate `commons` concept — **one set per
libvirt host**, multiplexing one instance per co-located stack. Bootstrap
ensures them idempotently; they are never duplicated per stack.

## 4. Command Surface

The four phases stop being competing verbs and become **named, ordered steps
of `bootstrap`**. This is what dissolves the `create`-vs-`provision`
confusion.

### 4.1 Phases

| # | phase | what it does | absorbs |
|---|---|---|---|
| 1 | `net` | ensure shared libvirt networks | `net create/up` |
| 2 | `vms` | make this stack's VMs exist + boot; ensure shared commons VMs exist | `vm create/up` |
| 3 | `provision` | build cluster + QM on them; add this stack's svc QM + cross-connect channels + app instance | `vm provision` + `qm create` |
| 4 | `observe` | ensure OBS up + instrument this stack (exporters, scrape targets, dashboard) | `obs up` + `obs instrument` (renders targets/dashboards/reach internally) |

### 4.2 Tier 1 — stack lifecycle

- `mqlab bootstrap <stack>` — runs all four phases end-to-end. Idempotent and
  **resumable**: a re-run continues from the first incomplete phase.
- `mqlab bootstrap <stack> --from <phase>` — re-run that phase and everything
  after (resume after a failure).
- `mqlab bootstrap <stack> --only <phase>` — run exactly one phase (e.g.
  re-`provision` after a playbook edit).
- `mqlab teardown <stack>` — remove this stack's cluster/SAN VMs and its
  commons instances; commons VMs/OBS are **reference-counted** and reclaimed
  only when no stack remains (see §5.2).
- `mqlab teardown <stack> --commons` — also tear down the shared commons
  (clean-host teardown).
- `mqlab status [<stack>]` — one unified view: every stack, its phase
  progress, commons health. Absorbs `vm status`, `net status`, `obs status`.
- `mqlab ssh <guest>` — unchanged.

### 4.3 Tier 2 — runtime / scenario control

- `mqlab qm <status|up|down|move> <stack>` — drive the QM to induce
  failover/DR (uses the stack's mechanism `verbs`). `qm create` is gone — it
  is the `provision` phase.
- `mqlab run <stack>` — the post-bring-up baseline/parity test driver.

### 4.4 Plumbing (kept, orthogonal)

`doctor`, `build <path|ensure|clean|status|migrate>`, `pki <ensure|issue|list>`,
`parity`.

### 4.5 Demoted to phase internals (no longer user-facing)

`net create/up/down/destroy/show`, `vm create/up/down/destroy/inventory/roster`,
`obs targets/dashboard/net-state/reach-peers/up/instrument`.

**Net effect:** ~40 commands across 6 groups → 4 lifecycle + 2 runtime + 4
plumbing.

## 5. Lifecycle, Idempotency & Commons

### 5.1 Phase state and resumption

Each stack records which phases have completed, in `build/state/` (the
irreplaceable-facts bucket). `bootstrap` consults it:

- Default runs from the first incomplete phase forward — a re-run after a
  failure resumes; it does not restart.
- Every phase is **idempotent and re-entrant** (existing `creates:`/state
  guards already give this for most steps); re-running a completed phase is a
  safe no-op.
- **Fail-loud, halt-forward:** a phase that errors stops the run with the
  failing phase named and the remediation command
  (`mqlab bootstrap <stack> --from <phase>`) printed. No phase reports success
  on partial work. No swallowed errors. (This is the structural fix for the
  `_bootstrap_run` silent stop.)

### 5.2 Reference-counted commons

- The `vms`/`provision`/`observe` phases **ensure** the commons before adding
  this stack's slice. Existence is detected from live virsh/probe state (as
  `_probe_states` does today); if already up, the phase only adds this stack's
  instance.
- `teardown <stack>` removes the stack's cluster/SAN VMs and its commons
  instances (its svc QM, app instance, scrape targets, dashboard), then checks
  whether any other stack still references the shared commons VMs/OBS.
  **Last one out reclaims them**; otherwise they stay up.
- `--commons` forces commons teardown regardless.

### 5.3 Preflight

`bootstrap` runs the `doctor` host-gate first (as `_prepare_lab` already
does); fail-loud if the host isn't ready, before touching networks.

## 6. Concurrency Implementation

Governing idea: **stacks differentiate by port and instance name on the
shared commons, not by extra VMs or IPs.**

- **Per-host commons.** Each libvirt host has exactly one commons set
  (`obs` + `mon-probe` + `svc` VM + `app` VM) serving the ≤2 co-located
  stacks. Cross-host stacks are independent; no cross-host networking. Target
  layout: Mac runs `pcmk-ubuntu` + `nativeha-ubuntu`; cloud runs `rdqm-rhel`
  + `nativeha-rhel`.
- **`svc` VM — multi-instance by QM + port.** One VM, one QMSVC instance per
  stack (distinct QM name + listener port), each peering with its stack's QM
  over `net-ext` via that stack's cross-connect channels. Added by
  `provision`, removed by teardown.
- **`app` VM — multi-instance by unit.** One VM, one app/client instance per
  stack (separate systemd unit + config), pointed at its own stack's QM
  VIP/connection.
- **`mon-probe` — multi-instance exporters by port.** Each stack gets its own
  mq_prometheus exporter instance(s) on its own port range (no fixed
  `:9157`/`:9158` collision). Added by `observe`.
- **Per-stack-additive Prometheus targets + Grafana dashboards.** Instead of
  one fleet-wide `node.json` listing every host (the cause of the stale
  "down" targets), `observe` registers only this stack's node + exporter
  targets and its dashboard; `teardown` removes them. Prometheus then reflects
  exactly what is running.
- **Explicit, written-down allocation.** Each stack's config declares its
  cluster node IPs, QM VIP(s), svc QM name+port, app unit id, and exporter
  port range. Co-located stacks are non-overlapping because the values are
  explicit, not derived (glass-box). `nativeha-ubuntu`'s block is **reserved
  now** so it provably can't collide with `pcmk-ubuntu`. Aligns with #345.

## 7. Migration & Consolidation

- **Config collapse (`topology.yaml`):** `setups` (9) + `arms` (4) → one
  `stacks` block (4). `monitoring` setup → `commons`. `pcmk-rhel` arm
  deleted.
- **Playbook consolidation.** Each technology's three shape-specific
  playbooks collapse to **one full-HADR playbook per stack**:
  - `site-pcmk.yml` + `site-pcmk-dr.yml` + `site-distributed.yml` → one
    `site-pcmk.yml`.
  - `site-rdqm.yml` + `site-rdqm-dr.yml` + `site-rdqm-distributed.yml` → one
    `site-rdqm.yml`.
  - `site-nativeha.yml` is already full-HADR + svc/app — it becomes the
    template the others match.
  - The standalone `qm-create` playbooks fold into each stack's provision
    playbook as its QM step.
- **Phase ownership of commons:** `vms` ensures the shared `svc`/`app`/`obs`
  VMs exist; `provision` adds this stack's svc QM + channels + app instance;
  `observe` adds this stack's exporters + targets + dashboard. Teardown
  unwinds in reverse with the reference-count check.
- **CLI refactor:** `_bootstrap_run` becomes the phase sequencer
  (net → vms → provision → observe) with per-stack phase-state, `--from` /
  `--only`, fail-loud/halt-forward. Demoted groups' logic moves into phase
  functions; their user-facing verbs are removed.
- **Breaking changes are clean.** Personal lab on `develop`; old setup names
  and the `net`/`vm`/`obs`/`qm` command groups go away with no deprecation
  shims. Existing `build/state` setup manifests are discarded; the cold
  rebuild regenerates them.
- **Documentation deliverable:** a `docs/` command reference stating what each
  verb and phase does.

## 8. Testing & Acceptance

- **Unit:** CLI invocation; phase-state resume logic; `--from`/`--only`;
  commons reference-counting (last-one-out rule); exporter/QM port-allocation
  non-collision.
- **Validation:** `vrg-container-run -- vrg-validate` green; `parity` harness
  still passes.
- **Cold-rebuild acceptance gate** (standing rule — lint-green ≠ done):
  `mqlab bootstrap <stack>` from scratch for each of the 3 implemented stacks
  must come up one-pass, end-to-end, with the Grafana dashboard actually
  populated.
- **Concurrency acceptance:** bootstrap both Ubuntu stacks on one host;
  confirm both dashboards populate, no port/IP collisions, one shared OBS
  scrapes both.

## 9. Rough Implementation Sequence

1. `stacks` config model + delete non-canonical setups/arms.
2. Consolidate provision playbooks (one full-HADR playbook per stack).
3. CLI phase sequencer + `teardown`/`status` + demote groups.
4. Concurrency: multi-instance svc/app/exporters + per-stack additive targets.
5. Cold-rebuild + concurrency validation + command-reference docs.
