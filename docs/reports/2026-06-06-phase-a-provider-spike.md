# Phase A Provider Spike — Findings

> **Status:** complete. Executed 2026-06-06 inside the `vergil-user` dev VM
> (12 CPU / 64 GiB, nested virt enabled) per
> [`docs/plans/2026-06-06-phase-a-virtualization-harness.md`](../plans/2026-06-06-phase-a-virtualization-harness.md)
> Tasks 1–4. Design context: spec §7.2 (the Apple-Silicon provider bind).

## Contents

- [Verdict](#verdict)
- [Evidence](#evidence)
  - [KVM-accelerated arm64 (the make-or-break property)](#kvm-accelerated-arm64-the-make-or-break-property)
  - [TCG-emulated x86-64 — mechanics proven; SSH-ready blocked by a plugin cap](#tcg-emulated-x86-64--mechanics-proven-ssh-ready-blocked-by-a-plugin-cap)
  - [Severable networking (the §3.1 fault primitives)](#severable-networking-the-31-fault-primitives)
- [Required provider settings (the spike's hard-won configuration)](#required-provider-settings-the-spikes-hard-won-configuration)
- [Environment prerequisites (each was a real blocker)](#environment-prerequisites-each-was-a-real-blocker)
- [Plan errata (corrections the spike feeds back into Task 6+)](#plan-errata-corrections-the-spike-feeds-back-into-task-6)

## Verdict

**The leading hypothesis holds — all three properties, after the #24
addendum.** Nested `vagrant-libvirt` on this stack (M5 Max → macOS vz →
Lima → KVM) delivers KVM-accelerated arm64 guests, genuinely severable
multi-NIC networking, and TCG-emulated x86-64 guests that reach DHCP in
**under a minute**. The spike initially mis-read the x86 arm as
"SSH-ready blocked by a plugin IP-wait cap"; the follow-up diagnostic
(#24 addendum below) found the true cause — a sub-x86-64-v2 CPU model
crash-looping EL9 userspace — and with `cpu_mode = "maximum"` the arm is
fully proven. **The spec §6 cloud-x86 break-glass is not needed.**

## Evidence

### KVM-accelerated arm64 (the make-or-break property)

- Box: `cloud-image/ubuntu-24.04` (v20260518.0.0) — **candidate 1 worked**;
  publishes a libvirt-provider arm64 build. No box-building fallback needed.
- Boot wall-clock: **~36 s** to "Machine booted and ready".
- Proof of acceleration: `virsh dominfo` domain runs and guest
  `systemd-detect-virt` reports **`kvm`** (not `qemu`/TCG); `uname -m`
  = `aarch64`.

### TCG-emulated x86-64 — mechanics proven; SSH-ready blocked by a plugin cap

- **Proven:** with the cross-arch settings below, the domain **defines,
  starts, and executes** under TCG (`virsh list` running; CPU time rising
  ~1:1 with wall-clock throughout — actively computing, not wedged).
- **Not proven:** boot to SSH. Two boxes were tried —
  `almalinux/9` (x86_64) and the much leaner `debian/bookworm64` (4 vCPU) —
  and **both failed to obtain a DHCP lease within vagrant-libvirt's
  hardcoded IP-wait cap**: `wait_till_up.rb` retries `tries: 300` × 2 s
  fog waits ≈ **600 s, ignoring `config.vm.boot_timeout` entirely**. Total
  wall-clock at teardown: ~15.3 min (alma, incl. box download) and ~11 min
  (debian).
- **Interpretation (data vs judgment):** the CPU-time progression is
  *consistent with* slow-but-healthy TCG boot (judgment: likely just the
  ~10–20× TCG slowdown on EL9/Debian cloud-init boots); a boot loop cannot
  be ruled out without console capture — no console was attached (vagrant
  tears the domain down on timeout).
- **Remediation options for the Phase C RDQM/RHEL-x86 arm** (decision
  deferred to the human; the lab proceeds arm64-first regardless):
  1. Diagnose first: boot the same box once via raw `virsh`/libvirt with a
     serial console attached, outside vagrant, to confirm it reaches DHCP
     and measure the true wall-clock.
  2. Carry a small plugin patch/fork raising the IP-wait (and propose an
     upstream knob — `boot_timeout` is the obvious carrier).
  3. Pre-baked lean x86 image (minimal services, fast DHCP) for lab use.
  4. Spec §6 break-glass: move the RDQM arm to a cheap cloud x86 box.

#### ADDENDUM (#24, same day): root cause found — CPU model, not speed

Option 1 (diagnose) was executed and **closed the question**. Raw-libvirt
boots of the same `almalinux/9` image under TCG:

| CPU config | Result |
|---|---|
| `custom`/`qemu64` (the spike's setting) | 40 min of 1:1 CPU spin, **no DHCP, empty serial** — then killed |
| `mode='maximum'` (`qemu -cpu max`) | **DHCP lease at 47 s**; SSH-reachable; `uname -m`=`x86_64`, `systemd-detect-virt`=`qemu` |

**Root cause:** EL9 requires the **x86-64-v2** microarchitecture level.
`qemu64` is sub-v2: the kernel boots, then glibc's HWCAP check kills early
userspace — an invisible crash-loop with no console output (the box's
serial console is not configured) that *looks* identical to "TCG is slow."

**Consequences:**

- The Required-provider-settings x86 stanza is corrected to
  `lv.cpu_mode = "maximum"` (no `cpu_model`); `custom`/`qemu64` is wrong
  for any EL9+ guest and must not be copied forward.
- The hardcoded 600 s IP-wait (above) still exists but **no longer bites**
  — EL9 reaches DHCP in well under a minute.
- **TCG x86 is viable and fast enough; the spec §6 cloud-x86 break-glass
  is not needed.** Phase C proceeds locally as the spec intended.
- `debian/bookworm64`'s earlier timeout is unexplained-but-moot (Debian's
  baseline is x86-64-v1, so it was plausibly genuinely slow under
  `qemu64`'s minimal feature set; untested under `maximum`).

### Severable networking (the §3.1 fault primitives)

Two distinct, both-useful fault axes were validated on an isolated libvirt
network (`net-hb-a`, no `<forward>`, no DHCP):

1. **Per-NIC severance — the reversible primitive.**
   `virsh domif-setlink <dom> <vnet> down` → guest sees `NO-CARRIER`,
   kernel withdraws the route, connectivity drops; `... up` restores
   cleanly. **Allow ~2 s settle before asserting** — immediately after
   setlink the guest may still answer (carrier/route propagation).
   **Assert severance against a peer guest's IP on that net** — the host
   gateway IP still answers via the management default route while the
   NIC is down (weak host model), exactly like the isolation case below.
2. **Per-network severance — the realistic silent partition.**
   `virsh net-destroy` kills connectivity but the guest link **stays UP**
   (no local link-down signal — the hard case for cluster heartbeats).
   **Caveat: it is one-way in practice.** `net-start` recreates the bridge
   but does **not** re-attach running guests' taps; recovery requires a
   guest reload (or tap re-plug). Use per-NIC severance for
   sever-and-restore tests; use net-destroy only where the test plan
   accepts guest reloads on recovery.

Isolated (forward-less) networks permit host↔guest ICMP, so gateway pings
are valid **positive** checks for a net the guest is attached to.
**Isolation (negative) checks must target other guests' IPs, not host
gateway IPs:** the host answers pings to any of its bridge addresses via
the guest's management-net default route (Linux weak host model), so a
cross-site gateway ping succeeds even when the bridges are perfectly
isolated. Found when the Task 7 smoke test's gateway-based negatives all
"failed" against correct isolation.

## Required provider settings (the spike's hard-won configuration)

The plan's Task 6 Vagrantfile must carry these; none are plugin defaults.

**arm64 guests (KVM):**

```ruby
lv.loader   = "/usr/share/AAVMF/AAVMF_CODE.fd"   # UEFI mandatory on arm64
lv.nvram    = "/var/lib/libvirt/qemu/nvram/<name>_VARS.fd"
# loader WITHOUT nvram is emitted as type='rom' and libvirt still
# rejects the domain (plugin domain.xml.erb:80-86) - set BOTH.
lv.input :type => "mouse", :bus => "virtio"       # no PS/2 or USB ctrl on arm64 virt
lv.cpu_mode = "host-passthrough"                  # host-model unsupported on aarch64 KVM
```

**x86-64 guests (TCG):**

```ruby
lv.driver       = "qemu"
lv.machine_arch = "x86_64"        # plugin option is machine_arch, NOT arch
lv.machine_type = "q35"
lv.cpu_mode     = "maximum"       # qemu -cpu max. REQUIRED for EL9+ guests:
                                  # sub-v2 models (qemu64) crash-loop early
                                  # userspace invisibly. See #24 addendum.
m.vm.boot_timeout = 1800
```

**Both:** networks must exist before `vagrant up` (pre-created via
`virsh net-define`/`net-start`); **NIC changes require domain
re-creation** — `vagrant reload` does not add networks to an existing
domain.

## Environment prerequisites (each was a real blocker)

| Prerequisite | Where it lives | Found by |
|---|---|---|
| `nested = true` profile knob → `/dev/kvm` | `vergil.toml` (#14, vergil-vm#131) | gate |
| `libvirt`/`kvm` group membership | vergil-vm#137 + restart-at-end-of-build (vergil-vm#142) | gate |
| `qemu-efi-aarch64` (AAVMF), `ovmf` | `vergil.toml` (#20) | spike Task 2 |
| `libarchive-tools` (bsdtar) | `vergil.toml` (#14) | gate |
| default libvirt storage pool | created in Task 1 (not provisioned) | spike Task 1 |

The storage pool is the one piece neither the VM profile nor the lab repo
provisions yet — Task 5's `net-up.sh` era scripts assume it exists; consider
folding pool creation into the harness scripts or the VM profile later.

## Plan errata (corrections the spike feeds back into Task 6+)

- `lv.arch` → **`lv.machine_arch`** (plan Task 3 used the wrong name).
- Spike domain names are `spike_<machine>` (project dir is `lab/spike`),
  so the main lab's prefix will be `lab_<machine>` as the plan assumed —
  verify on first `vagrant up`.
- Smoke tests must sleep ~2 s after `domif-setlink` before asserting.
- The plan's severability drill (Task 7 Step 4) should use per-NIC
  severance only; net-destroy recovery semantics make it unsuitable for
  a green-after-restore assertion without a guest reload.
