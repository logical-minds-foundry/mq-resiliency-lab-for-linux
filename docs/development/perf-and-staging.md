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
  - [5.1 Huge-page-backed guest RAM (macOS)](#51-huge-page-backed-guest-ram-macos)
- [6. Grain of salt: directional, not apples-to-apples](#6-grain-of-salt)
- [7. References](#7-references)

## 1. What is built today

| Piece | Issue | Status |
| --- | --- | --- |
| Perf record format (`src/mqlab/perf.py`) | #1201 | merged |
| `MQLAB_ENV` profiles (`src/mqlab/topology.py`) | #1202 | merged |
| Host-contention sampler (steal, host CPU/IO) | #1203 | merged |
| Bootstrap writes the perf report | #1205 | merged |
| `mqlab perf diff` (this page) | #1204 | merged |
| Failed-step time, pre-flight, guest busy/iowait/load, Vergil-VM steal | #1215 | merged |
| `memory_backing: hugepages` lever + on-demand reservation (§5.1) | #1241 | this change |

Every `mqlab bootstrap` writes a perf report (§3), including one that fails
partway or finds nothing to do. `mqlab perf diff` works on any file in the
`PerfRecord.to_json()` format, including reports written before #1215.

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
- **Only three levers can be overridden:** top-level `boot_batch`, top-level
  `memory_backing` (#1241, §5.1) and per-node `cpus`. A profile that touches
  any other key, or names a node the base does not declare, fails loud. So does
  a `memory_backing` value other than `hugepages`. Widening the lever set is a
  deliberate code change (`_TOP_LEVEL_LEVERS` / `_NODE_LEVERS`), made only
  when evidence justifies the new lever.
- **The profiles started empty** (`macos: {}`, `cloud: {}`), so the first
  parallel run was a pure platform comparison. Levers are added one at a time
  as evidence proves them. Today `macos` sets `memory_backing: hugepages`
  (§5.1) and `cloud` is still empty.

## 3. Where the perf report lands

Each bootstrap writes `perf-<timestamp>.json` beside its
`<timestamp>-bootstrap.log` transcript, in the `runs` directory of the shared
`state` bucket. Resolve that directory with `mqlab build path`, and never
hardcode a `build/` path:

```bash
ls "$(uv run mqlab build path state)/runs/"
```

Copy the macOS and cloud reports to one machine (any file names) and pass them
to `mqlab perf diff`.

### Report fields

The JSON is additive: new fields are added, existing ones are never renamed,
so older reports still diff.

- **`started_at`** is the Unix time the perf clock started. Since #1215 it
  starts *before* the bootstrap pre-flight, and every sample's `t` is seconds
  since then.
- **`phases.<name>`** has `seconds` and `retries` (summed over its steps),
  `failed` (how many of its steps failed, #1215) and `steps`. Each step has
  `label`, `seconds`, `retries` and `ok`. Phases appear in run order:
  - **`preflight`** (#1215) holds the pre-flight calls that run before any
    phase is selected: `render inventory` and `probe lab state`. On a cold
    lab the probe can take minutes (#1212), and before #1215 that time was not
    in the report at all. It is a report phase only; you cannot resume
    `--from preflight`.
  - **`net` / `vms` / `provision` / `observe`** are the bootstrap phases.
- **A failed step is kept** (#1215) with `"ok": false`. Its `seconds` are its
  *final* attempt's elapsed time and its `retries` are the attempts before
  that, which is the same final-attempt rule a successful step uses. It counts
  in its phase's `seconds` and `failed`. Before #1215 the failing step was
  dropped, so a failed `observe` read as under a second when its `site-obs.yml`
  step had actually run for almost 10 minutes.
- **`milestones`** holds per-VM `boot:<vm>` / `boot_retries:<vm>` and the
  observe readiness waits. A failed `vms` batch gives no `boot:` milestone.
- **`samples[]`** holds one entry per sampler tick (every 15 s):
  - `host.cpu`, `host.iowait` and `host.cpus` come from
    `virsh nodecpustats --percent` / `virsh nodeinfo` on the lab host.
  - `host.self_steal`, `host.self_busy` and `host.self_iowait` (#1215) are the
    **Vergil VM's own** CPU. The Vergil VM runs `mqlab` and libvirt, and it
    is itself a guest of the outer hypervisor (Apple Hypervisor on macOS, the
    cloud hypervisor on x86). These fields are deltas of its local
    `/proc/stat` aggregate line; no ssh is involved.
  - `guests.<name>` has `steal`, `busy` and `iowait` (#1215 added the last
    two), which are deltas of the guest's `/proc/stat` aggregate line. It also
    has `load1`, `load5` and `load15` (#1215) from the guest's
    `/proc/loadavg`. Both files are read in the **same** ssh call, so there is
    still one round trip per guest per tick.
  - That ssh call does **not** log in each tick (#1221). Every Ubuntu login
    runs the dynamic MOTD (`landscape-sysinfo`) through PAM. On a busy guest
    one run outlasted the 15 s tick, and the runs piled up on the node being
    measured. The probes therefore share one OpenSSH master connection per
    guest (`ControlMaster=auto`, `ControlPersist=60`), so each guest sees one
    login per run. The control sockets live in `$XDG_RUNTIME_DIR/mqlab-ssh-mux/`,
    or in `<system temp dir>/mqlab-ssh-mux-<uid>/` (mode 0700) when
    `XDG_RUNTIME_DIR` is unset. This is deliberately **not** under `build/`
    (#1228): on the macOS dev VM `build/` is a virtiofs mount, and ssh cannot
    create a Unix socket there, so every probe exited 255. See
    [`build-layout.md`](build-layout.md). The probes use a relative
    `ControlPath=%C` and run from that directory, so the socket path stays
    short however deep the directory is. A guest that is not up yet opens no
    master and is retried on the next tick. When the sampler stops it closes
    the masters (`ssh -O exit`), and any master that did not close is listed
    in `notes`.
  - If the mux can't be set up, probes still run (#1228). This covers a
    control dir that can't be created or isn't ours, and ssh exiting 255 with
    a control-socket error such as `muxserver_listen`. The sampler adds one
    note, `sampler: ssh multiplexing unavailable (...)`, and from then on uses
    plain `ssh -o ControlMaster=no -o ControlPath=none` for the rest of the
    run. The failed probe is retried that way at once, so no sample is lost.
    Each fallback probe is a full login again, so the MOTD pile-up can come
    back; the note tells you why.
  - **busy** is (user + nice + system + irq + softirq) / total ticks.
    **iowait** and **steal** are their own columns over the same total.
    Total is the first eight `/proc/stat` columns, since guest time is
    already inside user/nice.
  - A percentage is `null` on the first reading of a guest or of the Vergil
    VM, because a delta needs two readings. It is also `null` after a counter
    reset (a reboot). A guest whose probe failed is absent from that tick. A
    failed Vergil-VM probe leaves the `self_*` fields `null`. Each failure is
    recorded once in `notes` (key `guest <name>`, `host` or `vergil-vm`), and
    recovery is noted too.

Example sample (values illustrative):

```json
{
  "t": 615.2,
  "host": {"cpu": 62.4, "iowait": 3.1, "cpus": 24,
           "self_steal": 11.8, "self_busy": 58.0, "self_iowait": 2.9},
  "guests": {
    "obs": {"steal": 1.6, "busy": 97.2, "iowait": 0.4,
            "load1": 7.9, "load5": 6.2, "load15": 3.4}
  }
}
```

The bootstrap's closing perf summary prints the mean and peak of each of
these: host cpu/iowait, `vergil-vm` steal/busy/iowait, and per guest
steal/busy/iowait/load1. A phase with a failed step is flagged `N FAILED`.

## 4. Reading `mqlab perf diff`

`mqlab perf diff <a.json> <b.json>` prints a table. Every column is
**A relative to B**:

- **delta = A − B** seconds. A positive delta means A spent longer.
- **ratio = A / B**. `16.50x` means A took 16.5 times as long. It shows `—`
  when either side lacks the value or B is zero.

Sections:

- **Phases.** Summed seconds per phase (`preflight` / `net` / `vms` /
  `provision` / `observe`), with each side's retries. A phase that has a
  failed step on either side ends with `FAILED steps A/B <n>/<m>`. That
  step's time *is* in the phase seconds (§3). A report without the `failed`
  key counts as 0. Steps with no phase appear as `(unphased)`. A phase
  present on one side only is listed with `—` for the missing side.
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
- **Host means.** For each side, the mean host `cpu` and `iowait` and the
  Vergil VM's own `self_steal`, `self_busy` and `self_iowait` (§3) over the
  samples. A field with no reading (for example a report written before #1215
  has no `self_*` fields) shows `—`.
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

Since #1215 the samples can help separate those cases. The following is
interpretation, not a measured rule:

- High **guest busy** with high **load1** but low guest steal means the guest
  is saturated by its own work. In the first macOS baseline, guests
  intermittently missed the 10 s ssh probe while showing under 2 % steal,
  which was the gap this closes.
- High **guest iowait** points at storage.
- High **`self_steal`** means the outer hypervisor is starving the Vergil VM
  itself, and with it every nested guest. Guest steal is measured against the
  Vergil VM's vCPUs, so it cannot show this.

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

### 5.1 Huge-page-backed guest RAM (macOS)

**Evidence (data, spike #1240).** On the macOS dev VM, with a memory-churning
load on obs (12 processes looping mmap, touch every 4 KiB page, munmap), an
idle 1-vCPU guest's page first-touch went from 0.8 µs to 5.7–219 µs and its
re-touch of already-mapped memory from 0.16 µs to 1.7–153 µs. A pure-CPU load
on obs, an idle lab, and memory pressure did not reproduce it. With obs and
the measured guest backed by 2 MiB huge pages, the same churn left the other
guest at 0.67–1.33 µs / 0.20–0.23 µs, and obs itself completed about 110× more
churn cycles. Results and method:
<https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1240>.

**Reading (judgment, from the spike).** The slowdown is not an inherent cost
of arm64 nested virtualization. It appears when a guest churns memory, and the
cost is paid in the macOS hypervisor handling the nested guests' 4 KiB
second-level mappings. Huge pages cut the number of those mappings by up to
512×. obs's JVM tier (OpenSearch, Data Prepper, Dashboards) is exactly that
workload, which is why `observe` ran 40–50× longer on macOS than on cloud.
x86 cloud (Intel nested KVM) does not show the problem, so the `cloud`
profile keeps the default 4 KiB backing.

**What the lever does** (`env_profiles.macos.memory_backing: hugepages`):

- **Vagrantfile.** The lever reaches `lab/Vagrantfile` through the resolved
  topology (each node carries `memory_backing`), the same path `cpus` takes,
  never by reading `MQLAB_ENV`. The Vagrantfile adds vagrant-libvirt's
  `lv.memorybacking :hugepages`, which renders
  `<memoryBacking><hugepages/></memoryBacking>` in the domain XML. The
  syntax is `Config#memorybacking(option, config = {})` in vagrant-libvirt
  0.12.2 (`lib/vagrant-libvirt/config.rb` and
  `lib/vagrant-libvirt/templates/domain.xml.erb`), the version the dev VM
  installs.
- **Reservation before any `vagrant up`.** A huge-page-backed guest cannot
  boot without free huge pages. When the `vms` phase is about to run,
  `mqlab bootstrap` sizes the need from the effective topology: the RAM of
  each guest it will boot (those not already running), rounded up to 2 MiB
  pages, plus a 128-page (256 MiB) margin. `mqlab commons up` does the same
  for the commons. It then runs `ansible/host-hugepages.yml` against
  localhost with `become`. The play raises `vm.nr_hugepages` (runtime only,
  never `/etc/sysctl.d`) so that many pages are free on top of those already
  held by running guests. If the kernel can't assemble them, it runs
  `sync`, `vm.drop_caches=3` and `vm.compact_memory=1`, then retries once.
  If it is still short, it **fails loud** with needed vs got, and the bootstrap
  stops before booting anything. There is no silent fallback to 4 KiB backing.
  For `nativeha-ubuntu --no-dr` the need is 11,392 pages (22.25 GiB, of
  which obs is 10 GiB); for the full HADR set it is 14,464 pages (28.25 GiB).
- **Perf report.** The reservation is a `preflight` step,
  `reserve huge pages (<N> x 2 MiB)`, and a note records the need, the guests,
  the before and after `HugePages_*` counters, and whether the reclaim retry
  ran.
- **Release on teardown.** `mqlab teardown` resets `vm.nr_hugepages=0` only
  when the last stack is down, the same condition that reclaims the commons,
  and only after every destroy step succeeded. `--commons` alone does not
  release, because another stack's running guests may still hold pages; that
  case prints `huge pages kept`. The play verifies `HugePages_Total=0` and
  fails loud if a guest still maps pages.

Keep `MQLAB_ENV=macos` exported for `teardown` as well as `bootstrap`: the
release is gated on the lever, so a teardown without it leaves the pages
reserved. Check with `grep HugePages_ /proc/meminfo`, and release by hand
(from `ansible/`) with
`ansible-playbook host-hugepages.yml -c local -i localhost, -e hugepages_release=true`.

Acceptance for the lever is the real gate (#1200): a cold `MQLAB_ENV=macos`
`nativeha-ubuntu --no-dr` bootstrap with all guests huge-page-backed,
compared with cloud run 3 (#1237: 987 s; `opensearch_green` 17.7 s).

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
  environment". This is the source of the per-guest steal %, and (#1215) of
  busy % and iowait % and the Vergil VM's own steal/busy/iowait.
  <https://man7.org/linux/man-pages/man5/proc_stat.5.html>
- `proc_loadavg(5)`: the first three fields of `/proc/loadavg` are the 1, 5
  and 15 minute load averages (runnable plus uninterruptible-sleep tasks).
  This is the source of the per-guest `load1`/`load5`/`load15`.
  <https://man7.org/linux/man-pages/man5/proc_loadavg.5.html>
- `ssh_config(5)`: `ControlMaster`, `ControlPath` (including the `%C`
  connection hash) and `ControlPersist` define the connection multiplexing
  the guest probes use (#1221). <https://man.openbsd.org/ssh_config>
- `unix(7)`: a Unix socket path is limited by the size of `sun_path` (108 bytes
  on Linux), which is why the control socket path is kept relative and short.
  <https://man7.org/linux/man-pages/man7/unix.7.html>
- Linux kernel, *HugeTLB Pages*: `vm.nr_hugepages`, the `HugePages_Total`
  / `Free` / `Rsvd` counters in `/proc/meminfo`, and why a runtime
  allocation can fall short on fragmented memory.
  <https://docs.kernel.org/admin-guide/mm/hugetlbpage.html>
- Linux kernel, `/proc/sys/vm`: `drop_caches` and `compact_memory`, used
  for the reservation's reclaim retry (#1241).
  <https://docs.kernel.org/admin-guide/sysctl/vm.html>
- libvirt domain XML, *Memory Backing*: the `<memoryBacking><hugepages/>`
  element vagrant-libvirt emits. <https://libvirt.org/formatdomain.html#memory-backing>
- `pam_motd(8)`: the PAM module that shows the message of the day at login.
  On Ubuntu it runs the dynamic MOTD scripts, which the per-tick logins set off.
  <https://man7.org/linux/man-pages/man8/pam_motd.8.html>
