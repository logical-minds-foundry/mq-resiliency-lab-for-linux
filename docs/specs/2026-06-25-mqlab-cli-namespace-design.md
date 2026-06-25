# mqlab CLI Namespace Rationalization — Design

> **Status:** approved design, ready for planning.
> **Date:** 2026-06-25
> **Author:** Phillip Moore (with Claude)
> **Issue:** #350
> **Depends on:** #351 (QM naming convention — lands first; provides the
> per-stack distinct QM names that concurrent co-location relies on).
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
- The fleet-wide queue-manager rename (drop the `QM` prefix; per-stack
  `<short>APP`/`<short>SVC` names). That lands first as #351; this spec
  depends on its distinct per-stack names for concurrent co-location.

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
- `mqlab commons <up|status|down>` — stand up / check / tear down the shared
  per-host commons (OBS + `svc` + `app` + probe) independently of any stack.
  Stack phases still ensure it idempotently; this is the standalone peer to
  `teardown <stack> --commons` (the workflow today's `obs up` gave you).

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

### 5.1 Completion is observed, not recorded

There is **no stored "phase-done" ledger** — a recorded flag can drift from
reality (you edit a provision playbook; a VM dies out of band; a phase writes
"done" too early), which is exactly the "command says success, cluster is
empty" trap this effort exists to kill.

Instead, each phase defines a cheap **"satisfied?" probe** against live state,
and `bootstrap` re-derives what's done every run:

- `net` — the shared networks exist.
- `vms` — this stack's guests (and the commons VMs) are created and booted.
- `provision` — the cluster daemons are up and the QM is defined.
- `observe` — the stack's exporter responds and its targets are registered.

`bootstrap` runs each phase whose probe is unsatisfied, in order. Resumability
falls out for free and **cannot drift**: "completed" always means *observed*,
never *asserted*. This extends the existing `_probe_states` pattern from
guest-state to phase-state.

- Every phase is **idempotent and re-entrant** (existing `creates:`/state
  guards already give this for most steps); re-running a satisfied phase is a
  safe no-op.
- **Fail-loud, halt-forward:** a phase that errors stops the run with the
  failing phase named and the remediation command
  (`mqlab bootstrap <stack> --from <phase>`) printed. No phase reports success
  on partial work. No swallowed errors. (This is the structural fix for the
  `_bootstrap_run` silent stop.)
- `--only` / `--from` override the probe (force a re-run) for when a phase is
  probe-satisfied but you changed its inputs (e.g. edited a playbook).

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

### 5.4 Prerequisite-ensures (don't regress them)

Recent fixes bolted "ensure X before doing Y" onto today's verbs (#306 build
ensure on every command; #334/#336 ensure the setup's MQ tarball; #342 ensure
exporter PKI in `obs up`; #344 the `_ensure_prereqs` galaxy+MQ+PKI helper;
open #307 auto-`pki ensure` before provisioning). The phase refactor must
carry these forward, not drop them. Each phase owns its prerequisite-ensure,
**converged into the single `_ensure_prereqs` helper** rather than scattered:

- root callback — `build ensure` (unchanged, #306).
- `vms` — boxes + MQ tarball present.
- `provision` — galaxy collections + MQ + PKI material (`_ensure_prereqs`,
  closing #307).
- `observe` — exporter PKI (#342).

Turning the scattered ensures into one helper per phase makes this a
consolidation, not a regression risk.

## 6. Concurrency Implementation

Governing idea: **stacks differentiate by port and instance name on the
shared commons, not by extra VMs or IPs.**

- **Per-host commons.** Each libvirt host has exactly one commons set
  (`obs` + `mon-probe` + `svc` VM + `app` VM) serving the ≤2 co-located
  stacks. Cross-host stacks are independent; no cross-host networking. Target
  layout: Mac runs `pcmk-ubuntu` + `nativeha-ubuntu`; cloud runs `rdqm-rhel`
  + `nativeha-rhel`.
- **`svc` VM — multi-instance, per-stack independent.** One VM hosting one
  **independent service QM per stack** (one-shared is rejected: the automated
  HA/DR failover testing — partial skeleton already in the scripts —
  manipulates each stack's service QM on its own). Each peers with its stack's
  QM over `net-ext` via that stack's cross-connect channels, on a **distinct
  listener port** (external observability — see each stack's service
  distinctly from outside). QM **names** follow the #351 convention
  (`<short>SVC`, distinct per stack — distinct names also avoid the MQ
  same-host same-name collision on this shared VM). **PKI:** a shared
  `app-org` identity/keystore is reused across the per-stack service QMs; a
  spike confirms `MON.SVRCONN` SSLPEER pinning holds with N co-resident QMs
  before the full concurrency build. Added by `provision`, removed by
  teardown.
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
  cluster node IPs, QM VIP(s), service QM name (per #351) + port, app unit id,
  and exporter port range. Co-located stacks are non-overlapping because the
  values are explicit, not derived (glass-box). `nativeha-ubuntu`'s block is
  **reserved now** so it provably can't collide with `pcmk-ubuntu`. Aligns
  with #345 (ports) and #351 (names).

## 7. Migration & Consolidation

- **Config collapse (`topology.yaml`):** `setups` (9) + `arms` (4) → one
  `stacks` block (4). `monitoring` setup → `commons`. `pcmk-rhel` arm
  deleted.
- **Playbook consolidation (highest-risk task — composable role-includes, not
  a monolithic merge).** Each technology's three shape-specific playbooks
  collapse to **one full-HADR playbook per stack**, assembled from validated
  building blocks — `cluster-HA` + `DR-replication` + `commons-wiring` — so
  the single playbook is a composition, not a hand-merge:
  - `site-pcmk.yml` + `site-pcmk-dr.yml` + `site-distributed.yml` → one
    `site-pcmk.yml`.
  - `site-rdqm.yml` + `site-rdqm-dr.yml` + `site-rdqm-distributed.yml` → one
    `site-rdqm.yml`.
  - `site-nativeha.yml` is already full-HADR + svc/app — it becomes the
    template the others match.
  - The standalone `qm-create` playbooks fold into each stack's provision
    playbook as its QM step.

  **Risk to budget for:** the stack you run today (`pcmk-ubuntu`, currently
  HA-only via the old `distributed-pcmk-ubuntu`) has not exercised site B +
  DRBD cross-site in the distributed shape. Making full HADR canonical will
  likely surface latent site-B/DR bugs — treat this as "consolidate playbooks
  *and* fix the pcmk-ubuntu DR path," proven by the cold-rebuild gate.
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
- **No in-place migration.** Landing this destroys any currently-running lab
  (e.g. the half-built `distributed-pcmk-ubuntu`) and cold-rebuilds under the
  new stack names — consistent with the ephemeral, reproducible-lab
  philosophy. Tear down before, rebuild after.
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
- **Concurrency acceptance (manual, human-driven):** on the cloud host,
  bootstrap **both RHEL stacks** (`rdqm-rhel` + `nativeha-rhel`) — the two
  implemented stacks that co-locate there — and confirm both dashboards
  populate, no port/QM-name/IP collisions, one shared OBS scrapes both. (The
  Ubuntu pair can't serve this test until `nativeha-ubuntu` is built; the
  cloud RHEL pair is the validatable concurrency case.)

## 9. Rough Implementation Sequence

0. **Depends on #351** (QM naming convention) landing first — provides the
   distinct per-stack QM names concurrency relies on.
1. `stacks` config model + delete non-canonical setups/arms.
2. Consolidate provision playbooks into composable role-includes (one
   full-HADR playbook per stack); fix the pcmk-ubuntu DR path as it surfaces.
3. CLI phase sequencer (probe-derived completion) + `teardown`/`status`/
   `commons` + demote groups; converge prerequisite-ensures into
   `_ensure_prereqs`.
4. PKI spike (per-stack service QMs on one host; SSLPEER pinning), then
   concurrency: multi-instance svc/app/exporters + per-stack additive targets.
5. Cold-rebuild + (manual) RHEL-pair concurrency validation + command-
   reference docs.
