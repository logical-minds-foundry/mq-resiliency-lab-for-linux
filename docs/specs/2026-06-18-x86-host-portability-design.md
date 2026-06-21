# x86 host portability — gate virtualization on host architecture

- **Issue:** #276
- **Date:** 2026-06-18
- **Status:** design (brainstormed; pending implementation plan)
- **Supersedes assumptions in:** `docs/reports/2026-06-06-phase-a-provider-spike.md`
  (the arm64-host provider mechanics it established remain correct; this spec
  generalises the *selection* of those mechanics to the actual host arch).
- **Builds on (does not duplicate):** the version-manifest subsystem #266
  (`docs/specs/2026-06-18-version-manifest-design.md`; commits #278/#280). That
  work already owns *versioned MQ artifact acquisition* — `manifest._ARCH_SUFFIX`
  (platform→tarball arch), `manifest.tarball_name()`, `manifest.setup_platforms()`,
  and `artifact.ensure_mq_tarballs()` (per-platform cache→download→verify). This
  spec **integrates with** that machinery (see D6 and §7) instead of building a
  parallel fetcher. The feature branch was rebased onto #266 before planning.

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
3. **MQ artifacts — the arch *suffix* is still arm64-pinned in three sites** even
   after #266 templated the version (`{{ mq_version }}`):
   - `scripts/fetch-mq.sh` — the bulk downloader; fetches only `UbuntuLinuxARM64`
     (the code-level `cli._fetch_mq_tarball` is a manual-placement stub that
     raises, so this script is still how `build/mq/` actually gets populated);
   - `ansible/roles/mq-install/tasks/main.yml` — server set, `…UbuntuLinuxARM64…`;
   - `ansible/roles/mq-client/tasks/main.yml` — the `app-client` client set,
     `…UbuntuLinuxARM64…`. **This one is easy to miss** and would break the app
     path on x86. The implementation must not trust this hand list: it **greps the
     repo for `UbuntuLinuxARM64`** (and any `Ubuntu*ARM64`) and fixes every hit, so
     a fourth site cannot hide.

   Note: `manifest._ARCH_SUFFIX` *maps* platform→suffix correctly per platform but
   only knows `ubuntu2404-arm64 → UbuntuLinuxARM64`; it gains an `ubuntu2404-x86_64`
   entry here (§7). The roles above don't consult the manifest — they pin the
   suffix literally — so they still need the arch fix.

Already arch-neutral (no change needed): the observability roles
(Prometheus/Loki/Alloy/node-exporter) all branch on `ansible_architecture`; the
RHEL box builder; the `cloud-image/ubuntu-24.04` box itself (its Vagrant-Cloud
entry already ships a `libvirt` provider for **both** `amd64` and `arm64`, so the
**box name does not change** — see §4.2). The topology box *registry* is still
modified by this spec: it gains a concrete `ubuntu2404-x86_64` platform entry.

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
- **D4 — arm64-on-x86 is unreachable and guarded.** The host-dependent default
  platform never selects `ubuntu2404-arm64` on an x86 host (it selects
  `ubuntu2404-x86_64`), and `rhel96-x86_64` is x86_64. So no node ever resolves to
  an arm64 guest on an x86 host. If that combination is ever requested anyway,
  resolution fails loud — we do not emulate ARM on x86.
- **D5 — Preflight diagnoses, it does not install.** Outside Vergil, a sanity-check
  verifies host prerequisites and *suggests* distro install commands. It never
  runs them; a `--fix` that installs is explicitly deferred.
- **D6 — Integrate with the version-manifest subsystem (#266), don't duplicate
  it.** Platform strings stay **arch-explicit** (`ubuntu2404-arm64`,
  `ubuntu2404-x86_64`) so `manifest._ARCH_SUFFIX`/`setup_platforms`/
  `artifact.ensure_mq_tarballs` keep working unchanged. Native-preferred is
  expressed as a **host-dependent default platform**, not a logical-platform
  sentinel — making `fleet.lab_guests()` host-aware makes the whole manifest
  acquisition chain (`setup_platforms → tarball_name → ensure_mq_tarballs`) pick
  the host's arch automatically. The Vagrantfile keeps reading the manifest's
  `build/box-versions.json`; this spec only replaces its `case arch` block.

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
- `in_vergil` — whether we are inside the managed Vergil base VM. **Confirmed
  marker: the `/etc/vergil` sentinel file exists** (corroborated in the live dev
  VM by the Lima virtiofs mounts and the `lima-vergil-user-…` hostname; the file
  is the stable, explicit signal we key on).

`probe()` is the only function that touches the real host. Everything else takes a
`HostFacts` value as input, so the full matrix is unit-testable with no hardware.

### 4.2 The resolver (`src/mqlab/platforms.py`)

Pure function `resolve(topology, facts) -> dict[str, ResolvedNode]`. This is the
authority named in D2. `ResolvedNode` carries every field the Vagrantfile needs:
`platform, box, arch, driver, machine_arch, machine_type, loader, nvram,
input_bus, cpu_mode, boot_timeout, cpus, memory, extra_disk, dvd, nics` (it keeps
`platform` so the Vagrantfile can still index the manifest's
`build/box-versions.json` by platform — D6).

**Arch-explicit platforms + a host-dependent default** (D6 — not a logical
sentinel). The topology box registry keeps concrete, arch-tagged platforms and
gains one entry:

- `ubuntu2404-arm64` → `{box: cloud-image/ubuntu-24.04, arch: aarch64}` (today).
- `ubuntu2404-x86_64` → `{box: cloud-image/ubuntu-24.04, arch: x86_64}` (**new**;
  same box — that box ships both `libvirt` arch variants, **confirmed** against
  Vagrant Cloud, so no new box and Vagrant's host-arch default already pulls the
  matching variant).
- `rhel96-x86_64` stays pinned x86_64 (unchanged).

Native-preferred is then a pure `default_platform(facts)` → `ubuntu2404-arm64` on
an arm64 host, `ubuntu2404-x86_64` on an x86_64 host. `fleet.lab_guests()` applies
it (replacing the static `defaults.platform` / `fleet.DEFAULT_PLATFORM`), so every
node that doesn't pin a platform tracks the host — and, via D6, the manifest
artifact chain follows automatically.

The matching x86_64 Ubuntu MQ deb tarball name is **confirmed**:
`…-IBM-MQ-Advanced-for-Developers-UbuntuLinuxX64.tar.gz` (HTTP 200 on the IBM CDN).

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
`build/lab/topology.resolved.yaml` — the same "mqlab renders a `build/*` file the
Vagrantfile reads" pattern that #266 established with `build/box-versions.json`.
The Vagrantfile loads the resolved file and applies each node's fields verbatim;
its `case platform.arch` block (lines 42–62) is **deleted**. It **keeps** reading
`build/box-versions.json` for the per-platform box-version pin (#266) — that
mechanism is untouched; each `ResolvedNode` carries its concrete `platform` so the
existing `box_versions[platform]` lookup still works. If the resolved file is
absent or stale, the Vagrantfile fails loud, naming the mqlab command that
produces it.

**The resolved file is a precondition of exactly the verbs that load the
Vagrantfile — and only those.** In the post-#266 `cli.py`, `vagrant` is shelled
from precisely four places: `vm create` and `vm up` (`vagrant up <g>`, line 541),
`obs up` (`vagrant up obs mon-probe`, line 402), and `vm ssh` (`vagrant ssh` via
`os.execvp`, line 787). **`vm down` / `vm destroy` / `vm status` and the `net`
verbs use `virsh`, not `vagrant`** (`virsh shutdown`/`destroy`/`undefine`/`list`,
lines 550–559, 722) — they do **not** load the Vagrantfile and must **not** be
gated. This is load-bearing: `vm status` calls no resolver, so it keeps working as
a read-only diagnostic even on a host with no usable KVM (matching the
display-safe `resolve()`); gating it would hard-fail diagnosis on exactly the
hosts that need it. So a small, idempotent `ensure_resolved()` is called by the
four `vagrant`-loading verbs only; the Vagrantfile's loud guard is a true backstop
that fires only if a human runs raw `vagrant` from `lab/` outside mqlab.

## 5. Components & data flow

```
hostfacts.probe() ─┬─► default_platform(facts) ─► fleet.lab_guests() ─► manifest.setup_platforms ─► artifact.ensure_mq_tarballs (#266)
                   │                                      └─► vmstatus
                   └─► platforms.resolve(topo, facts) ─► ResolvedNode per guest
topology.yaml ─────────────────────────────────────────────┐
                                                            ├─► render build/lab/topology.resolved.yaml ─► Vagrantfile (+ box-versions.json, #266)
                                                            └─► doctor preflight
```

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `hostfacts.py` | Probe + normalise host (arch, kvm, distro, in-Vergil via `/etc/vergil`) | OS / filesystem |
| `platforms.py` | Pure resolver `(topo, facts) → ResolvedNode` + `default_platform(facts)` | nothing (pure) |
| resolved-topology renderer | Write `build/lab/topology.resolved.yaml`; `ensure_resolved()` | `platforms`, `hostfacts`, `paths` |
| `doctor` (preflight) | Host-capability checklist + suggestions; exit non-zero on hard fail | `hostfacts`, `shutil.which` |
| `Vagrantfile` | Apply resolved fields (+ `box-versions.json`); fail loud if resolved file missing | resolved file, box-versions.json |
| `fetch-mq.sh` / `mq-install` / `mq-client` | Arch-correct MQ artifact | host arch / `ansible_architecture` |
| `manifest._ARCH_SUFFIX` (#266) | platform→tarball arch | gains `ubuntu2404-x86_64` entry |
| `fleet.lab_guests()` | name→platform, **now host-aware default** | `default_platform(facts)` |
| `topology.yaml` | `ubuntu2404-x86_64` platform entry | — |

### 5.1 Topology-consumer audit (bounding the blast radius)

The host-dependent default means `fleet.lab_guests()` is no longer a pure function
of `topology.yaml` — it now folds in `default_platform(facts)`. Its consumers
therefore inherit host-awareness and must be checked. Modules that read
`lab/topology.yaml` and/or `lab_guests()`: `arms`, `cli`, `dashboard`, `fleet`,
`guestsel`, `inventory`, `netstate`, `parity`, `roster`, `scrape`, `setups`,
`vmstatus`, plus the #266 newcomers `manifest` and `artifact`. The implementation
**must audit each** and record a verdict:

- **name-only (unaffected)** — never reads `boxes[*].arch` / `defaults.platform`
  as a concrete key, never depends on the resolved arch; or
- **arch-aware (intended host-awareness)** — `fleet.lab_guests()` (the injection
  point), `vmstatus` (shows the per-guest platform), and — critically —
  `manifest.setup_platforms()` + `artifact.ensure_mq_tarballs()` (#266), which
  must see the host-resolved platform so the right MQ tarball is acquired on x86.
- **must stay deterministic in tests** — `lab_guests()` and `setup_platforms()`
  must take **injected facts** (default to `probe()`), so the unit suite and CI
  (which runs on x86 GitHub runners) don't flip results by host arch. This is a
  signature ripple into #266's `manifest.py`; coordinate with that owner.

The deliverable is an explicit per-module verdict plus the facts-threading change
to `lab_guests`/`setup_platforms`.

## 6. Preflight — `mqlab doctor`

A new command, also run (via `ensure_resolved`'s sibling gate) as the first step
of the `vagrant`-loading verbs (`vm create`/`vm up`/`obs up`/`vm ssh` — §4.3).
`doctor` is a **host-capability** check (arch, KVM, tools); it is deliberately
*not* setup-aware.

1. **In Vergil?** → report "Vergil-managed; prerequisites guaranteed by the
   `[vm.vergil-user]` profile" and pass. The check trusts the profile.
2. **Outside Vergil** → checklist against probed facts:
   - **Native KVM** usable for the host arch — **hard fail** if absent (D3). No
     override.
   - **Required tools**: `qemu-system-x86_64` (plus `qemu-system-aarch64` only on
     an arm64 host), `libvirtd`/`virsh`, `vagrant` + the `vagrant-libvirt` plugin,
     `ansible`, `genisoimage`, the Python/uv runtime.
3. **Every miss** prints a suggested install command for the detected distro
   family — suggestion only, never executed (D5).
4. Exit non-zero on any hard failure; lifecycle verbs refuse to proceed.

**Out of `doctor`'s scope, by design:**
- *Artifact prerequisites* (MQ tarball present-or-placeable; RHEL box + DVD ISO).
  These are **setup-specific**, and #266 already enforces them where the setup is
  known: `artifact.ensure_mq_tarballs()` (invoked by `vm create`/`provision`)
  fails loud on a missing tarball via the `_fetch_mq_tarball` placement stub.
  `doctor` does not duplicate that check.
- *The arm64-on-x86 guard* (D4) is enforced at **resolution** time
  (`platforms.resolve` → `ensure_resolved`, hence on every gated bring-up), not as
  a standalone `doctor` line — it is unreachable by construction, so it is a
  resolver assertion, not a checklist item.

The "Lima / nerdctl" layer is explicitly *not* a host prerequisite: those are the
macOS→VM mechanism Vergil uses to create the base VM, invisible from inside the
Linux host. What the preflight checks is the Linux-host tool/package set that the
Vergil profile would otherwise guarantee.

## 7. Artifacts (MQ)

The lab uses **two** IBM MQ tarballs, and which Ubuntu one is needed flips by host
arch while the RHEL one is constant:

| host | Ubuntu guests need | RHEL guests need |
|------|--------------------|------------------|
| arm64 (Mac dev) | `UbuntuLinuxARM64` (debs) | `LinuxX64` (rpms) |
| x86_64 (target) | `UbuntuLinuxX64` (debs, **new**) | `LinuxX64` (rpms) |

**The acquisition layer already exists (#266) and mostly composes for free.**
`artifact.ensure_mq_tarballs(setup, …)` iterates `manifest.setup_platforms(setup)`
→ `manifest.tarball_name(version, platform)` → fetch/verify per platform. Because
`setup_platforms` runs through `lab_guests()`, making `lab_guests()` host-aware
(D6, §4.2) makes this whole chain select the x86_64 Ubuntu platform on an x86
host. The integration is therefore small and targeted:

1. **`manifest._ARCH_SUFFIX`** — add `"ubuntu2404-x86_64": "UbuntuLinuxX64"`
   alongside the existing `ubuntu2404-arm64`/`rhel96-x86_64`/`alma9-x86_64`
   entries. (Confirmed filename: `…-UbuntuLinuxX64.tar.gz`, HTTP 200.) After this,
   `ensure_mq_tarballs` acquires the right Ubuntu tarball per host with no further
   change.
2. **Install roles** — `mq-install` and `mq-client` still pin the suffix literally
   (`…UbuntuLinuxARM64…`); change both to derive it from `ansible_architecture`
   (`UbuntuLinux{{ 'ARM64' if ansible_architecture == 'aarch64' else 'X64' }}`),
   matching the obs-role idiom. The `./ibmmq-*.deb` install glob is arch-agnostic,
   so the install bodies are otherwise unchanged. `rdqm-install` already uses
   `LinuxX64` and is unchanged.
3. **`scripts/fetch-mq.sh`** (the bulk downloader that actually populates
   `build/mq/`) — today it hardcodes `UbuntuLinuxARM64` and never fetches
   `LinuxX64` (RDQM's tar lands manually, per `lab-bringup-capture.md`). Make it
   fetch, by host arch (`uname -m`): the matching Ubuntu deb tarball
   (`UbuntuLinuxARM64`|`UbuntuLinuxX64`) **plus** `LinuxX64` (always, for any RHEL
   arm). This closes "clone and run" on a fresh host. (`cli._fetch_mq_tarball`
   stays the manual-placement stub it is today.)

## 8. Error handling

Fail loud throughout, following the existing `StepFailedError` / `InventoryError`
idiom — no swallowed errors, no silent fallbacks:

- missing native KVM → hard stop (D3), in `require_native_kvm`/`doctor`;
- arm64-on-x86 requested → hard stop (D4), in `platforms.resolve`;
- missing resolved topology file → Vagrantfile hard stop naming the fix;
- missing required host tool → `doctor` hard stop with an install suggestion;
- missing MQ artifact → `artifact.ensure_mq_tarballs` hard stop (#266), not
  `doctor` (the setup is known there, not in the host-capability check);
- unknown host arch from `platform.machine()` → raise.

## 9. Testing & acceptance

- **Unit** — `platforms.resolve` across the full matrix (both host arches ×
  kvm/no-kvm × each platform); `hostfacts` normalisation
  (`arm64`/`aarch64`/`amd64`/`x86_64`, unknown → raise); preflight checklist (each
  missing tool/artifact → suggestion + non-zero exit; in-Vergil short-circuit);
  resolved-topology renderer; inventory/fleet regression (status shows resolved
  arch). Facts are injected, so the whole matrix is reachable under the repo's
  100%-branch-coverage gate.
- **Acceptance is split into two tiers** because no x86 host is available yet
  (development happens on the arm64 Mac for the next week or two):
  - **Tier 1 — logic + arm64 regression (blocking, now).** The full unit matrix
    passes, plus a resolver dry-run on the Mac proving it *would* select native
    KVM / row 2 on an x86 host (the x86 *host* path cannot be exercised on arm64
    — only the x86 *guest*/TCG path can). Critically, a one-pass cold bring-up on
    the arm64 Mac proves this change **breaks nothing** that works today (Ubuntu
    KVM + RHEL TCG, unchanged). This is the near-term bar: *do no harm to the Mac
    path.*
  - **Tier 2 — real x86 host bring-up (deferred, blocks "done").** A one-pass
    cold bring-up on a real x86 host with native KVM. We **expect the first real
    run to surface breakage** and to iterate from there. Until this passes the
    spec is **not** "done" — Tier 1 unblocks coding, it does not close the gate.
    The x86 host is most likely an x86 box at work where the repo is cloned and
    run (which is the intended target use anyway), or, failing that, a
    nested-virt-capable cloud x86 node stood up for this validation.
  - Lint-green is necessary but not sufficient for either tier.

## 10. Open items for the implementation plan

**Resolved during planning prep (no longer open):**

- `in_vergil` marker → the `/etc/vergil` sentinel file (§4.1).
- x86_64 Ubuntu box → no new box; `cloud-image/ubuntu-24.04` ships both `libvirt`
  arch variants (confirmed via Vagrant Cloud) (§4.2).
- x86_64 Ubuntu MQ tarball → `…-IBM-MQ-Advanced-for-Developers-UbuntuLinuxX64.tar.gz`
  (HTTP 200 confirmed) (§4.2/§7).
- x86_64 firmware → keep the current default (q35/no explicit loader; today's TCG
  x86_64 path uses it and boots). AAVMF stays aarch64-only.

**Still open for the plan:**

- Decide the exact `ResolvedNode` schema / resolved-file YAML shape (fields listed
  in §4.2).
- **Thread injected facts through `fleet.lab_guests()` and #266's
  `manifest.setup_platforms()`** so host-awareness is deterministic in tests / on
  x86 CI (§5.1) — coordinate the `manifest.py` signature change with #266's owner.
- Execute the §5.1 consumer audit and record the per-module verdict (now incl.
  `manifest`, `artifact`).
- Rework `scripts/fetch-mq.sh` to fetch host-arch Ubuntu + `LinuxX64` (§7).
- **Identify/procure the Tier-2 x86 acceptance host** (work x86 box or
  nested-virt cloud node). Blocks acceptance, not implementation.
