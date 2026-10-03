# OS-axis spike: Ubuntu 26.04 libvirt base box and x86-64-v3 guest exposure

> Spikes **S1** and **S3** (spec §5), plan task **T0b** of epic
> [logical-minds-foundry/.github#280](https://github.com/logical-minds-foundry/.github/issues/280)
> (issue [#1272](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1272)).
> Tasks T8 (Ubuntu 26 entry) and T10 (RHEL 10 entry) consume it.
>
> The live data was gathered on 2026-10-03 by two validation tasks, one per host:
> [#1290](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1290)
> (macOS arm64, Lima Vergil VM) and
> [#1291](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1291)
> (cloud x86_64, GCP Vergil VM). Their Outcome comments hold the verbatim
> evidence. Both ran the same throwaway script, reproduced in the
> [appendix](#appendix-the-spike-script-verbatim).
>
> Convention: each claim is tagged **[data]** (what the run or a cited source
> shows) or **[judgment]** (our reasoning on top).

## 1. Results per host

| Fact | macOS arm64 (#1290) | cloud x86_64 (#1291) |
|---|---|---|
| Box | `cloud-image/ubuntu-26.04` | `cloud-image/ubuntu-26.04` |
| Pinned version | `20260927.0.0`, provider `libvirt (arm64)` | `20260927.0.0`, provider `libvirt (amd64)` |
| Box add | already present from attempt 1 (same version) | downloaded and added |
| Boot (`vagrant up`) | **booted**, `boot_seconds=35` | **booted**, `boot_seconds=36` |
| Guest OS | `Ubuntu 26.04.1 LTS` (Resolute Raccoon) | `Ubuntu 26.04.1 LTS` (Resolute Raccoon) |
| Guest kernel | `7.0.0-34-generic` | `7.0.0-34-generic` |
| x86-64-v3 flags, host | n/a (arm64) | `abm avx2 bmi1 bmi2 f16c fma movbe xsave` |
| x86-64-v3 flags, guest (KVM `host-passthrough`) | n/a (arm64) | `abm avx2 bmi1 bmi2 f16c fma movbe xsave` |
| x86-64-v3 flags, guest (TCG `maximum`) | not measured | **not measured** |
| Script result | `spike exit=0`, guest destroyed | `spike exit=0`, guest destroyed |

- **[data]** Vagrant Cloud publishes `cloud-image/ubuntu-26.04` version
  `20260927.0.0` for the libvirt provider on both `amd64` and `arm64`. The box
  metadata URL is
  `https://vagrantcloud.com/api/v2/vagrant/cloud-image/ubuntu-26.04`.
  (#1290, #1291)
- **[data]** The box boots under the lab's vagrant-libvirt on both hosts, in
  35 s (arm64) and 36 s (x86_64), measured from `vagrant up` to the end of
  hostname setup. (#1290, #1291)
- **[data]** On the cloud x86_64 host, all eight x86-64-v3 marker flags the plan
  names (`avx2 bmi1 bmi2 fma movbe f16c abm xsave`) are present in the guest
  under KVM `cpu_mode: host-passthrough`, and they match the host's set. (#1291)
- **[data] Not measured: TCG.** Plan T0b step 3 also asks for the same check in a
  TCG guest with `cpu_mode: maximum`. The spike script did not run a TCG guest,
  so this report has no data on whether TCG exposes the v3 flags.
- **[judgment]** The TCG gap does not block Phase 3. Phase 3's go/no-go turns on
  KVM exposure on the cloud host, which is shown above. On a host without KVM, a
  RHEL 10 guest would depend on TCG exposing x86-64-v3. Anyone who needs that
  path must measure it first.

## 2. Ubuntu 26.04 package availability (live `apt-cache policy`)

- **[data]** After `apt-get update` in the booted 26.04 guest, `apt-cache policy`
  gave the same candidates on both hosts (#1290, #1291):

  | Package | Installed | Candidate (arm64 and amd64) |
  |---|---|---|
  | `pacemaker` | (none) | `3.0.1-1ubuntu2` |
  | `pcs` | (none) | `0.12.1-2ubuntu3` |
  | `fence-agents-virsh` | (none) | `4.17.0-1ubuntu1` |
  | `drbd-utils` | (none) | `9.22.0-1.2build1` |
  | `targetcli-fb` | (none) | `1:3.0.1-0.1build1` |
  | `linux-modules-extra-7.0.0-34-generic` | no output | no output |

- **[data]** These candidates match the archive-metadata versions recorded in
  [`os-version-support-matrix.md`](../reference/os-version-support-matrix.md)
  §3.3.
- **[data]** `apt-cache policy` prints nothing for a package name apt does not
  know, so `linux-modules-extra-7.0.0-34-generic` does not exist on 26.04, on
  either architecture. (#1290, #1291)

### 2.1 Why `linux-modules-extra-*` is absent

- **[data]** Canonical's kernel team deprecated `linux-modules-extra` starting
  with the 6.15 development kernel (the 25.10 cycle). Every kernel module now
  ships in `linux-modules`, which is installed by default. Source: Kleber Souza,
  "Kernel Development Release Cadence and Deprecation of linux-modules-extra",
  Ubuntu Community Hub, 2025-07-28:
  <https://discourse.ubuntu.com/t/kernel-development-release-cadence-and-deprecation-of-linux-modules-extra/65176>
- **[data]** A follow-up comment on #1291 inspected the guest's kernel package
  offline, without booting a guest: `linux-modules-7.0.0-34-generic`
  `7.0.0-34.34` amd64, from
  <https://archive.ubuntu.com/ubuntu/pool/main/l/linux/linux-modules-7.0.0-34-generic_7.0.0-34.34_amd64.deb>
  (sha256 `fd207ceefc3b1d817439d1ebf1013d00689c60fa90a04f7b8b4ef39d3f01ac3f`).
  It contains `drbd.ko.zst`, `target_core_mod.ko.zst` with the file, iblock,
  pscsi and user backstores, `iscsi_target_mod.ko.zst` and `tcm_loop.ko.zst`.
  `modinfo` on the extracted files gave `vermagic: 7.0.0-34-generic` for each,
  and DRBD `version: 8.4.11`. The same archive pool directory has no
  `linux-modules-extra-7.0.0-34*` file.
- **[judgment]** The DRBD and LIO target modules come with the base
  `linux-modules` package on 26.04, so the lab does not need
  `linux-modules-extra-*` there.
- **Not run:** `modinfo drbd` / `modinfo target_core_mod` (or `modprobe`) inside
  a booted 26.04 guest, and any check of the arm64 kernel package for this exact
  kernel. The offline check above covers amd64 only. The in-guest check on both
  architectures belongs to T6/T8.
- **[data]** The in-tree DRBD module is the 8.4 line (8.4.11), not DRBD 9, while
  `drbd-utils` is 9.22. (#1291 follow-up)
- **[judgment]** This matches how the Ubuntu `pcmk` arm uses DRBD today (the
  in-tree module plus `drbd-utils` from apt). A 26.04 design that needs DRBD 9
  features would need LINBIT's out-of-tree `drbd-dkms`. That `drbd-utils` 9.22
  drives the 8.4 module through its 8.4-compatible userland is expected, but was
  not tested.

## 3. Script defect in attempt 1 (not a 26.04 finding)

- **[data]** The first arm64 run failed at `vagrant up` with
  `Call to virDomainDefineXML failed: unsupported configuration: ACPI requires
  UEFI on this architecture`. The spike Vagrantfile set a UEFI `loader` for
  aarch64 but no `nvram` vars file. (#1290)
- **[data]** The script was fixed to mirror `lab/Vagrantfile` (loader **and**
  nvram), and the #1290/#1291 issue bodies were updated before the real runs.
  Both results above come from the fixed script, which is the one in the
  appendix.
- **[judgment]** The defect was in the throwaway script, not in the box or the
  lab. The lab's own Vagrantfile already sets both values.

## 4. Go/no-go

| Phase | Gate (plan T0b) | Result | Decision |
|---|---|---|---|
| **Phase 2** (Ubuntu 26) | a 26.04 libvirt box exists and boots | box `20260927.0.0` exists for amd64 and arm64, and boots on both hosts | **GO** |
| **Phase 3** (RHEL 10) | a KVM guest on the cloud host exposes the x86-64-v3 flags | all 8 flags present under `host-passthrough` on the cloud x86_64 host | **GO** (cloud x86 host) |

- **[judgment]** Phase 3 is GO for the cloud x86_64 host. RHEL is x86-only, so
  the arm64 host has no Phase 3 role. TCG exposure is unmeasured (§1).
- **[judgment]** Phase 2 GO means the base box is usable. It does not change the
  IBM support status: Ubuntu 26.04 is still not an IBM-supported MQ platform
  ([`os-version-support-matrix.md`](../reference/os-version-support-matrix.md)
  §3.1 and §4), so the Ubuntu stack defaults stay at 24.

## 5. Implications for T6 and T8

- **[judgment] `drbd-san` must stop installing `linux-modules-extra` on 26.04.**
  `ansible/roles/drbd-san/tasks/main.yml` caches and installs
  `linux-modules-extra-{{ ansible_kernel }}`. That package does not exist on
  26.04, so the task would fail there. T6/T8 need a per-version branch that
  installs nothing extra on 26.04 (the modules are in `linux-modules`), plus an
  in-guest `modprobe drbd` / `modprobe target_core_mod` check on both
  architectures.
- **[judgment] Review the pacemaker and pcs roles for the major-version jump.**
  26.04 ships pacemaker `3.0.1` and pcs `0.12.1`. 24.04 ships `2.1.6` and
  `0.11.7` (matrix §3.3). Review `ansible/roles/pcmk-cluster` for pacemaker 3.x
  and pcs 0.12 changes in T8; V2 must show the cluster forms.
- **[judgment]** `fence-agents-virsh`, `drbd-utils` and `targetcli-fb` are all
  present on 26.04. Only their version deltas need checking in T8.
- **[judgment] (T10)** The cloud x86_64 host can expose x86-64-v3 to a KVM guest
  under `host-passthrough`, so it meets the `x86-64-v3` requirement T10 adds to
  the RHEL 10 catalog entry.

## Appendix: the spike script (verbatim)

This is the script from the #1290 and #1291 issue bodies, byte for byte (the two
bodies carry identical scripts). It needs a checkout of this repo inside its
Vergil VM. It boots one throwaway `spike-2604_spike2604` domain, records facts,
and destroys the domain. It never touches `lab_*` domains.

```bash
#!/usr/bin/env bash
# OS-axis spike (epic logical-minds-foundry/.github#280, parent task #1272).
# Self-contained: boots ONE throwaway cloud-image/ubuntu-26.04 guest, records facts, destroys it.
# Run from a checkout of mq-resiliency-lab-for-linux inside its Vergil VM. Touches no lab_* domain.
set -euo pipefail
BOX=cloud-image/ubuntu-26.04
BOX_VERSION=20260927.0.0
dir="$(uv run mqlab build path temp)/spike-2604"
mkdir -p "$dir" && cd "$dir"
cat > Vagrantfile <<'VF'
Vagrant.configure("2") do |config|
  config.vm.box = "cloud-image/ubuntu-26.04"
  config.vm.box_version = "20260927.0.0"
  config.vm.synced_folder ".", "/vagrant", disabled: true
  config.vm.define "spike2604" do |n|
    n.vm.hostname = "spike2604"
    n.vm.provider :libvirt do |lv|
      lv.cpus = 2
      lv.memory = 2048
      lv.cpu_mode = "host-passthrough"
      if `uname -m`.strip == "aarch64"
        # arm64 virt needs UEFI firmware AND an nvram vars file, exactly as the lab sets them
        # (lab/Vagrantfile loader+nvram); a loader alone fails with "ACPI requires UEFI".
        lv.loader = "/usr/share/AAVMF/AAVMF_CODE.fd"
        lv.nvram  = "/var/lib/libvirt/qemu/nvram/spike-2604_VARS.fd"
        lv.input :type => "mouse", :bus => "virtio"
      else
        lv.machine_type = "q35"
      end
    end
  end
end
VF
arch=$([ "$(uname -m)" = aarch64 ] && echo arm64 || echo amd64)
out="$dir/spike-$(uname -m).txt"
rc=0
{
  echo "== host: $(uname -m) $(hostname) $(date -u +%FT%TZ)"
  echo "== host x86-64-v3 flags (empty on arm64):"
  grep -o -w -E 'avx2|bmi1|bmi2|fma|movbe|f16c|abm|xsave' /proc/cpuinfo | sort -u | tr '\n' ' '; echo
  echo "== box add"
  vagrant box add "$BOX" --provider libvirt --architecture "$arch" --box-version "$BOX_VERSION" 2>&1 \
    || echo "(box add returned non-zero; already present is fine)"
  echo "== up"
  start=$(date +%s)
  vagrant up --provider libvirt 2>&1 | tail -8
  echo "boot_seconds=$(( $(date +%s) - start ))"
  echo "== guest facts"
  vagrant ssh -c 'cat /etc/os-release; uname -r'
  echo "== guest x86-64-v3 flags (host-passthrough; empty on arm64)"
  vagrant ssh -c "grep -o -w -E 'avx2|bmi1|bmi2|fma|movbe|f16c|abm|xsave' /proc/cpuinfo | sort -u | tr '\n' ' '"
  echo
  echo "== apt-cache policy (after apt-get update)"
  vagrant ssh -c 'sudo apt-get update -qq >/dev/null 2>&1; for p in pacemaker pcs fence-agents-virsh drbd-utils targetcli-fb "linux-modules-extra-$(uname -r)"; do apt-cache policy "$p" | head -3; done'
} 2>&1 | tee "$out" || rc=$?
echo "== destroy"
vagrant destroy -f 2>&1 | tail -2
# vagrant-libvirt can leave an arm64 domain's nvram-backed definition behind; remove it if present.
if virsh -c qemu:///system list --all --name | grep -qx 'spike-2604_spike2604'; then
  virsh -c qemu:///system undefine spike-2604_spike2604 --nvram --remove-all-storage
fi
echo "spike exit=$rc; results: $out"
exit "$rc"
```
