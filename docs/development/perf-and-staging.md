# Perf reports and bootstrap staging — parallel validation

**Purpose.** How to compare a cold bootstrap on macOS/arm64 against the same
commit on x86 cloud, read the difference with `mqlab perf diff`, and tune the
staging levers from that evidence, one lever at a time. The design is epic
`logical-minds-foundry/.github#275` (`epics/275-bootstrap-staging/spec.md`);
this page is the operator procedure for its §3 (parallel validation) and §4
(iterate).

## Contents

- [1. What is built today](#1-what-is-built-today)
- [2. The parallel-run procedure](#2-the-parallel-run-procedure)
- [3. Where the perf report lands](#3-where-the-perf-report-lands)
- [4. Reading `mqlab perf diff`](#4-reading-mqlab-perf-diff)
- [5. One lever at a time](#5-one-lever-at-a-time)
- [6. Grain of salt: directional, not apples-to-apples](#6-grain-of-salt)
- [7. References](#7-references)

## 1. What is built today

| Piece | Issue | Status |
| --- | --- | --- |
| Perf record format (`src/mqlab/perf.py`) | #1201 | merged |
| `MQLAB_ENV` profiles (`src/mqlab/topology.py`) | #1202 | merged |
| Host-contention sampler (steal, host CPU/IO) | #1203 | merged |
| Bootstrap writes the perf report | #1205 | not built |
| `mqlab perf diff` (this page) | #1204 | this change |

Until #1205 lands, **no bootstrap writes a perf report**, so there is nothing
real to diff yet. `mqlab perf diff` already works on any file in the
`PerfRecord.to_json()` format.

## 2. The parallel-run procedure

The commands on this page run `mqlab` from a development checkout, so they use
`uv run mqlab`. See
[operating the lab from a dev session](operating-the-lab-from-a-dev-session.md#1-the-vms-are-libvirt-guests-under-qemusystem)
for why, and for where that convention stops.

1. **Same commit on both platforms.** Check out the same commit (or branch
   head) on the macOS host and on the x86 cloud host. A diff across different
   commits measures the code change *and* the platform at once, and you cannot
   separate the two.
2. **Select the environment profile.** Set `MQLAB_ENV` in the shell that runs
   `mqlab`:

   ```bash
   # macOS/arm64 host
   export MQLAB_ENV=macos
   uv run mqlab bootstrap nativeha-ubuntu --no-dr

   # x86 cloud host
   export MQLAB_ENV=cloud
   uv run mqlab bootstrap nativeha-ubuntu --no-dr
   ```

3. **Collect both perf reports** (see §3) onto one machine.
4. **Diff them**, macOS as A and cloud as B:

   ```bash
   uv run mqlab perf diff macos-perf.json cloud-perf.json
   ```

### How `MQLAB_ENV` actually applies

These are the real semantics from `src/mqlab/topology.py`. They are not
obvious from the variable's name.

- **The `mqlab` process reads it, not Vagrant.** `mqlab` merges
  `env_profiles.<env>` from `lab/topology.yaml` onto the base topology. It
  then renders the result to the resolved topology under the `work` bucket,
  and `lab/Vagrantfile` reads that file. Vagrant never reads `MQLAB_ENV`.
  Export it in the shell that runs `mqlab` (don't prefix a single command
  only). Every `mqlab` verb that renders the resolved topology re-renders it
  from whatever `MQLAB_ENV` it sees. So a later `mqlab` call without the
  variable silently goes back to base values for the next Vagrant load.
- **Values:** unset or empty means the base topology, unchanged. `macos` or
  `cloud` applies that profile. Anything else fails loud (`ValueError`), and
  a value with no matching `env_profiles` entry fails loud too.
- **Only two levers can be overridden:** top-level `boot_batch` and per-node
  `cpus`. A profile that touches any other key, or names a node the base does
  not declare, fails loud. Widening the lever set is a deliberate code change
  (`_TOP_LEVEL_LEVERS` / `_NODE_LEVERS`), made only when evidence justifies
  the new lever.
- **Both profiles start empty** (`macos: {}`, `cloud: {}`). Until a lever is
  set, `MQLAB_ENV=macos` and `=cloud` both run the base topology. The first
  parallel run is therefore a pure platform comparison, which is the baseline
  you want.

## 3. Where the perf report lands

Emission is Task 3 of the epic (#1205) and **is not built yet**. Its file
name and exact location are defined by that change, not here. The plan puts
it with the bootstrap's run record, which lives in the `runs` directory of the
shared `state` bucket. Resolve that directory with `mqlab build path`, and
never hardcode a `build/` path:

```bash
ls "$(uv run mqlab build path state)/runs/"
```

Copy the macOS and cloud reports to one machine (any file names) and pass them
to `mqlab perf diff`.

## 4. Reading `mqlab perf diff`

`mqlab perf diff <a.json> <b.json>` prints a table. Every column is
**A relative to B**:

- **delta = A − B** seconds. A positive delta means A spent longer.
- **ratio = A / B**. `16.50x` means A took 16.5 times as long. It shows `—`
  when either side lacks the value or B is zero.

Sections:

- **Phases.** Summed seconds per phase (`net` / `vms` / `provision` /
  `observe`), with each side's retries. Steps with no phase appear as
  `(unphased)`. A phase present on one side only is listed with `—` for the
  missing side.
- **Milestones.** Named points such as `opensearch_green`, with the same
  delta and ratio. A milestone recorded on one side only is shown, not dropped.
- **Dominant divergence.** The phase, present on both sides, with the largest
  absolute delta. This is where to look first; it is not a verdict.
- **Top steal contributors.** For each side, the guests with the highest
  mean vCPU steal % across the host-contention samples. Each sample carries a
  `guests` map of guest name to `{"steal": <pct>}`. A guest's first reading is
  a baseline with `"steal": null`; it is expected and skipped. A current perf
  record always has a `samples` list. When nothing was sampled the list is
  empty, and this shows `(no readable steal samples)` with a note. A report
  with no `samples` key at all (written before the #1203 sampler) shows `n/a`
  and a note explains why. The diff still runs in both cases.
- **Notes.** Each report's own `notes` (degraded or unavailable samples),
  plus diff-level notes: a stack mismatch between A and B, missing samples,
  or sample entries that had no readable steal value (counted, then skipped).

There is **no pass/fail verdict**, by design. The output helps you judge; it
does not judge for you. A report that is not a perf record (unreadable,
invalid JSON, missing `phases` or `milestones`, a non-numeric duration) makes
the command exit 2 with a message naming the file and the problem. It never
compares a partial report.

**What to look for:** the *shape* of the bottleneck. Check which phase
dominates on each side, and whether that phase coincides with high steal on
specific guests. The spec's motivating case is `observe`: about 33 min during
a full macOS bootstrap against seconds on an uncontended host. A diff that
shows `observe` dominating alongside high steal on `obs` points at CPU
contention. If `observe` dominates with *low* steal, the cause is more likely
I/O or memory bandwidth. Measuring and separating those cases is the reason
the sampler exists.

## 5. One lever at a time

The loop (spec §4, validation #1200):

1. Change **one** lever in **one** profile, usually `macos` (for example
   `boot_batch: 2`, or `nodes: { obs: { cpus: 8 } }`).
2. Build the same commit on both platforms and run the parallel procedure.
3. Read the diff against the previous run of the same platform *and* against
   the other platform.
4. Keep, revert, or tune based on that evidence. Then make the next change.

Don't batch changes. The variables interact, and the relationships are
**non-linear**: more vCPU does not mean more speed, which is why obs vCPU is
a tuning candidate at all. If two levers change together and the run gets
faster, you cannot tell which lever helped, or whether one helped while the
other hurt. Each lever change that proves out is its own small PR. A
reliability claim needs the consecutive-pass count from the spec (5 clean
cold bootstraps), not a single green run.

## 6. Grain of salt

The two sides run on **fundamentally different hardware**: a macOS laptop
hypervisor with nested guests against a cloud host with dedicated cores. The
VM architectures and network stacks differ too. Guests are sized about the
same (vCPU and memory), but wall-clock seconds are **directional, not
apples-to-apples**:

- Compare **shape**, not seconds. "Observe dominates on macOS but not on
  cloud" is a finding. "macOS is 312 s slower" on its own is not.
- **Expect different sweet spots per platform.** A macOS-specific throttle
  may be unnecessary on cloud, which can keep its profile maximal.
- Ratios between runs on the **same** platform (before/after one lever) carry
  much more weight than ratios across platforms.

## 7. References

Prior art this procedure draws on:

- Brendan Gregg, *The USE Method*: "For every resource, check utilization,
  saturation, and errors." Steal time is a saturation signal for a guest's
  vCPUs. <https://www.brendangregg.com/usemethod.html>
- Brendan Gregg, *Active Benchmarking*: analyse the system *while* the
  workload runs, so the result explains its own limiter. That is why the perf
  report carries contention samples next to the timings.
  <https://www.brendangregg.com/activebenchmarking.html>
- `proc_stat(5)`: the `steal` field of `/proc/stat` is "Stolen time, which is
  the time spent in other operating systems when running in a virtualized
  environment". This is the source of the per-guest steal %.
  <https://man7.org/linux/man-pages/man5/proc_stat.5.html>
