# x86 host portability — gate virtualization on host architecture

- **Issue:** #276
- **Date:** 2026-06-18
- **Status:** design (brainstormed; pending implementation plan)
- **Supersedes assumptions in:** `docs/reports/2026-06-06-phase-a-provider-spike.md`
  (the arm64-host provider mechanics it established remain correct; this spec
  generalises the *selection* of those mechanics to the actual host arch).

## 1. Problem

The lab was built entirely on Apple Silicon (arm64): an x86 Mac laptop running a
Lima/Vergil base VM, with nested libvirt guests inside it. Several layers silently
assume the host is ARM. The goal is to clone this repo onto an **x86 Linux host**
(bare-metal or VM) and get the same lab behaviour, with the virtualization and
emulation decisions gated on the *real* host architecture rather than a baked-in
ARM assumption.

The concrete payoff: on x86, the entire fleet runs **native KVM**. Today, RHEL
x86_64 guests run under TCG emulation on the arm64 Mac — functional but slow. On
the x86 target nothing is emulated, which is the whole reason to support x86.

### 1.1 Where the ARM assumption lives today

1. **Virtualization / accel selection** — `lab/Vagrantfile:34-54` hardcodes
   `aarch64 → kvm` and `x86_64 → qemu`(TCG). On an x86 host this is exactly
   backwards: x86_64 should be KVM (native) and there should be no TCG at all.
2. **Default guest arch** — `lab/topology.yaml:21` (`defaults.platform:
   ubuntu2404-arm64`) and `src/mqlab/fleet.py:19` (`DEFAULT_PLATFORM`) default
   every non-RHEL node to arm64.
3. **MQ artifacts** — `scripts/fetch-mq.sh` and `ansible/roles/mq-install/tasks/
   main.yml` are pinned to the `UbuntuLinuxARM64` tarball / arm64 `.deb` path.

Already arch-neutral (no change needed): the observability roles
(Prometheus/Loki/Alloy/node-exporter) all branch on `ansible_architecture`; the
topology box registry; the RHEL box builder.

## 2. Decisions

These were settled during brainstorming (2026-06-18) and bound the design:

- **D1 — Guest arch tracks the host (native-preferred).** Ubuntu guests resolve to
  x86_64 on an x86 host and arm64 on the Mac. RHEL stays x86_64 always (it is
  x86_64-only in this lab). On x86, the whole lab is native KVM.
- **D2 — A single resolver in mqlab (Python) is the authority.** Given host facts
  and the topology, it resolves each node to a concrete provider config. The
  Vagrantfile, inventory, fleet status, and MQ fetch all consume that output. No
  arch decision is duplicated in Ruby.
- **D3 — Native-arch KVM is a hard requirement.** If the host's *own* architecture
  has no usable `/dev/kvm`, the lab refuses to run. There is **no TCG opt-in** for
  the native arch. The only remaining TCG is foreign-arch guests on the dev Mac
  (RHEL x86_64 on arm64) — intrinsic, unavoidable, and unchanged.
- **D4 — arm64-on-x86 is unreachable and guarded.** No arch-pinned arm64 platform
  exists, so the only arm64 platform (logical `ubuntu2404`) can never resolve to
  arm64 on an x86 host. If that combination is ever requested, preflight fails
  loud — we do not emulate ARM on x86.
- **D5 — Preflight diagnoses, it does not install.** Outside Vergil, a sanity-check
  verifies host prerequisites and *suggests* distro install commands. It never
  runs them; a `--fix` that installs is explicitly deferred.

### 2.1 Non-goals

- Automated provisioning of a bare host into a lab host (suggestions only).
- A `--fix`/`--install` mode (future work).
- Bare-metal ARM as a deployment target. ARM is the dev environment only; nobody
  is expected to run this lab on an ARM server. The detection is generic and would
  tolerate it, but it is not a design target.
- Publishing the repo externally. Kept in mind (no Vergil-only assumptions baked
  into the host path), but not a deliverable here.

## 3. Acceptance criterion

**mqlab behaves identically on ARM and x86.** Architecture is the only thing that
may ever differ between the two, and ideally even that is invisible to the
operator. Any behavioural difference that is *not* explained by architecture is a
defect.

## 4. The resolution model

### 4.1 Host facts (`src/mqlab/hostfacts.py`)

The single I/O boundary that probes and normalises the host. A small frozen
dataclass plus a `probe()` that fills it:

- `arch` — `platform.machine()` folded to canonical `aarch64` / `x86_64`
  (`arm64 → aarch64`, `amd64 → x86_64`). Unknown values raise loudly.
- `kvm` — `/dev/kvm` exists and is readable+writable.
- `distro_family` — from `/etc/os-release` `ID`/`ID_LIKE`: `apt` (ubuntu/debian) or
  `dnf` (rhel/almalinux/fedora). Used only for preflight install suggestions.
- `in_vergil` — heuristic marker that we are inside the managed Vergil base VM.
  **The canonical marker is to be confirmed during planning** — candidates are a
  Vergil sentinel file or a `vrg-*` wrapper on `PATH`. We will not guess one in
  code; the spike picks a real, documented signal.

`probe()` is the only function that touches the real host. Everything else takes a
`HostFacts` value as input, so the full matrix is unit-testable with no hardware.

### 4.2 The resolver (`src/mqlab/platforms.py`)

Pure function `resolve(topology, facts) -> dict[str, ResolvedNode]`. This is the
authority named in D2. `ResolvedNode` carries every field the Vagrantfile needs:
`box, arch, driver, loader, nvram, cpu_mode, input, boot_timeout, extra_disk,
dvd, nics`.

**Logical platforms** replace hardcoded-arch ones in the topology box registry:

- `ubuntu2404` (logical) → the arm64 Ubuntu box on an arm64 host, the x86_64 Ubuntu
  box on an x86_64 host. `defaults.platform` and `fleet.DEFAULT_PLATFORM` become
  `ubuntu2404`.
- `rhel96-x86_64` stays pinned x86_64 (unchanged).

The box registry gains the x86_64 Ubuntu box entry. **The exact box name and the
matching x86_64 Ubuntu MQ deb tarball name are to be verified against their
sources during planning — they will not be assumed here.**

**The resolution matrix** — every provider field is a function of
`(guest_arch, host_arch, kvm)`:

| guest arch | host arch | KVM? | driver      | firmware            | cpu_mode          | boot_timeout | when                          |
|------------|-----------|------|-------------|---------------------|-------------------|--------------|-------------------------------|
| aarch64    | aarch64   | yes  | `kvm`       | AAVMF (loader+nvram)| host-passthrough  | default      | Mac dev — Ubuntu guests       |
| x86_64     | x86_64    | yes  | `kvm`       | OVMF / default q35  | host-passthrough  | default      | x86 target — everything       |
| x86_64     | aarch64   | n/a  | `qemu` (TCG)| default q35         | `maximum` (#24)   | 1800         | Mac dev — RHEL guests         |
| aarch64    | x86_64    | —    | *unsupported* | —                 | —                 | —            | guarded: preflight fails loud |

The rule in one line: **`driver = kvm` iff `guest_arch == host_arch` and KVM is
usable; otherwise TCG.** Today's arm64-host behaviour (Ubuntu KVM, RHEL TCG) is
rows 1 + 3; an x86 host is rows 1 + 2 (all native). Row 4 is unreachable by
construction (D4) and exists only as a loud guard.

`cpu_mode = maximum` on the x86_64/TCG row is required, not an optimisation: EL9
needs x86-64-v2; sub-v2 models boot the kernel then hang early userspace
(diagnosed in #24).

### 4.3 Handoff to the Vagrantfile

Ruby cannot import the Python resolver, so mqlab renders
`build/lab/topology.resolved.yaml` — the same pattern as `inventory.py` rendering
`build/inventory.ini`. The Vagrantfile loads the resolved file and applies each
node's fields verbatim; its `case platform.arch` block is **deleted**. If the
resolved file is absent or stale, the Vagrantfile fails loud, naming the mqlab
command that produces it. `mqlab vm create` / `vm up` render it as a pre-step, so
the common path never sees the error.

## 5. Components & data flow

```
hostfacts.probe() ─┐
                   ├─► platforms.resolve(topo, facts) ─► ResolvedNode per guest
topology.yaml ─────┘                                      │
                                                          ├─► render build/lab/topology.resolved.yaml ─► Vagrantfile (dumb consumer)
                                                          ├─► fleet status (shows resolved arch)
                                                          └─► fetch-mq target set (which OS × arch artifacts)
```

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `hostfacts.py` | Probe + normalise host (arch, kvm, distro, in-Vergil) | OS / filesystem |
| `platforms.py` | Pure resolver: `(topo, facts) → ResolvedNode` | nothing (pure) |
| resolved-topology renderer | Write `build/lab/topology.resolved.yaml` | `platforms`, `hostfacts`, `paths` |
| `doctor` (preflight) | Checklist + suggestions; exit non-zero on hard fail | `hostfacts`, tool/artifact probes |
| `Vagrantfile` | Apply resolved fields; fail loud if file missing | resolved file |
| `fetch-mq.sh` / `mq-install` | Arch-correct MQ artifact | resolved arch / `ansible_architecture` |
| `topology.yaml` | Logical platform + x86_64 Ubuntu box | — |

## 6. Preflight — `mqlab doctor`

A new command, also run automatically as the first step of `vm create` / `vm up`.

1. **In Vergil?** → report "Vergil-managed; prerequisites guaranteed by the
   `[vm.vergil-user]` profile" and pass. The check trusts the profile.
2. **Outside Vergil** → checklist against probed facts:
   - **Native KVM** usable for the host arch — **hard fail** if absent (D3). No
     override.
   - **Required tools**: `qemu-system-x86_64` (plus `qemu-system-aarch64` only on
     an arm64 host), `libvirtd`/`virsh`, `vagrant` + the `vagrant-libvirt` plugin,
     `ansible`, `genisoimage`, the Python/uv runtime.
   - **Artifact prerequisites** for the *targeted* setup: the right MQ tarball(s)
     present-or-fetchable; the RHEL box + DVD ISO present if a RHEL setup is
     requested.
   - **Guard**: arm64-on-x86 requested → hard fail with the explicit message (D4).
3. **Every miss** prints a suggested install command for the detected distro
   family — suggestion only, never executed (D5).
4. Exit non-zero on any hard failure; lifecycle verbs refuse to proceed.

The "Lima / nerdctl" layer is explicitly *not* a host prerequisite: those are the
macOS→VM mechanism Vergil uses to create the base VM, invisible from inside the
Linux host. What the preflight checks is the Linux-host tool/package set that the
Vergil profile would otherwise guarantee.

## 7. Artifacts (MQ)

- `scripts/fetch-mq.sh` — fetch the Ubuntu deb tarball matching the resolved Ubuntu
  guest arch (arm64 on the Mac, x86_64 on the target). The exact x86_64 Ubuntu
  tarball filename is **verified against IBM during planning, not assumed**. RHEL's
  x86_64 MQ artifacts are host-arch-independent and unchanged.
- `ansible/roles/mq-install/tasks/main.yml` — drive the tar filename from
  `ansible_architecture` (the pattern the obs roles already use). The
  `./ibmmq-*.deb` install glob is already arch-agnostic, so the install body is
  unchanged.

## 8. Error handling

Fail loud throughout, following the existing `StepFailedError` / `InventoryError`
idiom — no swallowed errors, no silent fallbacks:

- missing native KVM → hard stop (D3);
- arm64-on-x86 requested → hard stop (D4);
- missing resolved topology file → Vagrantfile hard stop naming the fix;
- missing required tool or artifact → preflight hard stop with a suggestion;
- unknown host arch from `platform.machine()` → raise.

## 9. Testing & acceptance

- **Unit** — `platforms.resolve` across the full matrix (both host arches ×
  kvm/no-kvm × each platform); `hostfacts` normalisation
  (`arm64`/`aarch64`/`amd64`/`x86_64`, unknown → raise); preflight checklist (each
  missing tool/artifact → suggestion + non-zero exit; in-Vergil short-circuit);
  resolved-topology renderer; inventory/fleet regression (status shows resolved
  arch). Facts are injected, so the whole matrix is reachable under the repo's
  100%-branch-coverage gate.
- **Acceptance** — per the repo's cold-rebuild acceptance gate, a real one-pass
  lab bring-up on an x86 host (native KVM, nothing emulated), and a regression
  bring-up on the arm64 Mac dev host (Ubuntu KVM + RHEL TCG, unchanged). Lint-green
  is necessary but not sufficient.

## 10. Open items for the implementation plan

- Confirm the `in_vergil` marker (real, documented signal).
- Verify the x86_64 Ubuntu vagrant-libvirt box name.
- Verify the x86_64 Ubuntu MQ Advanced for Developers deb tarball filename against
  IBM.
- Decide the exact `ResolvedNode` schema / resolved-file shape.
- Confirm x86_64 firmware choice (OVMF vs SeaBIOS/default q35) for the native-KVM
  row.
