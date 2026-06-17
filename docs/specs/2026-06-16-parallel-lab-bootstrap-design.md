# Parallel lab bootstrap — design

- **Issue:** #211
- **Date:** 2026-06-16
- **Status:** Proposed
- **Scope:** Bounded-concurrent VM creation in the `mqlab` orchestrator, plus
  Ansible-native fan-out for safe provisioning. Hard-ordered phases stay serial.
- **Shape:** Spike-first. A Phase-0 spike (§3.0) proves the concurrency
  *mechanism* and sets the concurrency *degree* before the orchestrator change is
  built — the same way the Phase A provider spike de-risked the harness.

## 1. Problem

Lab bootstrap drives every VM boot and every provisioning play through a single
linear loop (`run_steps`, `src/mqlab/orchestrator.py:39`). `mqlab vm create`
emits one `vagrant up <node>` step per guest (`_create_step`, `src/mqlab/cli.py:421`)
and the loop runs them strictly one at a time. End-to-end bring-up is
"go-get-lunch" long.

The dominant cost is **VM creation**, not provisioning. The host (`vergil.toml`,
`[vm.vergil-user]`: 12 vCPU / 64 GiB) KVM-accelerates the arm64 guests (~36 s to
ready, per the Phase A provider spike) but runs the six RDQM x86_64 guests under
**TCG emulation** — ~11–15 min each, CPU-bound at ~1:1 throughout the boot
(`docs/reports/2026-06-06-phase-a-provider-spike.md`). Six of those serially is
the lunch break. The boots are independent of one another, which makes them the
safe, high-value target for concurrency.

Provisioning is mostly *not* safe to parallelize at the orchestrator level: the
Pacemaker arm has hard ordering (iSCSI target → initiator login → LUN format →
cluster-wide rescan → cluster formation → STONITH → QM), and both arms end with
a serial cluster-join. Those barriers must remain serial.

## 2. Goals and non-goals

**Goals**

- Run independent `vagrant up` boots concurrently, bounded by a configurable
  degree, to cut bring-up wall-clock.
- Speed up genuinely-independent provisioning work (per-node package installs,
  per-site fan-out) via Ansible's own parallelism — without the orchestrator
  launching parallel `ansible-playbook` processes.
- Preserve the lab's glass-box posture: a readable top-to-bottom plan, step-mode
  checkpointing, fail-loud aggregation, and an attributable transcript.

**Non-goals (YAGNI)**

- Per-node live-stream UI with independent transcripts.
- Ansible `strategy: free` (revisit only if a measured play proves fork-bound).
- A general dependency-graph (DAG) rework of the orchestrator.

## 3. Design

### 3.0 Phase 0 — mechanism + degree spike (gates the rest)

Two assumptions underpin the orchestrator change, and both must be proven on the
real lab VM before the change is built. The spike is a throwaway measurement run
(documented in a short report), not production code.

**Assumption 1 — N concurrent `vagrant up <node>` processes actually run in
parallel.** Vagrant serializes work with lock files (a global box-add lock and
per-environment/per-machine action locks). Concurrent `vagrant` *processes* in
one project dir may block one another or error (`"another process is already
executing"`) instead of booting in parallel — most acutely on a cold rebuild,
where all boots race to import the same base box. If that happens, the
per-process design (chosen to keep per-node transcript/fail-loud granularity)
degrades to serial or fails, and delivers nothing.

The spike compares, on a cold box, timed and observed for actual parallelism:

1. **Per-process:** 3× concurrent `vagrant up <node>` as separate processes
   (the design's preferred mechanism — preserves per-node `CommandStep`s).
2. **Native-parallel:** a single `vagrant up <n1> <n2> <n3>` with vagrant-libvirt
   parallelism (one process, one `CommandStep`; per-node status reconstructed by
   parsing Vagrant's `[node]`-prefixed output).

The mechanism that genuinely parallelizes wins and is recorded here as the
implemented mechanism. If per-process serializes or races, the orchestrator
fans out by launching the one native-parallel process and the ParallelGroup
members become the parsed per-node status lines rather than separate processes.

**Assumption 2 — the concurrency degree.** TCG boot is CPU-bound at ~1:1, so the
wall-clock floor for the six RDQM boots is roughly
`(6 guests × 2 vCPU × per-vCPU work) / available cores`. The default degree is
therefore *derived*, not guessed:

```text
max_parallel_default = floor((host_vcpus - reserve) / guest_vcpus)
```

with a small `reserve` for libvirt, the arm64/KVM guests, obs/fixtures, and the
agent session. The spike **sweeps** `-j 3 / 4 / 6` on a cold boot and records the
value that minimizes wall-clock without oversubscription thrash; that measured
value is the shipped default. (For reference: 12 vCPU, 2-vCPU guests, modest
reserve → the formula lands in the 4–6 range, which the sweep confirms or
corrects.)

**Phase-0 exit criteria:** a chosen mechanism and a measured default `-j`,
written into this section, before §3.1 is implemented.

### 3.1 Orchestrator: the parallel-group execution model

**Data model.** A plan is today `list[CommandStep]`. Add one unit:

```text
ParallelGroup(label: str, members: list[CommandStep], max_parallel: int)
```

A plan becomes `list[CommandStep | ParallelGroup]`. A lone `CommandStep` behaves
exactly as today; a `ParallelGroup` is the new concurrent unit. The plan stays a
flat, inspectable list — the whole bring-up sequence still reads top to bottom.

**Execution (`run_steps`).** The loop gains one branch:

- `CommandStep` → unchanged: run, stream to the transcript sink, check exit code,
  `pauser.wait()` if step-mode.
- `ParallelGroup` → submit every member to a bounded
  `ThreadPoolExecutor(max_workers=max_parallel)`. Threads (not asyncio) because
  `runner.run` already blocks and streams to a sink; threads reuse it untouched.
  (If the Phase-0 spike selects native-parallel, a group instead drives the one
  `vagrant up <subset>` process and renders per-node status from its parsed
  output; the failure/checkpoint semantics below are unchanged.)
  **Wait for all futures — never cancel.** An early failure therefore lets
  in-flight boots drain to a defined state (a fully-created VM is cleaner to
  inspect or reuse than one killed mid-boot). After all members complete,
  aggregate exit codes:
  - any non-zero → raise `StepFailedError` naming **every** failed member; do
    **not** advance to the next unit and do **not** pause.
  - all zero → in step-mode, `pauser.wait()` **once**, after the group.

**Box pre-warm (cold-boot safety).** Before the first create `ParallelGroup`,
emit a one-time, idempotent serial step per distinct base box that ensures the
box's libvirt volume is present (import it if absent). This removes the cold-boot
box-import race (multiple first boots racing to upload the same image) and makes
cold and warm boots behave identically — directly serving the cold-rebuild gate
(§5). It runs regardless of which mechanism Phase 0 selects.

**Transcript / rendering.** The shared transcript is written under a lock, with
every line prefixed by its node (`[rdqm-a1] …`) so interleaved output stays
attributable. The renderer shows the group header and a per-member status line
(running / ok / failed). This is the explicit mitigation for "a hang and a slow
op look identical" once output interleaves — a stalled member is visible at a
glance.

**Planner change.** `_plan_create` stops appending one step per guest and instead
emits a single `ParallelGroup` containing `_create_step(g)` for every ABSENT
guest. `_plan_up` (virsh `start`, also independent per guest) groups the same
way. Hard-ordered phases remain ordinary sequential `CommandStep`s — they are
never grouped.

**Idempotent re-run.** After a drained failure (some guests created, one failed),
re-running `mqlab vm create` is safe: `_plan_create` is state-aware and classifies
the already-created guests as present, so the next group contains only the guests
still ABSENT. This is a guarantee of the design, not an accident of timing.

### 3.2 CLI surface

On `mqlab vm create` (and `mqlab vm up`):

- `--max-parallel / -j N` — concurrency degree, threaded to
  `ParallelGroup.max_parallel`. Default is the Phase-0 measured value (§3.0).
- `--serial` — a **distinct** control, not a synonym for `-j 1`. It suppresses
  grouping entirely: the planner emits one `CommandStep` per guest (today's
  linear plan), so each guest is its own step-mode checkpoint. This is the
  full-transparency debugging path (step-and-inspect each boot individually).
  `-j 1`, by contrast, still builds a single `ParallelGroup` run with
  `max_workers=1` — sequential execution but one post-group checkpoint. The two
  differ deliberately at the checkpoint granularity, and `--serial` is the one to
  reach for when isolating a hang.

### 3.3 Provisioning: Ansible-native, no orchestrator change

Keep `mqlab vm provision` as a single `ansible-playbook` step. Obtain
"safe provisioning" parallelism by raising `forks` in `ansible.cfg` to cover the
largest per-arm host group (6), so Ansible fans each task across all nodes in a
play at once. Keep the default **`linear`** strategy: it still parallelizes per
task up to `forks` while preserving the per-task barrier that the storage and
cluster-formation ordering depends on. `strategy: free` is explicitly deferred
(it removes that barrier and is risky around the `run_once` storage plays).

This keeps provisioning fail-loud and Ansible-native, consistent with the
repo principle of preferring Ansible over shell for sysadmin orchestration, and
confines the orchestrator change to VM creation where the long pole is.

## 4. Testing

Unit-test `run_steps` group handling with a fake runner:

1. All members succeed → execution advances and pauses exactly once after the
   group (step-mode).
2. One member fails → siblings still run to completion; then `StepFailedError`
   names the failed member(s); execution stops (no advance, no pause).
3. `--serial` → the planner emits per-guest `CommandStep`s (no `ParallelGroup`),
   each its own checkpoint; `-j 1` → one `ParallelGroup`, sequential, one
   post-group checkpoint. The two are asserted distinct.
4. `max_parallel` bounds in-flight members — tested **deterministically**, no
   sleeps or wall-clock. A synchronizing fake runner increments a shared counter
   on entry and decrements on exit under a lock, recording the observed maximum,
   and uses a `threading.Barrier(max_parallel)` (or latch) so the test asserts
   that (a) observed concurrency never exceeds `max_parallel`, and (b) a full
   barrier of that width is actually reached.

Assert transcript node-prefixing and the failure-aggregation message. Everything
runs within the existing `vrg-validate` gate (mind the known gotchas: StrEnum /
UP042, ruff magic-comma, 100% branch coverage, `uv run pytest`, no masked exit
codes).

## 5. Acceptance gate

This is a lab bring-up change, so lint-green ≠ done. It is accepted only after a
**full VM cold rebuild** brings the lab up one-pass on the parallel path,
capturing before/after wall-clock and confirming no emulated-boot thrash at the
chosen `-j`. The Phase-0 spike (§3.0) already exercises cold boots, so its
report supplies the before/after numbers and the measured default; the gate
confirms the integrated path end-to-end.

## 6. Risks

- **Mechanism may not parallelize.** Concurrent `vagrant` processes may serialize
  or race on locks. Retired by the Phase-0 spike (§3.0) before any build.
- **Emulated-boot thrash** at too high a `-j` (net slowdown or OOM). Mitigated by
  the bounded pool, the derived default, and the spike's `-j` sweep.
- **Cold-boot box-import race.** Mitigated by the serial box pre-warm step (§3.1).
- **Interleaved output obscuring a hang.** Mitigated by node-prefixed transcript
  lines, the per-member status display, and the `--serial` escape hatch.
- **Accidental grouping of an ordered step.** Mitigated by grouping only inside
  `_plan_create` / `_plan_up`; ordered phases are emitted as plain `CommandStep`s.

## 7. Coordination

The in-flight `#199` (rdqm-parity-build) also touches `src/mqlab/cli.py` and the
arm-backend seam introduced by `#212`. This change touches `run_steps`
(`orchestrator.py`) and `_plan_create` / `_plan_up` (`cli.py`). The
implementation plan should land aware of `#199`'s footprint to avoid a messy
merge — sequence behind it or keep the cli.py edits narrowly scoped to the
create/up planners.
