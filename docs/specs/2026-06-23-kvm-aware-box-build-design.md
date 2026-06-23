# KVM-aware RHEL box build — gate the build domain on host architecture

- **Issue:** #327
- **Date:** 2026-06-23
- **Status:** design (brainstormed; pushback-reviewed; pending implementation plan)
- **Corrects an assumption in:** `docs/specs/2026-06-18-x86-host-portability-design.md`
  (#276). That spec listed "the RHEL box builder" as *already arch-neutral, no
  change needed* (§1.1). That is true of the build **output** (the resulting
  `.box` is arch-independent), but not of the build **path**: the transient build
  domain is hardcoded to TCG, so the build forfeits native KVM on an x86 host —
  the exact thing #276 set out to gain. #276 was implemented in #296
  (`feat(arch): gate lab virtualization on host architecture`), which created
  `platforms.py` and wired `vm create` to invoke `build-box.sh`, but left the
  build domain TCG-pinned.
- **Builds on (does not duplicate):** the resolver in `src/mqlab/platforms.py`
  (#276/#296), which is the single authority for the host-arch virtualization
  matrix (#276 §4.2, D2). This spec reuses that matrix rule for one more consumer
  — the box-build domain — rather than adding a second copy.

## 1. Problem

`lab/boxes/rhel96/build-domain.xml.tpl` hardcodes `<domain type='qemu'>` with
`<cpu mode='maximum'/>` — TCG software emulation, unconditionally. The transient
RHEL 9.6 build domain therefore runs emulated on **every** host, including the
native-x86 cloud host, where it genuinely takes ~45–90 min.

The `"installing (TCG, expect 45-90 min)"` message in `build-box.sh:112` is, as a
result, **accurate, not stale**. The deception the operator perceives is real but
the message is not its source: the build is *actually* slow because the domain is
pinned to TCG. Just rewording the message would make it lie in the other
direction. The defect is that the box build bypasses the `platforms.py` authority
that every running lab guest already uses to resolve KVM-vs-TCG correctly.

The payoff named in #276 — "on x86, the entire fleet runs native KVM ... nothing
is emulated, which is the whole reason to support x86" — is not yet realised for
the one-time box build.

### 1.1 Where the assumption lives

- `lab/boxes/rhel96/build-domain.xml.tpl:5` — `<domain type='qemu'>` (TCG).
- `lab/boxes/rhel96/build-domain.xml.tpl:15` — `<cpu mode='maximum'/>` (the TCG
  full-emulation CPU model, #24).
- `lab/boxes/rhel96/build-box.sh:103` — the `sed` substitution, currently `@ISO@`
  only.
- `lab/boxes/rhel96/build-box.sh:112` — the message asserting TCG + 45–90 min,
  printed regardless of host.
- `src/mqlab/cli.py:692–694` — a comment asserting the build is "~45-90min ... on
  a truly first-ever run", with no KVM qualifier.

## 2. Decisions

Settled during brainstorming and the pushback review (2026-06-23):

- **D1 — Reuse the `platforms.py` authority; do not duplicate the matrix rule.**
  The box-build domain's KVM-vs-TCG decision is computed by a pure function in
  `platforms.py`, using the same predicate as `_provider`: KVM iff the guest arch
  equals the host arch and `/dev/kvm` is usable. The box build's guest is always
  x86_64, so the predicate reduces to `facts.arch == X86_64 and facts.kvm`.
  Critically, `build-box.sh` does **not** re-derive this rule in bash — a second
  copy would drift from `platforms.py`, which is precisely what this decision
  exists to prevent.
- **D2 — `platforms.py` decides; the `.tpl` renders.** The new function returns
  `(domain_type, cpu_mode)`. The XML template stays the human-readable structural
  authority and gains `@DOMAIN_TYPE@`/`@CPU_MODE@` placeholders alongside the
  existing `@ISO@`. mqlab owns the matrix bits; the template owns the domain shape.
- **D3 — The decision reaches `build-box.sh` as required CLI arguments set by the
  orchestrator — one interface for human and harness alike.** `mqlab` computes the
  decision and invokes `build-box.sh --domain-type <t> --cpu-mode <m>`. The script
  treats both as **required**: if either is missing or not one of the accepted
  values, it exits non-zero with a usage message naming exactly what to pass. This
  was chosen over two rejected alternatives surfaced in review:
  - *build-box.sh calls back into `mqlab`* — rejected as an inversion: `mqlab vm
    create → bash build-box.sh → mqlab vm build-virt` is a recursive call that
    re-probes the host and couples the script to the CLI being on `PATH`.
  - *build-box.sh defaults the values when unset (silent fallback to TCG)* —
    rejected because it makes a hand-run behave differently from a harness-run.
    The script must not have special-cased "magic" behavior depending on its
    caller. A missing argument is an error the human is told how to fix, not a
    silent degrade.
- **D4 — No fabricated KVM duration.** The KVM path has never run (the build has
  been TCG-pinned), so no measured KVM build time exists. The KVM message states
  it is native and faster than the TCG path, without an invented figure.
- **D5 — Fail loud, no silent fallback.** Missing/invalid args → usage message +
  non-zero exit (D3). A `type='kvm'` domain on a host without usable `/dev/kvm`
  fails at `virsh start`, caught by the script's `set -e`. The pure decision
  function itself never raises (display-safe), mirroring `platforms.resolve`.

### 2.1 Non-goals

- Changing running-lab guest resolution (already correct via #276/#296).
- Rewriting historical `docs/plans/*` that quote the old message (history, not
  live behaviour).
- Measuring and recording an exact KVM build duration (a future follow-up may
  observe one and add it — see §6 Tier 2).
- Changing the `build-box.sh` cache/REUSE logic, ISO staging, network setup
  (#324), retryability teardown (#326), or cleanup.
- Re-architecting the raw-virsh RHEL box build. Its specialness (a hand-rolled
  transient domain outside the Vagrant/resolver path) is acknowledged; the goal
  here is the smallest correct change to make it KVM-aware. Unifying it with the
  resolver path, or moving its remaining bash logic into testable Python, is an
  explicit candidate for a later refactor, out of scope now.

## 3. The rule

The box-build guest is fixed at x86_64. Applying #276 §4.2's one-line rule
(`driver = kvm` iff `guest_arch == host_arch` and KVM usable; else TCG):

| host arch | `/dev/kvm` | domain type | cpu mode          | when                         |
|-----------|------------|-------------|-------------------|------------------------------|
| x86_64    | usable     | `kvm`       | `host-passthrough`| native-x86 host (cloud) — KVM |
| x86_64    | absent     | `qemu`      | `maximum`         | x86 host w/o KVM (defensive)* |
| aarch64   | n/a        | `qemu`      | `maximum`         | arm64 Mac — x86 guest foreign |

\* Per #276 D3, the lab refuses to run on its native arch without usable KVM
(`require_native_kvm`), so the gated bring-up never reaches the box build on an
x86 host lacking `/dev/kvm`. The function still returns TCG there so it is honest
and display-safe when invoked standalone — it never raises, mirroring
`platforms.resolve`.

`cpu_mode = maximum` on the TCG rows is required, not cosmetic: EL9 needs
x86-64-v2; sub-v2 models hang early userspace (#24). `host-passthrough` is the
KVM-row mode used everywhere else (`platforms.CPU_KVM`).

**Only `type` and `cpu mode` are host-dependent.** Everything else in the build
domain is unchanged and is *not* parameterized:

- `<emulator>/usr/bin/qemu-system-x86_64</emulator>` is correct for **both** paths.
  On x86 KVM, libvirt drives that same binary with KVM acceleration — switching to
  `type='kvm'` is what engages `/dev/kvm`; the emulator binary does not change.
  (There is no separate "kvm" emulator binary; `qemu-system-x86_64` is it.)
- `machine='q35'`, `<features><acpi/></features>`, the 4 vCPU / 4096 MiB sizing,
  the virtio disk + SATA cdroms, the OEMDRV kickstart volume, and the
  `on_poweroff`/`on_reboot=destroy` install-loop guard all stay as-is. An
  implementer should change exactly two attributes and nothing else.

## 4. Components & data flow

```
mqlab vm create
  └─ _ensure_local_boxes(guests)
       └─ _box_build_steps(...)                 # orchestrator (Python)
            ├─ facts = probe()                   # host facts
            ├─ (dtype, cpu) = platforms.build_domain_virt(facts)   # the authority
            └─ CommandStep("box rhel/9.6-x86_64",
                 Command(["bash", build-box.sh,
                          "--domain-type", dtype, "--cpu-mode", cpu]))
                   │
                   ▼
            build-box.sh   (thin consumer)
              ├─ parse + validate args (usage-die if missing/invalid)   # D3/D5
              ├─ sed @ISO@/@DOMAIN_TYPE@/@CPU_MODE@ → domain.xml
              ├─ message branch on --domain-type (KVM | TCG)            # D4
              └─ virsh define/start; wait for shut off
```

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `platforms.build_domain_virt(facts)` | Pure `(domain_type, cpu_mode)` for the x86_64 box build | `hostfacts` types, `CPU_KVM`/`CPU_TCG` (pure) |
| `_box_build_steps` / `_ensure_local_boxes` (`cli.py`) | Compute the decision from probed facts; pass it as args on the `build-box.sh` `Command` | `platforms.build_domain_virt`, `hostfacts.probe` |
| `build-domain.xml.tpl` | Build-domain XML shape with `@DOMAIN_TYPE@`/`@CPU_MODE@`/`@ISO@` placeholders | — |
| `build-box.sh` | Parse + validate the two args, substitute, define/start domain, branch the message | its CLI args, the template |
| `cli.py:692–694` comment | Accurate duration note (TCG vs KVM) | — |

### 4.1 `platforms.build_domain_virt`

```python
def build_domain_virt(facts: HostFacts) -> tuple[str, str]:
    """(domain_type, cpu_mode) for the local x86_64 RHEL box build.

    KVM when the host natively virtualizes x86_64; TCG otherwise (foreign-arch
    arm64 Mac, or an x86 host without usable /dev/kvm). Pure and display-safe —
    never raises — mirroring resolve()."""
    kvm = facts.arch == X86_64 and facts.kvm
    return ("kvm", CPU_KVM) if kvm else ("qemu", CPU_TCG)
```

Lives in `platforms.py` next to `_provider`, sharing `X86_64`, `CPU_KVM`, and
`CPU_TCG` so there is one definition of the matrix constants.

### 4.2 Orchestrator integration (`cli.py`)

`_box_build_steps` (reached via `_ensure_local_boxes`, exercised by `vm create`)
gains the decision and threads it into the `Command`:

- Obtain `facts`. The bring-up path already probes (`vm create` → `ensure_resolved`
  at `cli.py:215`); `_box_build_steps`/`_ensure_local_boxes` take `facts` as an
  **injected parameter** (default `probe()`) so the value is deterministic in
  tests, the same facts-threading pattern #276 §5.1 used for `lab_guests` /
  `setup_platforms`.
- Compute `(dtype, cpu) = platforms.build_domain_virt(facts)`.
- Append `--domain-type <dtype> --cpu-mode <cpu>` to the existing
  `["bash", <build-box.sh>]` argv.

No new CLI subcommand is added. (An earlier draft proposed `mqlab vm build-virt`
for `build-box.sh` to call back into; D3 removed that inversion, so the command is
dropped — YAGNI.)

### 4.3 `build-box.sh` changes

- **Argument parsing.** Extend the existing `for arg in "$@"` loop (which already
  handles `--rebuild-box`/`--dry-run` and rejects unknown args) to accept
  `--domain-type <kvm|qemu>` and `--cpu-mode <host-passthrough|maximum>`. Both are
  **required**. On a missing or unrecognized value, print a usage message —
  naming the flags and that `mqlab` normally supplies them — and exit non-zero
  (D3/D5). This runs before any expensive work, including the `--dry-run` report,
  so `--dry-run` also surfaces a missing-arg error.
- **Substitution.** Extend the `sed` at line 103 to substitute `@DOMAIN_TYPE@` and
  `@CPU_MODE@` in addition to `@ISO@`.
- **Message branch.** Replace the unconditional line 112 with a branch on the
  domain type:
  - `kvm` → `installing (KVM — native virtualization, much faster than the TCG path); waiting for shut off...`
  - `qemu` → the existing `installing (TCG, expect 45-90 min); waiting for shut off...`
- **Comments.** Update the header (lines 5, 68) so the duration is qualified as
  TCG-only, not stated as the universal cost.

A `type='kvm'` domain whose host lacks usable `/dev/kvm` fails at `virsh start`,
caught by `set -e` — loud by construction, no extra guard needed.

## 5. Error handling

Fail loud throughout, matching the repo idiom:

- missing/invalid `--domain-type`/`--cpu-mode` → usage message + non-zero exit,
  before any side effects (D3/D5);
- a KVM domain that cannot acquire `/dev/kvm` → `virsh start` fails under `set -e`;
- `build_domain_virt` itself never raises (display-safe), so any read path that
  calls it stays diagnostic.

## 6. Testing & acceptance

The decision and its wiring move into Python, where the suite reaches them; the
bash arg-handling/substitution/message is the only part not unit-covered, by the
deliberate choice in §2.1 (acceptable for now, refactor candidate later).

- **Unit — `tests/test_platforms.py`.** `build_domain_virt` across the matrix
  using the existing synthetic `HostFacts` fixtures: `X86_KVM → ("kvm",
  "host-passthrough")`, `X86_NOKVM → ("qemu", "maximum")`, `ARM_KVM → ("qemu",
  "maximum")`. Reachable under the repo's 100%-branch-coverage gate.
- **Unit — `tests/test_cli_vm.py`.** The orchestrator's arg-passing is testable
  through the existing `RecordingRunner` seam:
  `test_ensure_local_boxes_builds_missing` already asserts
  `argvs[1] == ["bash", <build-box.sh>]`. Extend/parallel it to assert the argv
  carries `--domain-type kvm --cpu-mode host-passthrough` for injected `X86_KVM`
  facts, and `--domain-type qemu --cpu-mode maximum` for `ARM_KVM`. (Facts must be
  injected for determinism — CI runs on x86 runners.)
- **Not unit-tested (by §2.1 decision).** `build-box.sh` arg-parsing, the usage-die
  path, the `sed` substitution, and the message branch. `conftest.py`
  autouse-neutralizes `_ensure_local_boxes`, so `build-box.sh` is never shelled in
  the unit suite, and the `--dry-run` path is not a seam for the substitution
  (which lives in the expensive BUILD path). These are covered by Tier-2
  acceptance. (A cheap optional `bats`-style or `bash -n`/shell unit for just the
  usage-die path is noted as an open item, not required.)
- **Acceptance (two tiers, mirroring #276 §9):**
  - **Tier 1 (blocking, now)** — full unit matrix green; the arg-passing assertions
    above; a no-harm check that the arm64-Mac path still resolves TCG args and
    builds the box unchanged.
  - **Tier 2 (blocks "done")** — a real KVM box build on the native-x86 cloud host:
    `mqlab` passes `--domain-type kvm`, the build completes and produces a working
    `.box`, and a hand-run of `build-box.sh` with no args prints the usage message
    and exits non-zero. This is also the opportunity to observe a real KVM build
    duration (D4 follow-up), if we choose to record one.

## 7. Open items for the implementation plan

- Final flag spelling (`--domain-type`/`--cpu-mode` proposed) and the accepted
  value sets (`kvm|qemu`, `host-passthrough|maximum`).
- Confirm `facts` injection threads cleanly into `_box_build_steps` /
  `_ensure_local_boxes` (default `probe()`), without disturbing the #276 facts
  flow already in `vm create`.
- Decide whether to add the cheap standalone usage-die shell test (§6), or leave
  the bash side entirely to Tier-2.
