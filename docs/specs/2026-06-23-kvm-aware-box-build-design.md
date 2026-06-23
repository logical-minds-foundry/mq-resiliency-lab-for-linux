# KVM-aware RHEL box build — gate the build domain on host architecture

- **Issue:** #327
- **Date:** 2026-06-23
- **Status:** design (brainstormed; pending implementation plan)
- **Corrects an assumption in:** `docs/specs/2026-06-18-x86-host-portability-design.md`
  (#276). That spec listed "the RHEL box builder" as *already arch-neutral, no
  change needed* (§1.1). That is true of the build **output** (the resulting
  `.box` is arch-independent), but not of the build **path**: the transient build
  domain is hardcoded to TCG, so the build forfeits native KVM on an x86 host —
  the exact thing #276 set out to gain.
- **Builds on (does not duplicate):** the resolver in `src/mqlab/platforms.py`
  (#276), which is the single authority for the host-arch virtualization matrix
  (#276 §4.2, D2). This spec reuses that matrix rule for one more consumer — the
  box-build domain — rather than adding a second copy.

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
- `lab/boxes/rhel96/build-box.sh:112` — the message asserting TCG + 45–90 min,
  printed regardless of host.
- `src/mqlab/cli.py:692–694` — a comment asserting the build is "~45-90min ... on
  a truly first-ever run", with no KVM qualifier.

## 2. Decisions

Settled during brainstorming (2026-06-23):

- **D1 — Reuse the `platforms.py` authority; do not duplicate the matrix rule.**
  The box-build domain's KVM-vs-TCG decision is computed by a pure function in
  `platforms.py`, using the same predicate as `_provider`: KVM iff the guest arch
  equals the host arch and `/dev/kvm` is usable. The box build's guest is always
  x86_64, so the predicate reduces to `facts.arch == X86_64 and facts.kvm`.
- **D2 — `platforms.py` decides; the `.tpl` renders.** The new function returns
  `(domain_type, cpu_mode)`. The XML template stays the human-readable structural
  authority and gains `@DOMAIN_TYPE@`/`@CPU_MODE@` placeholders alongside the
  existing `@ISO@`. mqlab owns the matrix bits; the template owns the domain shape.
- **D3 — No fabricated KVM duration.** The KVM path has never run (the build has
  been TCG-pinned), so no measured KVM build time exists. The KVM message states
  it is native and faster than the TCG path, without an invented figure.
- **D4 — Fail loud, no silent fallback.** If the decision command errors or returns
  unexpected tokens, `build-box.sh` aborts and names the cause. It does not default
  to a domain type — a silent default would mask a broken authority.

### 2.1 Non-goals

- Changing running-lab guest resolution (already correct via #276).
- Rewriting historical `docs/plans/*` that quote the old message (history, not
  live behaviour).
- Measuring and recording an exact KVM build duration (a future follow-up may
  observe one and add it).
- Changing the `build-box.sh` cache/REUSE logic, ISO staging, or cleanup.

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

`cpu_mode = maximum` on the TCG row is required, not cosmetic: EL9 needs
x86-64-v2; sub-v2 models hang early userspace (#24). `host-passthrough` is the
KVM-row mode used everywhere else (`platforms.CPU_KVM`).

## 4. Components & data flow

```
hostfacts.probe() ─► platforms.build_domain_virt(facts) ─► (domain_type, cpu_mode)
                                  │
        mqlab vm build-virt ◄─────┘  (prints the two tokens)
                  │
build-box.sh reads tokens ─► sed @DOMAIN_TYPE@/@CPU_MODE@/@ISO@ ─► domain.xml ─► virsh define/start
                  └─► message branch (KVM | TCG)
```

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `platforms.build_domain_virt(facts)` | Pure `(domain_type, cpu_mode)` for the x86_64 box build | `hostfacts` types, `CPU_KVM`/`CPU_TCG` (pure) |
| `mqlab vm build-virt` | Probe live facts, print `<domain_type> <cpu_mode>` | `platforms.build_domain_virt`, `hostfacts.probe` |
| `build-domain.xml.tpl` | Build-domain XML shape with `@DOMAIN_TYPE@`/`@CPU_MODE@`/`@ISO@` placeholders | — |
| `build-box.sh` | Read decision, substitute, define/start domain, branch the message | `mqlab vm build-virt`, the template |
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

### 4.2 CLI surface

A `vm build-virt` command on the existing `vm_app` Typer group: probes facts and
prints the two tokens on one line (e.g. `kvm host-passthrough`). `build-box.sh`
consumes it as `read DOMAIN_TYPE CPU_MODE < <(mqlab vm build-virt)`. The command
is a thin veneer over the pure function, matching the repo's CLI idiom.

### 4.3 `build-box.sh` changes

- Resolve the decision before defining the domain; abort loudly if the command
  fails or the tokens are empty/unexpected (D4).
- Extend the existing `sed` (currently `@ISO@` only) to also substitute
  `@DOMAIN_TYPE@` and `@CPU_MODE@`.
- Branch the install message on `DOMAIN_TYPE`:
  - `kvm` → `installing (KVM — native virtualization, much faster than the TCG path); waiting for shut off...`
  - `qemu` → the existing `installing (TCG, expect 45-90 min); waiting for shut off...`
- Update the header comment (lines 5, 68) to qualify the duration as TCG-only.

A `type='kvm'` domain whose host lacks usable `/dev/kvm` fails at `virsh start`,
caught by the script's `set -e` — loud by construction, no extra guard needed.

## 5. Error handling

Fail loud throughout, matching the repo idiom:

- decision command errors / empty tokens → `build-box.sh` aborts, naming the
  failed `mqlab vm build-virt` call (D4);
- a KVM domain that cannot acquire `/dev/kvm` → `virsh start` fails under `set -e`;
- `build_domain_virt` itself never raises (display-safe), so `vm status`-style
  read paths that might call it stay diagnostic.

## 6. Testing & acceptance

- **Unit (`tests/test_platforms.py`)** — `build_domain_virt` across the matrix
  using the existing synthetic `HostFacts` fixtures: `X86_KVM → ("kvm",
  "host-passthrough")`, `X86_NOKVM → ("qemu", "maximum")`, `ARM_KVM → ("qemu",
  "maximum")`. Reachable under the repo's 100%-branch-coverage gate.
- **CLI** — `vm build-virt` prints the expected tokens for an injected/mock
  `probe()`, following the existing `test_cli*` mocking pattern.
- **Template/wiring** — assert `build-domain.xml.tpl` carries the three
  placeholders and that a KVM and a TCG substitution each render a well-formed
  `type='kvm'`/`type='qemu'` domain, aligned with the existing `conftest`
  build-box dry-run harness.
- **Acceptance (two tiers, mirroring #276 §9):**
  - **Tier 1 (blocking, now)** — full unit matrix green; a resolver dry-run proving
    an x86 host *would* select `kvm`/`host-passthrough`; and a no-harm check that
    the arm64-Mac path still resolves TCG and builds the box unchanged.
  - **Tier 2 (blocks "done")** — a real KVM box build on the native-x86 cloud host
    completes and produces a working `.box`. This is also the opportunity to
    observe a real KVM build duration (D3 follow-up), if we choose to record one.

## 7. Open items for the implementation plan

- Final command name/spelling under `vm` (`build-virt` proposed) and its exact
  output format (single line, space-separated proposed).
- Whether `build_domain_virt` lives directly in `platforms.py` or a tiny sibling;
  default is `platforms.py` next to `_provider`, sharing `CPU_KVM`/`CPU_TCG`.
- Confirm the build-box dry-run test harness can assert template substitution
  without a live `virsh` (it currently shells `build-box.sh --dry-run`).
