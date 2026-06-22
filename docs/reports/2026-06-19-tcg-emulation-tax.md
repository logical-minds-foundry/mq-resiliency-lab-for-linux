# The TCG Emulation Tax — why the lab's CPU numbers are not meaningful on Apple Silicon

> **Issue:** #289. **Date:** 2026-06-19. **Author:** live debugging session
> (read-only, on the libvirt host).
> **Related:** #276 (make the lab host-arch-aware), #283 (acquire out-of-Vergil
> x86 acceptance environments).
> **Status:** evidence report — the empirical backbone for #276/#283.

## TL;DR

The investigation started from a simple question: *why are six idle Native HA
nodes burning ~75% CPU?* The answer turned out to be bigger than the question.

1. **The CPU is consumed by emulation, not by MQ.** The six Native HA nodes are
   RHEL 9.6 **x86_64 guests running on an aarch64 (Apple Silicon) host**, so QEMU
   runs them under **TCG** — full software CPU emulation. There is no message
   traffic; the replication links carry only idle-heartbeat noise (40–100 bps).
   The CPU is the cost of *translating every guest instruction on the host*.
2. **It is not a Native HA problem.** The RDQM arm — same RHEL x86_64 TCG
   platform, a completely different HA mechanism (DRBD-style replicated storage,
   not raft) — shows the identical cost. The discriminator is "**is a queue
   manager process running**," not the HA technology. Same-platform VMs with no
   running QM sit at ~4%.
3. **The "uniform drop from 75% → 40%" was a host-contention artifact.** Every
   QM-running guest tracks the **hypervisor's own CPU in lockstep** because they
   all draw from one saturated host CPU pool. When host saturation eased, all of
   them dropped together; the genuinely-idle VMs never moved.

**The load-bearing conclusion:** *a CPU number that scales with host contention
and is dominated by JIT-translation overhead is not a measurement of MQ
behavior.* The lab's performance and timing graphs on this substrate are, for
quantitative purposes, **meaningless** — they show numbers, but the numbers do
not mean what a reader assumes they mean. To get meaningful performance data, the
queue-manager-bearing arms must run on **native x86 KVM** (#276/#283).

---

## 1. Why this matters (the decision this report supports)

The lab exists to produce *trustworthy* evidence about MQ HA/DR behavior —
including how the arms behave under load. The whole observability stack (#103,
#279, #285) exists to turn that behavior into graphs an engineer can reason about.

This report demonstrates that, on the current Apple-Silicon dev substrate, the
**CPU dimension of those graphs is not trustworthy**. The numbers are a blend of:

- genuine (emulated) guest computation, and
- pure emulation/scheduling overhead that scales with how contended the host is.

There is no clean way for a reader to separate the two from the graph alone. The
flat ~78% the Native HA nodes were showing is, more than anything, the **ceiling
of a saturated host** — not a property of the queue managers.

This is precisely the "emulation tax" that #276 was opened to remove ("on x86 the
whole lab runs native KVM and we stop paying the emulation tax that makes the
current setup slow") and that #283 was opened to validate against (a real x86
host with nested KVM, "a cloud instance is the likely route"). This report is the
quantified justification those issues were missing.

It also reframes the **lab's purpose on Apple Silicon**: the Mac substrate is
excellent for **functional correctness** (does failover work, does the QM re-form
quorum, does the app reconnect) — which is exactly what the Native HA Phase-1
findings claimed and no more ("Functional correctness only (TCG — no timing
claims)"). It is **not** a platform for any *quantitative* performance claim. That
boundary needs a real x86 host.

---

## 2. The environment (the nesting that creates the problem)

```
Apple Silicon Mac (arm64, Apple hypervisor)
└─ Lima / Vergil dev VM  ── aarch64, 12 vCPU, 62 GiB     ← this is the "host" below
   └─ libvirt / QEMU
      ├─ aarch64 guests  → -accel kvm  -cpu host         ← NATIVE, cheap (obs, mon-probe, app, svc)
      └─ x86_64  guests  → -accel tcg  -cpu max          ← EMULATED, expensive (all RHEL arms)
```

Measured facts (from the libvirt host, `lima-vergil-user-...`):

| Property | Value |
|---|---|
| Host architecture | `aarch64` (Apple) |
| Host CPUs / memory | 12 / 62 GiB |
| x86_64 guest accelerator | `-accel tcg -cpu max` (e.g. `nha-rhel-a1`) |
| aarch64 guest accelerator | `-accel kvm -cpu host` (e.g. `obs`) |
| `/dev/kvm` present? | Yes — but it can only accelerate **aarch64** guests |

The critical point: **KVM accelerates only same-architecture guests.** KVM is a
thin shim that lets guest instructions run directly on the physical CPU. An x86_64
instruction stream cannot run directly on an ARM core, so for x86 guests there is
nothing for KVM to accelerate — QEMU must fall back to **TCG**, its Tiny Code
Generator, which *translates* each block of guest x86 instructions into host ARM
instructions at runtime (a JIT). `/dev/kvm` existing on this host is a red
herring: it is used by the aarch64 guests (which is why `obs` is cheap), and is
simply inapplicable to the x86 guests. This is **intrinsic to running x86 guests
on Apple Silicon**, not a misconfiguration — #276's design already calls it out:
"The only remaining TCG is foreign-arch guests on the dev Mac (RHEL x86_64 on
arm64), which is intrinsic and unchanged."

> **Reference — KVM vs TCG.** KVM (Kernel-based Virtual Machine) executes guest
> code natively on the CPU's hardware virtualization extensions; overhead is
> small and roughly constant. TCG (QEMU's software emulator) dynamically
> translates guest basic blocks to host code and caches the translations; it is a
> *full CPU emulator* and pays a per-instruction translation/dispatch cost. For
> compute-bound guest code, TCG is commonly an order of magnitude (or more) slower
> than native, and — crucially for this report — it turns work that is "free" on
> hardware (idle loops, timer ticks, lock spins, polling threads) into real host
> CPU, because the emulator must still execute and dispatch every one of those
> guest instructions.

---

## 3. Methodology

All measurements were **read-only** and taken from the libvirt host (no changes to
any guest, no lab mutation):

1. **Process attribution** — `virsh list`, and `ps`/`top` on the host to confirm
   *which host processes* hold the CPU and *which accelerator* each guest uses.
2. **Instantaneous per-domain CPU** — a short Python sampler reading
   `/proc/<pid>/stat` (`utime+stime`) for each `qemu-system-*` process twice, 4 s
   apart, converting the delta to "% of one host core" (a 2-vCPU guest can reach
   200%). This gives a true instantaneous reading, unlike `ps`'s lifetime average.
3. **Time-series** — direct queries to the lab's own Prometheus (`obs`,
   `10.50.0.2:9090`, the same data source behind the dashboards), using
   node_exporter's `node_cpu_seconds_total` to compute per-host busy % over the
   preceding 30 minutes, alongside the **hypervisor's own** node_exporter series.

The time-series step is what made the "drop" legible: it let us watch the guests
and the host move together, rather than guessing.

---

## 4. Evidence

### 4.1 The host CPU consumers are the x86 QEMU processes

`top` on the host (one sample), busiest first — every top consumer is a
`qemu-system-x86` (TCG) process; the host is CPU-bound (load 12.4 on 12 cores,
~23% idle, **not** swapping):

```
%Cpu(s): 64.2 us, 12.5 sy,  0.0 ni, 23.3 id ...
   PID   %CPU  COMMAND        guest
1309779  180   qemu-system-x86 (nha-rhel-a3)
1310113  160   qemu-system-x86 (nha-rhel-b1)
2015323  130   qemu-system-x86 (rdqm-a*)
1310208  120   qemu-system-x86 (nha-rhel-b2)
1310384  110   qemu-system-x86 (nha-rhel-a1)
   4635   20   claude          (this session)
1031164   20   qemu-system-aar (obs — native KVM, cheap)
```

### 4.2 Instantaneous per-domain CPU (4 s sample)

% of **one** host core; a 2-vCPU guest maxes at 200%:

```
 120.7%  rdqm-a2          <- running QM (RDQM/DRBD)
 120.7%  rdqm-a1          <- running QM
 120.7%  nha-rhel-a2      <- running QM (Native HA / raft)
 118.7%  rdqm-a3          <- running QM
 118.2%  nha-rhel-b3      <- running QM (CRR site)
 114.0%  nha-rhel-a1      <- running QM
 107.5%  nha-rhel-b2      <- running QM
  90.7%  nha-rhel-b1      <- running QM
  86.7%  nha-rhel-a3      <- running QM
  13.2%  rdqm-b2          <- NO running QM
  11.5%  rdqm-b1          <- NO running QM
  10.5%  rdqm-b3          <- NO running QM
  10.5%  obs              <- aarch64, native KVM
   1.7%  mon-probe
   1.0%  svc-sim
   0.2%  app-client
```

### 4.3 Controlled comparison — the discriminator is "a QM is running," not the HA tech

This is the cleanest result in the investigation. Hold the platform constant
(RHEL 9.6 **x86_64 TCG**, 2 vCPU) and vary only whether a queue manager is
running on the node:

| Group | HA mechanism | QM running? | CPU (host saturated) |
|---|---|---|---|
| `nha-rhel-a` (×3) | Native HA (raft) | yes — `QMNATIVE` active+replicas | ~78% |
| `nha-rhel-b` (×3) | Native HA (CRR/DR) | yes — recovery group + replication | ~78% |
| `rdqm-a` (×3) | **RDQM (DRBD)** | yes — RDQM HA group | **~100%** |
| `rdqm-b` (×3) | RDQM (DRBD) | **no** | **~4%** |
| `obs` (×1) | n/a (aarch64, KVM) | n/a | ~4% |

Two completely different HA stacks (raft vs DRBD) cost the same when a QM is
running, and the same RDQM platform costs essentially nothing when no QM is
running. **The HA technology is not the variable. "A running queue manager being
software-emulated" is the variable.**

Why an "idle" running QM is not actually idle: an IBM MQ queue manager keeps a
standing pool of processes/threads — the execution controller (`amqzxma0`), agent
and pubsub/housekeeping processes (`amqzlaa0`, `amqzmuc0`, `amqzmgr0`), channel
and listener threads — that wake on timers to do checkpoints, log housekeeping,
channel scans, and (for Native HA) **continuous raft leader heartbeats** between
the three instances. On bare metal or KVM these periodic wakeups are nearly free.
Under TCG, each wakeup is a burst of guest instructions that must be translated
and dispatched on the host, so "idle" still costs roughly **one host core per
running QM**.

### 4.4 The "uniform drop" was the host, not the queue managers

30 minutes of node_exporter CPU (group-averaged % busy per host), with the
**hypervisor's own** series in the first column:

```
time    HYPERV  nha-a  nha-b  rdqm-a  rdqm-b   obs
11:08      98     78     78     100      18      5
11:12      98     77     77     100       4      4
11:16      98     78     77      99       5      4
11:20      98     77     77      97       5      4
11:24      98     77     77     100       4      4
11:28      98     78     77     100       4      4
11:30      98     78     78     100       5      4   <- host pegged; QM nodes flat-topped
11:32      87     64     64      69       4      4
11:34      70     48     46      24       3      3   <- host eased; ALL QM nodes fall together
11:36      83     58     56      55       4      4
11:38      84     51     51      70       4      4
```

Read this carefully, because it is the heart of the "why did it drop" question:

- For ~25 minutes the hypervisor sat **flat-pinned at 98%** and every QM-running
  guest sat **flat at ~77–78% (NHA) / ~100% (RDQM-a)**. A *dead-flat* top is the
  signature of a **saturation ceiling**, not a workload — real MQ work fluctuates;
  a pinned host does not.
- At ~11:31 total host demand fell. The **hypervisor dropped to ~70–87%**, and
  **every QM-running guest dropped in proportion, in the same minute** — three
  *independent* clusters (raft site A, raft site B, DRBD RDQM-a) stepping down
  simultaneously.
- The genuinely-idle VMs (`rdqm-b`, `obs`) **never moved** (~4%).

Three independent clusters cannot coincidentally change workload in the same
minute. The only thing they share is the **host CPU pool**. They "all track each
other" — and tracked the hypervisor — because they are identical emulated VMs
splitting one saturated host. The drop happened *to* them (host contention eased),
not *because of* anything inside the Native HA raft.

**What we could not pin:** the specific host consumer that backed off at ~11:31.
There was no libvirt domain start/stop in the host journal for that window and
nothing was provisioning, so it was most likely a transient host process (a
build/test/cron, or scheduler rebalancing) finishing — not an MQ event. This is
recorded as an honest open item, not a guess dressed as a finding.

---

## 5. Analysis — what the number actually is

Putting the evidence together, the guest-reported CPU% on the x86 arms is a
**blend of two things a reader cannot separate from the graph**:

1. **Emulated guest work** — the real instructions the QM executes, multiplied by
   TCG's per-instruction translation/dispatch cost. Even at idle this is large
   because emulation turns "free" hardware behavior (timers, spins, polls) into
   executed-and-translated instructions.
2. **Host-contention / scheduling effects** — because all x86 guests compete for
   the same saturated host CPU pool, each guest's reported busy% is gated by how
   much host CPU it can get, which is why the number moves with the hypervisor.

The empirical proof that (2) dominates the *variation*: the **same idle queue
managers** read ~78% when the host was saturated and ~48% twenty minutes later
when the host had slack. Nothing changed inside the QMs. A metric that nearly
halves based on host contention, with the workload held constant, cannot be read
as a workload metric.

> **Data vs judgment.** *Data:* the process attribution (§4.1), the per-domain
> instantaneous CPU (§4.2), the NHA-vs-RDQM controlled comparison (§4.3), and the
> guest↔hypervisor lockstep time-series (§4.4) are all directly measured. *Judgment:*
> the interpretation that the reading is "not a meaningful workload metric," and
> the projection of how much native KVM would change it (§6), is reasoning on top
> of that data, presented as such.

---

## 6. What changes on native x86 (the payoff #276/#283 are after)

On an x86_64 Linux host with KVM, the RHEL guests are **same-architecture** and
run under `-accel kvm` — exactly like `obs` (aarch64) does today, the one VM in
this lab that is *already* native and sits at ~4%. The entire TCG translation
layer disappears:

- An idle running queue manager stops costing ~1 host core; it costs what an idle
  QM costs on real hardware (near-nothing).
- The host stops being the bottleneck, so guests stop contending and stop
  tracking each other — CPU graphs start reflecting **MQ behavior** instead of
  **host saturation**.
- Performance and timing measurements become defensible: you can put load through
  a QM and read the resulting CPU/latency as a real signal.

This is also why running the **Vergil session itself on a remote x86 cloud VM** is
attractive: it would let this lab run entirely on native KVM for the QM arms,
removing the emulation tax at its root rather than working around it. (Captured
separately as a Vergil VM / vergil-tooling capability — see the follow-up.)

The arm64 Mac remains the right **development** environment (fast local iteration,
functional correctness, "do no harm on macOS" — #276 Tier-1). The split is:
**Mac/arm64 for dev + functional correctness; native x86 for any quantitative
result.**

---

## 7. Limitations & open items

- **The exact 11:31 host-load trigger is unidentified** (§4.4) — host journal
  showed no domain lifecycle event; most likely a transient host process. Not
  load-bearing for the conclusion (the conclusion rests on the guest↔hypervisor
  correlation, which holds regardless of the trigger).
- **We did not enter a guest.** The thread-level attribution inside the QM
  (`amqz*` processes) is inferred from MQ architecture, not captured here, to keep
  the investigation strictly host-side and read-only. A follow-up read-only `top`
  inside one node would add color but does not change the conclusion.
- **Absolute TCG-vs-native ratios are not claimed numerically.** This report
  establishes the *qualitative* result (the number is contention-gated emulation
  overhead, not workload) from direct measurement; the precise native-vs-emulated
  factor is exactly what #283's x86 acceptance run will measure.

---

## 8. Reproduction

All commands are read-only and run from the libvirt host.

```sh
# 1. Which guests run, and on which accelerator (TCG = emulated, KVM = native)
virsh -c qemu:///system list
ps -eo args | grep -m1 'guest=lab_nha-rhel-a1,' | grep -o -- '-accel [a-z]* -cpu [a-z]*'   # -> tcg
ps -eo args | grep -m1 'guest=lab_obs,'         | grep -o -- '-accel [a-z]* -cpu [a-z]*'   # -> kvm

# 2. Instantaneous per-domain CPU (sample /proc/<pid>/stat for each qemu twice).
#    See the sampler used for this report (utime+stime delta over 4 s, per guest).

# 3. Time-series from the lab's own Prometheus (the dashboard data source):
#    busy% per host, including the hypervisor, over the last 30 min:
#    100*(1 - avg by(host)(rate(node_cpu_seconds_total{mode="idle"}[2m])))
#    queried at http://10.50.0.2:9090/api/v1/query_range
```

---

## 9. Cross-references

- **#276** — Make the lab host-arch-aware: run natively on x86 Linux, keep arm64
  Mac as dev. *This report is its quantified justification.*
- **#283** — Acquire out-of-Vergil x86 acceptance environments. *This report
  motivates the priority; #283's first x86 run will produce the native-vs-emulated
  numbers this report deliberately does not claim.*
- **#103 / #279 / #285** — the observability stack whose CPU graphs this report
  shows must be read with the TCG caveat on the x86 arms.
- `docs/reports/2026-06-18-nativeha-rhel-phase1-findings.md` — the Phase-1
  findings that (correctly) scoped themselves to "functional correctness only
  (TCG — no timing claims)." This report is the evidence behind that disclaimer.
