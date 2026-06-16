# Parallel lab bootstrap — design

- **Issue:** #211
- **Date:** 2026-06-16
- **Status:** Proposed
- **Scope:** Bounded-concurrent VM creation in the `mqlab` orchestrator, plus
  Ansible-native fan-out for safe provisioning. Hard-ordered phases stay serial.

## 1. Problem

Lab bootstrap drives every VM boot and every provisioning play through a single
linear loop (`run_steps`, `src/mqlab/orchestrator.py:39`). `mqlab vm create`
emits one `vagrant up <node>` step per guest (`_create_step`, `src/mqlab/cli.py:421`)
and the loop runs them strictly one at a time. End-to-end bring-up is
"go-get-lunch" long.

The dominant cost is **VM creation**, not provisioning. The host (`vergil.toml`,
`[vm.vergil-user]`: 12 vCPU / 64 GiB) KVM-accelerates the arm64 guests but runs
the six RDQM x86_64 guests under **TCG emulation** — each emulated vCPU is a host
thread doing binary translation, so those boots are the long pole. The boots are
independent of one another, which makes them the safe, high-value target for
concurrency.

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
  **Wait for all futures — never cancel.** An early failure therefore lets
  in-flight boots drain to a defined state (a fully-created VM is cleaner to
  inspect or reuse than one killed mid-boot). After all members complete,
  aggregate exit codes:
  - any non-zero → raise `StepFailedError` naming **every** failed member; do
    **not** advance to the next unit and do **not** pause.
  - all zero → in step-mode, `pauser.wait()` **once**, after the group.

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

### 3.2 CLI surface

On `mqlab vm create` (and `mqlab vm up`):

- `--max-parallel / -j N` — concurrency degree, threaded to
  `ParallelGroup.max_parallel`.
- `--serial` — convenience equal to `-j 1`: every group degrades to its members
  run sequentially, each its own step-mode checkpoint. This is the
  full-transparency debugging path (e.g. isolating which boot hangs).

**Default: `-j 3`.** Rationale (data): the host is 12 vCPU; each x86_64 guest is
2 vCPU, so three concurrent TCG boots use ~6 emulated vCPUs and leave headroom
for each to make progress; arm64/KVM guests are cheap, so 3 is conservative for
them too. The "3 is the sweet spot" claim is **judgment, not measurement** — the
cold-rebuild gate (§5) sets the real number and may revise this default.

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
3. `--serial` / `-j 1` → a group produces sequential per-member checkpoints.
4. `max_parallel` actually bounds the number of in-flight members.

Assert transcript node-prefixing and the failure-aggregation message. Everything
runs within the existing `vrg-validate` gate (mind the known gotchas: StrEnum /
UP042, ruff magic-comma, 100% branch coverage, `uv run pytest`, no masked exit
codes).

## 5. Acceptance gate

This is a lab bring-up change, so lint-green ≠ done. It is accepted only after a
**full VM cold rebuild** brings the lab up one-pass on the parallel path,
capturing before/after wall-clock and confirming no emulated-boot thrash at the
chosen `-j`. That measurement retro-justifies (or corrects) the `-j 3` default.

## 6. Risks

- **Emulated-boot thrash** at too high a `-j` (net slowdown or OOM). Mitigated by
  the bounded pool, the conservative default, and the cold-rebuild measurement.
- **Interleaved output obscuring a hang.** Mitigated by node-prefixed transcript
  lines, the per-member status display, and the `--serial` escape hatch.
- **Accidental grouping of an ordered step.** Mitigated by grouping only inside
  `_plan_create` / `_plan_up`; ordered phases are emitted as plain `CommandStep`s.
