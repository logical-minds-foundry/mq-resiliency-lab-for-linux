# The baked-box lab model

The lab used to install everything on every node on every bring-up — MQ, the
DRBD/Pacemaker stack, the OSS agents, the observability stack — costing the best
part of an hour per run. The lab-bootstrap-performance epic
(`logical-minds-foundry/.github#70`) replaced that with **baked per-role boxes**:
the slow, deterministic install work is baked **once** into a golden box image
per role, and a bring-up only does the fast, instance-specific *configure* work.

This page is the reader's map to that model: the box taxonomy, the
bake/configure split, the build pipeline that produces the boxes, and — the part
that trips people up — the **three tiers of rebuild** and what each one costs.
For the exhaustive per-role bake-vs-configure classification, see
[`box-bake-manifest.md`](box-bake-manifest.md); for where the baked artifacts
live on disk, see [`build-layout.md`](build-layout.md).

## 1. The local-built boxes

The fleet is **generated from the OS version catalog**,
[`lab/versions.yaml`](../../lab/versions.yaml) (epic
`logical-minds-foundry/.github#280`). Nothing else writes a box name or an OS
version by hand. At today's versions the lab builds **eight boxes locally**: the
bare `rhel/9-x86_64` base box plus **seven per-role fat boxes**. Each fat box is a
**minimal per-role fat box**: it carries only the install surface that role needs,
nothing more. The taxonomy is **role × OS major × host-arch**:

| Box | Base | Arch | Role(s) that boot it | Bakes |
|-----|------|------|----------------------|-------|
| `mq-rdqm-rhel9` | `rhel/9-x86_64` (locally built) | `x86_64` (pinned) | `rdqm-a1..3`, `rdqm-b1..3` | MQ product + RDQM stack (DRBD/Pacemaker, kernel-matched `kmod-drbd`) + node-exporter + alloy + the journald diagnostic default |
| `mq-nativeha-rhel9` | `rhel/9-x86_64` (locally built) | `x86_64` (pinned) | `nha-rhel-crr-a1..3`, `nha-rhel-crr-b1..3` | base MQ product (**no** RDQM/DRBD — Native HA replicates in the raft log, so **no kernel pin**) + node-exporter + alloy |
| `obs-ubuntu24` | `cloud-image/ubuntu-24.04` | host-resolved | `obs` | Prometheus + Grafana + Loki + node-exporter + alloy + the prebuilt `mq_prometheus` exporter (built in the Go container, copied in; #1065) + MQ runtime, **plus** the log-search stack — OpenSearch + OpenSearch Dashboards + Data Prepper — consolidated onto obs (#1178/#1179, epic .github#267) |
| `infra-ubuntu24` | `cloud-image/ubuntu-24.04` | host-resolved | `infra-client`, `infra-svc` | BIND9 + `/etc/bind/zones` scaffolding + node-exporter + alloy |
| `mq-client-ubuntu24` | `cloud-image/ubuntu-24.04` | host-resolved | the MQ commons — `svc-sim` (svc), `app-client` (app), `mon-probe` (probe) | Ubuntu MQ product (server + client + SDK + samples) + node-exporter + alloy + the prebuilt `mq_prometheus` exporter (copied in; #1065) + `acl` + the svc responder pymqi venv (#1227) |
| `mq-nativeha-ubuntu24` | `cloud-image/ubuntu-24.04` | host-resolved | `nha-ubuntu-a1..3`, `nha-ubuntu-b1..3` | base Ubuntu MQ product (server + client + SDK + samples debs, **no** RDQM/DRBD — Native HA replicates in the raft log, so **no kernel pin**) + node-exporter + alloy |
| `pcmk-ubuntu24` | `cloud-image/ubuntu-24.04` | host-resolved | the Pacemaker cluster nodes — `pcmk-a1..3`, `pcmk-b1..3` | base Ubuntu MQ product (server + client + SDK + samples debs, **no** RDQM) + node-exporter + alloy |

### Box names: `<role>-<os><major>`

A box that contains a fixed OS carries that OS's **short major** in its name:
`<role>-<os><major>`. The roles are the catalog's `roles:` keys (`infra`, `obs`,
`mq-client`, `mq-nativeha`, `pcmk`, `mq-rdqm`); the OS is a catalog `os:` entry. A
RHEL base box is `rhel/<major>-x86_64`. Caches follow the box name (§3). The
point release (RHEL 9.6, a cloud-image version) is a **pin** in the catalog, not
part of the name, so moving 9.6 to 9.7 is a re-pin and a rebake, never a rename.
Logical names (stacks, nodes, inventory groups, QM names, playbooks, dashboards)
never carry a version. A playbook may carry the OS family
(`bake-nativeha-rhel.yml`), because a family is not a version.

`src/mqlab/box.py` builds the fleet from `Catalog.all_boxes`: the infra boxes
(`infra`, `obs`, `mq-client`) on the catalog's `infra:` OS, then each stack's box
roles on every OS major that stack supports and this host can run, plus one RHEL
base box per catalog RHEL major. RHEL is `x86_64`-only, so on an `aarch64` host the
fleet is the Ubuntu boxes alone. Until the topology names box roles directly
(Task T2 of the epic), a stack's roles are read back off its nodes' `platform:`
values, which already carry the generated names.

### The one-time rename (#1274)

Every box name changed when names became generated. The retired names live in one
place, [`src/mqlab/retired_boxes.py`](../../src/mqlab/retired_boxes.py):

| Retired name | Now |
|--------------|-----|
| `obs-ubuntu2404` | `obs-ubuntu24` |
| `infra-ubuntu2404` | `infra-ubuntu24` |
| `mq-ubuntu2404` | `mq-client-ubuntu24` |
| `mq-nativeha-ubuntu` | `mq-nativeha-ubuntu24` |
| `pcmk-ubuntu` | `pcmk-ubuntu24` |
| `rhel/9.6-x86_64` | `rhel/9-x86_64` |

`mq-rdqm-rhel9` and `mq-nativeha-rhel9` already fit the rule and keep their names.
The topology's platform keys follow too: `ubuntu24-arm64`, `ubuntu24-x86_64` and
`rhel9-x86_64`. The first build after the rename re-bakes every fat box, once. To
migrate a host:

1. `mqlab build migrate` renames the RHEL base box's cache
   (`rhel-9.6-x86_64-libvirt.box` to `rhel-9-x86_64.box`). A base box has no
   manifest hash, so the image is still good and skips a 45–90 minute DVD rebuild.
2. `mqlab box gc` deregisters every retired name from Vagrant, deletes the retired
   fat boxes' dead caches (their manifest hash names the box, so they can never be
   reused), and reclaims all of their libvirt base volumes. A volume that a live VM
   still backs onto is kept, as always. `--dry-run` reports without removing.
3. Bootstrap needs nothing extra. Before `vagrant up` it forgets any guest whose
   cached Vagrant `box_meta` names a retired box (or any box other than the one the
   topology now assigns, #858), so a retired box is never booted.

**Host-resolved vs. arch-pinned.** The five Ubuntu fat boxes are **host-resolved**:
each builds natively for whatever architecture the host runs — `aarch64` on an
Apple-silicon host, `x86_64` on an x86 host — so the guest arch is never pinned.
The two RHEL fat boxes (`mq-rdqm-rhel9`, `mq-nativeha-rhel9`) are **`x86_64`-only**;
building either on an ARM host is **refused**, not emulated (design D11) — their
RHEL DVD and MQ's LinuxX64 tarball are x86_64 artifacts. The Pacemaker arm's SAN
targets (`san-a`/`san-b`) carry no MQ payload, so they stay on the bare Ubuntu base
and are not baked.

The three shared Ubuntu MQ commons (svc / app / probe) all boot the **one**
`mq-client-ubuntu24` box: its server-set install carries the client and SDK too, so a
single baked image serves the simulated upstream (server + QM), the application
client (client + SDK for pymqi), and the probe's exporter (MQ runtime libs; the
`mq_prometheus` binary itself is prebuilt in the Go container, #1065).

### The RHEL9 flavors (the kernel-pin dilemma)

There are **two *fat* RHEL 9 boxes plus the bare base**, and the split is the
answer to a kernel-pin problem:

- **`mq-rdqm-rhel9`** — the fat RDQM box. RDQM's DRBD kernel module
  (`kmod-drbd`) must match the running kernel exactly. Baking it solves the pin
  **by construction**: the box is baked from this exact base, so its kernel and
  its baked `kmod-drbd` are matched from birth — there is no separate kernel pin
  to maintain, and no way for a boot-time update to drift the kernel out from
  under the module (see §5).
- **`mq-nativeha-rhel9`** — the fat Native-HA box. Native HA replicates in MQ's
  own raft log, not DRBD, so there is **no kernel module and no pin** — it bakes
  the base MQ product on the stock `rhel/9-x86_64` base (no RDQM/Pacemaker stack, no
  `extra_disk`). The two fat RHEL boxes are distinct not by kernel flavor but
  because native HA omits the entire RDQM/DRBD stack.
- **`rhel/9-x86_64`** — the *bare* RHEL 9 base box (topology platform
  `rhel9-x86_64`), installed at the catalog's pinned point release. No RHEL arm boots it un-baked
  any more; its sole role now is to be the base image the two fat RHEL boxes are
  baked **from** (§3). (The Native-HA RHEL arm was baked in
  `logical-minds-foundry/.github#88`; the RDQM box bakes from this same base, above.)

Both attach the RHEL install DVD as a cdrom — it doubles as a complete offline
BaseOS+AppStream dnf repo for the unregistered guests.

## 2. Bake vs. configure, and phased startup

**Bake** = image-bakeable install: packages, downloaded/compiled binaries, users,
directory scaffolding, and *static* config identical for every lab. It runs once,
when the image is built, and never contains secrets or topology-/host-specific
data.

**Configure** = per-run: everything keyed to a specific lab instance — queue
managers, clustering, TLS/PKI material, injected secrets, topology-rendered
scrape targets / dashboards / DNS zones, and per-host config.

Roles that mix the two are split with the `tasks_from` idiom — `tasks/install.yml`
(baked) and `tasks/configure.yml` (per-run) — so the per-run path reaches the same
end state it always did, just skipping the baked half. The full per-role
classification is [`box-bake-manifest.md`](box-bake-manifest.md).

### Phased startup: services baked inert, started per-run

A baked service that comes up on first boot **before** its per-run config exists
would either crash-loop or race the configure step. So the rule is: **bake the
software, but leave its service inert** (install half only, unit present but not
started). The per-run configure half drops the instance config and *then* starts
it — for example alloy is baked binary+unit but needs a per-run `config.alloy`
before it can run, and on the obs box Loki / Prometheus / Grafana are baked
install-half-only and started by `site-obs.yml`.

Two services are the deliberate **benign exceptions**, left enabled at bake
(#642):

- **`node_exporter`** — a static host-metrics exporter with no per-run config. It
  races nothing and cannot come up in a broken pre-config state, so it is baked
  whole and left enabled; its tiles simply go green as soon as a clone boots.
- **`rdqm.service`** — IBM's rpm-shipped `oneshot` RDQM reboot daemon, auto-enabled
  by the `MQSeriesRDQM` package. It is production-intended (reboot survival) and
  verified benign, so it is left enabled rather than fought back down to inert.

### No per-login dynamic MOTD (#1229)

On stock Ubuntu, every SSH session runs PAM's `pam_motd`, interactive or not.
The first of its two session lines (`motd=/run/motd.dynamic`, with no
`noupdate`) runs every script in `/etc/update-motd.d/` on each login. The
`pam_motd(8)` man page documents this: `noupdate` means "Don't run the scripts
in /etc/update-motd.d to refresh the motd file". `50-landscape-sysinfo` is the
expensive script. In the #1200 macOS runs, obs had five or more concurrent
`landscape-sysinfo` processes, each at about 90% CPU for 6–9 minutes. That is
roughly 4.5 of its 12 vCPUs, and OpenSearch was still not listening at 16
minutes. The sampler's logins, Ansible's own re-logins (ControlPersist expires
between plays) and operator SSH all trigger it.

So every baked Ubuntu box (`obs-ubuntu24`, `infra-ubuntu24`,
`mq-client-ubuntu24`, `mq-nativeha-ubuntu24`, `pcmk-ubuntu24`) runs the `motd-off` role in
its own play near the end of its bake:

- It comments out both `pam_motd.so` session lines in `/etc/pam.d/sshd` and
  `/etc/pam.d/login`. Ubuntu 24.04 ships the same pair in both files: openssh's
  [`debian/openssh-server.sshd.pam.in`](https://git.launchpad.net/ubuntu/+source/openssh/tree/debian/openssh-server.sshd.pam.in?h=ubuntu/noble-updates)
  and shadow's
  [`debian/login.pam`](https://git.launchpad.net/ubuntu/+source/shadow/tree/debian/login.pam?h=ubuntu/noble-updates).
  The edit matches only uncommented lines, so it is idempotent.
- It masks `motd-news.timer`, so nothing refreshes the MOTD in the background.
- It then fails the bake loudly if any file under `/etc/pam.d` still has an
  active `pam_motd` line, or if the timer does not read `masked`.

The static `/etc/motd` is no longer shown at login either. Nothing in the lab
uses it on these throwaway guests.

### cloud-init and snapd trimmed off the boot path (#1250)

In #1247 run 3, `systemd-analyze blame` on the slow guests put two units at the
top: `cloud-init.service` (obs +1min 6s, mon-probe +1min 25s) and
`snapd.seeded.service` (obs +60s, mon-probe +1min 6s). That run was I/O-bound
(#1249), which inflated both, but both run on every boot of every Ubuntu guest.
`cloud-init-local` and `cloud-init` also sit on sshd's critical chain:
`cloud-init.service` is `Before=sshd.service`, so vagrant waits for it.

**What cloud-init actually does on a clone.** The `cloud-image/ubuntu-24.04`
base is pristine (no `/var/lib/cloud`, no netplan, no `vagrant` user). Its
`99_vagrant.cfg` sets `datasource_list: [ NoCloud, None ]` and defines the
`vagrant` user with Vagrant's insecure key. Nothing ever attaches a NoCloud
seed, so cloud-init falls back to `DataSourceNone`. On the bake VM's single
boot it does the one-time work: it creates the `vagrant` user, generates the
SSH host keys, grows `/` to the 18G build disk and writes a fallback netplan
for the bake VM's NIC. The box image keeps that state under the instance id
`iid-datasource-none`, which is the same for every clone. So, as booting a
clone of `infra-ubuntu24` alone showed (#1250), every once-per-instance
module on a clone logs "previously ran". Only two jobs still do anything:

- **`cloud-init-local` re-renders the mgmt NIC's netplan.** With no local
  datasource it writes the fallback config on every boot, for the clone's own
  NIC. The baked `/etc/netplan/50-cloud-init.yaml` matches the *bake VM's* MAC
  (and its name was `enp1s0`; the clone's mgmt NIC came up as `enp5s0`).
  Without the re-render the clone's mgmt NIC gets no DHCP lease, and vagrant
  never reaches ssh.
- **The init stage's `growpart` + `resizefs` grow `/`.** They run with frequency
  `always`, and on first boot they grow the box's 18G root partition to the
  guest's 20G disk (§3's 18G / `virtual_size: 20` headroom; observed
  `18252545536 → 20400029184` bytes).

The hostname is Vagrant's job (`config.vm.hostname`, set over ssh after boot).
The private-network NICs are Vagrant's too (`/etc/netplan/50-vagrant.yaml`).

So cloud-init is **trimmed, not disabled.** A plain `/etc/cloud/cloud-init.disabled`
([cloud-init docs](https://cloudinit.readthedocs.io/en/latest/howto/disable_cloud_init.html))
would break first-boot networking. Every Ubuntu bake ends with a play that runs
the `cloud-init-trim` role:

- It drops `/etc/cloud/cloud.cfg.d/99_lab_trim.cfg`. That sets
  `cloud_init_modules: [growpart, resizefs]`, empties `cloud_config_modules`
  and `cloud_final_modules`, and sets `preserve_hostname: true`. The fallback
  netplan render is not a module, so the trim leaves it alone. The stages and
  module frequencies are in the cloud-init
  [boot stages](https://cloudinit.readthedocs.io/en/latest/explanation/boot.html)
  and [module reference](https://cloudinit.readthedocs.io/en/latest/reference/modules.html).
- It masks `cloud-config.service` and `cloud-final.service`. On a clone,
  everything in those stages has already run, or has nothing to do
  (`scripts_per_boot` has no scripts, `final_message` only logs).
- It fails the bake loudly unless all of these hold: the merged config that
  cloud-init itself loads (`cloudinit.stages.Init().cfg`) carries the trimmed
  lists, the two services read `masked`, `cloud-init-local` and `cloud-init`
  still read `enabled`, and no `cloud-init.disabled` marker exists.

Fully removing cloud-init would need replacements for both jobs: a MAC-agnostic
mgmt-NIC network config and an in-guest root grow. That is a bigger change,
left for later.

**snapd.** `snapd.seeded.service` is only `snap wait system seed.loaded`, and it
is `Before=multi-user.target`, so it holds boot until snapd has started. Four of
the five Ubuntu boxes have no snaps at all: the base seeds none (its
`state.json` reads seeded with zero snaps), and no bake role installs one. The
fifth is **obs on aarch64**. There `grafana-image-renderer`'s browser is the
distro `chromium-browser`, which on 24.04 is a transitional deb that installs
the chromium snap. The baked obs box carries `chromium`, `cups`, `core24`,
`gnome-46-2404`, `mesa-2404`, `gtk-common-themes`, `bare` and `snapd` snaps (the
cups snap is the one in obs's dmesg). So the same final play runs the
`snapd-off` role in one of two modes:

- **`purge`** (the default, and obs on x86_64, which uses the Google Chrome
  `.deb`). It first refuses to go on if `snap list` shows any snap, so a role
  that later starts using a snap fails the bake instead of being silently broken.
  Then it purges `snapd` and pins it out with `/etc/apt/preferences.d/99lab-no-snapd`
  (`Pin-Priority: -1`), so a later install cannot pull it back as a Recommends.
  `ubuntu-server` only *recommends* snapd. It verifies that snapd is gone and has
  no install candidate.
  The purge has a side effect on other roles (#1265). snapd's `postrm purge`
  runs `deb-systemd-helper purge`, and its `rmdir_if_empty` (init-system-helpers
  1.66ubuntu1, `/usr/bin/deb-systemd-helper`) removes **every** empty directory
  under `/etc/systemd/system` and `/etc/systemd/user`, not only snapd's. That is
  how x86_64 obs lost its baked, still-empty `grafana-server.service.d` (aarch64
  obs keeps snapd, so its copy survived). So the role records the empty
  directories before the purge and restores them, with the same mode and owner,
  afterwards. snapd's own directories stay gone. The grafana configure half also
  re-creates its drop-in directory per run, and the final bake-guard play fails
  the bake if a directory a per-run configure half needs is missing
  ([`box-bake-manifest.md`](box-bake-manifest.md#every-ubuntu-box-the-bake-guard-1265)).
- **`keep`** (obs on aarch64 only). snapd stays enabled so the chromium snap
  keeps working. Only the `snapd.seeded.service` boot gate is masked. The image
  is already seeded, so the gate only ever waited. The role verifies `masked`
  for the gate and `enabled` for `snapd.service` and `snapd.socket`, and it
  refuses `keep` on a box with no snaps.

Both roles run in each Ubuntu bake's last image-changing play (only the read-only
#1265 guard play follows), so the snap guard sees everything the bake installed. Every Ubuntu box needs a rebake to pick them up
(the manifest-hash closure forces it).

## 3. The build pipeline (`build-fatbox.sh`)

The builders are **dumb** (#1274): they carry no box table. `mqlab` passes every
input as a flag, taken from the box's catalog entry (`box.builder_args`):

| Builder | Flags mqlab passes |
|---------|--------------------|
| `lab/boxes/build-fatbox.sh` | `--box <role>-<os><major>`, `--arch`, `--base-kind ubuntu\|rhel`, `--base-box`, `--base-box-version <v\|none>`, `--bake <stem>` (runs `ansible/bake-<stem>.yml`), `--dvd <iso\|none>`, `--os-pin <base_box>@<pin>`, `--mq-bearing 0\|1`, `--domain-type`, `--cpu-mode` |
| `lab/boxes/rhel/build-box.sh` | `--major <N>`, `--point <N.M>`, `--iso <file>`, `--domain-type`, `--cpu-mode` |

Every flag is required; a missing one exits 2 with `ERROR: --<flag> is required`.
`--base-box-version` and `--dvd` take the literal `none` (an Ubuntu box attaches no
DVD; a RHEL bake must name one). Run `mqlab box build <box>` rather than a builder
by hand. `lab/scripts/stage-rhel-iso.sh` and `scripts/push-rhel-iso.sh` take
`--iso <file>` the same way.

`lab/boxes/build-fatbox.sh` builds a box by **provision-then-snapshot**:

1. Ensure the base box is present. The RHEL base is itself locally built by
   `rhel/build-box.sh`, and `mqlab` builds it **first**: `box build` of a RHEL fat
   box ensures its base box (REUSE when cached, never forced by `box rebuild` of
   the fat box), and the fat-box builder fails loudly if the base is not
   registered. The Ubuntu base comes from Vagrant Cloud at the catalog's
   `base_box_version` pin.
2. Full-copy the base disk into the libvirt pool as a transient build disk, boot
   a throwaway `fatbox-<box>-build` domain, and wait for its DHCP lease + sshd.
3. Run the box's bake playbook (`ansible/bake-<stem>.yml`) against it **over SSH**
   — the same per-node access the lab uses, but to this one build VM.
4. **Reset `/etc/machine-id`** (empty the file, drop the dbus copy) so every clone
   regenerates a unique machine-id on first boot. A fixed baked machine-id makes
   systemd derive an identical DHCP client-id on every clone; libvirt's dnsmasq
   keys leases on client-id, so two clones of the same Ubuntu fat box (e.g.
   `infra-client` + `infra-svc`) would collide on one IP and break netplan. This
   is why the reset is generic across every box.
5. Power off, `qemu-img convert -c` the disk into the host-durable cache, and
   `vagrant box add` it.

### Keeping a REUSE box registered (#1248)

A REUSE run does **not** re-add a box that is already registered from the same
cache artifact. Each `vagrant box add` the builders make is followed by a stamp
file, `mqlab-registration`, written beside the registered `box.img`. It records
the cache artifact's identity: its path, size, mtime and manifest hash (`-` for
the base box, which has none). On REUSE the builder compares that stamp with the
cache it would add now:

- **current**: every registered copy carries this identity, so the add is
  skipped (`registration: current … no vagrant box add needed`);
- **stale**: the box is registered from another artifact, so it is re-added
  and stamped;
- **unstamped**: the box is registered with no stamp (it was added by hand, or
  before #1248). The builder compares the registered `box.img` and
  `metadata.json` byte for byte with the cache, a one-time full read. If they
  are identical it **adopts** the registration: it writes the stamp and adds
  nothing, so the box keeps its `box.img` mtime and its libvirt base volume.
  Otherwise it re-adds the box;
- **absent**: the box is not registered, so it is added and stamped.

`build-fatbox.sh --dry-run` (and so `mqlab box status`'s builder call) prints the
same `registration:` line for a REUSE box. The shared logic lives in
[`lab/boxes/_box-register.sh`](../../lab/boxes/_box-register.sh), sourced by both
builders.

Skipping the add matters for two reasons. The add itself unpacks the whole box
(65–72 s for obs alone). It also gives the registered `box.img` a new mtime, and
for an unversioned box vagrant-libvirt names the base volume after that mtime
(`<box>_vagrant_box_image_0_<mtime>_box.img`, from `get_volume_name` in
vagrant-libvirt 0.12.2's `action/handle_box_image.rb`). So every re-add made the
next `vagrant up` upload the full base image into the libvirt pool again, about
10 GiB per macOS run.

The rebuild paths still re-add:

- A **rebake** (manifest-hash BUILD, or `mqlab box rebuild`) rewrites the cache
  and always runs `vagrant box add --force`, stamped with the new identity. The
  next `vagrant up` uploads a fresh base volume, and the old one is reclaimed
  (below).
- **`mqlab box clean`** deregisters the box (`vagrant box remove` deletes its
  directory, stamp included), so the next `box build` bakes and adds it fresh.
- A **VM rebuild** wipes `~/.vagrant.d` with the boot disk, so the first REUSE
  afterwards finds the box absent and adds it from the cache.

### Base-volume cleanup

After a bake and after every teardown, `box gc` reclaims box base volumes from the
libvirt `default` pool (#759). For a box that is registered, it keeps exactly the
volume the registered `box.img` resolves to (the mtime rule above) and deletes
every other volume of that box. A volume left over from before a rebake is
removed even if the rebaked box has not been uploaded yet. For a box with no
registration it keeps the newest volume. In both cases a volume that a live VM
overlay still uses as its backing store is never deleted. Teardown never
deregisters a box, so in the stack loop the same base volumes survive from run to
run and `vagrant up` reuses them.

### The host-durable cache

The baked `.box` artifacts live in **`build/state/boxes/`** on the persistent
`/vergil` data disk (resolved via the *main* worktree, so every git worktree
shares one cache). Each fat box is keyed by arch — `<box>-<arch>.box` (e.g.
`mq-nativeha-ubuntu24-aarch64.box`, `mq-rdqm-rhel9-x86_64.box`) beside a sibling
`<box>-<arch>.manifest-hash` stamp. A RHEL base box's cache is its name with `/`
replaced by `-` (`rhel-9-x86_64.box`); the name already carries the arch. Because
`state/` outlives the VM, a baked box survives a VM rebuild without a re-bake —
this is the pivot the rebuild tiers in §4 turn on.

### Staleness: manifest-hash + graduated age

The builder REUSEs a cached box only while it is still valid on two axes:

- **Manifest hash** — a sha256 over the version-pin set and the bake inputs. If it
  differs from the stamped hash, the cache is void and the box is rebuilt. The
  inputs include the **OS pin** (`--os-pin <base_box>@<pin>`: the RHEL point release
  or the Ubuntu cloud-image version), so a catalog re-pin forces a rebake (#1274).
  For the **MQ-bearing** boxes (`mq-rdqm-rhel9`, `mq-client-ubuntu24`,
  `mq-nativeha-rhel9`, `mq-nativeha-ubuntu24` — the roles flagged `mq_bearing: true`
  in [`lab/versions.yaml`](../../lab/versions.yaml), passed as `--mq-bearing 1`) the
  single-source MQ-version pin [`lab/mq-version`](../../lab/mq-version) is folded
  into that hash (#1087/#1088), so bumping the pin flips exactly those boxes to
  BUILD on the next bootstrap while the commons boxes (`obs-ubuntu24`,
  `infra-ubuntu24`, and `pcmk-ubuntu24`) stay REUSE — a
  pin bump rebases the MQ box layer with no manual `mqlab box rebuild`.
- **Graduated age** — under 7 days: REUSE silently; 7–14 days: REUSE but emit a
  NOTICE to refresh; **14 days or older: REFUSE** (non-zero exit) demanding
  `--rebuild-box`, because a bake that old may miss base-OS security updates.

## 4. The three-tier rebuild model

The single most important thing to internalize about the baked-box lab is that
**"rebuild" is not one operation** — it is three tiers with very different costs,
because the baked boxes and the running VMs live on **different disks**:

- The baked `.box` cache is on the **persistent `/vergil` data disk**
  (`build/state/boxes/`).
- The libvirt guest-image pool (`/var/lib/libvirt/images`, the per-domain
  `lab_*.img` overlays) and the registered Vagrant boxes are on the **ephemeral
  boot disk**.

| Tier | Trigger | What it wipes | Boxes re-baked? | Cost |
|------|---------|---------------|-----------------|------|
| **Nuclear** | wipe the `/vergil` data disk | `build/state/boxes/` and all live state | **Yes — every box from scratch** | highest |
| **VM rebuild** | `vrg-vm rebuild` | the boot disk only (image pool + registered boxes) | **No** — the `.box` cache on `/vergil` survives, so the box is just re-registered from cache | medium |
| **Stack loop** | teardown → bootstrap | only the guest VMs | **No** — reuses the already-registered baked images | lowest |

- **Nuclear** — wiping the data disk drops the box cache, so the next build takes
  the BUILD path and re-bakes all seven fat boxes (plus the base box, and
  re-acquires the entitlement-gated state media). This is the only tier that pays
  the full bake cost.
- **VM rebuild** — `vrg-vm rebuild` re-provisions the dev VM, wiping the boot disk;
  the image pool and registered Vagrant boxes are gone, but the `.box` cache on the
  persistent `/vergil` disk is untouched. `build-fatbox.sh` hits REUSE and just
  runs `vagrant box add` from cache — **no re-bake**.
- **Stack loop** — the everyday inner loop: tear the lab down and bootstrap it
  again. The VM and its registered boxes stay; only the guest VMs are recreated,
  and bootstrap runs just the per-run configure halves. The box ensure finds each
  REUSE box already registered from its cache and skips the add, and the libvirt
  base volumes survive teardown, so nothing is unpacked or uploaded again (#1248).

This split is deliberate and load-bearing: the image pool **must** stay on the
ephemeral boot disk. Redirecting it onto the persistent disk (#376) left orphaned
overlays surviving a rebuild and breaking the next `vagrant up`; it was reverted
in #385/#386. Persistent disks hold persistent data only — never VM overlays. See
[`build-layout.md`](build-layout.md) for the full disk-lifecycle rationale.

### Targeted box rebake: the `mqlab box` CLI

The three tiers above are coarse — they turn on which *disk* gets wiped. Between
them sits a finer, everyday need: **rebuild one box in place**, wiping no disk and
leaving the rest of the fleet alone (a role's install changed, or a box drifted
past its staleness band). That is the `mqlab box` CLI (epic
`logical-minds-foundry/.github#91`) — a thin orchestration layer over
`build-fatbox.sh`/`build-box.sh`; it never re-implements the bake or staleness
decision, it renders and drives the shell builder's own:

| Verb | What it does |
|------|--------------|
| `mqlab box status [BOXES…]` | read-only fleet table: per-box `ARCH` / `CACHED` / `AGE` / `HASH` (match\|mismatch) / `REGISTERED` / `DECISION` (REUSE\|BUILD\|STALE\|FORCE). The `ARCH` column shows each box's resolved build arch (host-resolved for the Ubuntu boxes, `x86_64` for the RHEL boxes). No side effects. |
| `mqlab box build [BOXES…]` | ensure each box is present — REUSE a valid cache, else bake. Auto-renders the host-resolved topology first (so a standalone build never dies at box registration on a fresh checkout). `--all` for the whole fleet; `--config <file>` for every box a build file needs (e.g. `os: rhel:9` builds the infra boxes plus the RHEL stacks' boxes on RHEL 9; an unsupported request exits 2 naming the fix). A RHEL fat box pulls in its base box first. Shares the ensure-box core with `bootstrap`. |
| `mqlab box rebuild [BOXES…]` | **force a fresh bake in place** (`--rebuild-box`), overwriting the cache — the targeted "rebake one box" operation, no disk wipe. |
| `mqlab box clean [BOXES…]` | pristine cache removal + Vagrant deregister (`--all` is confirm-guarded). `clean` then `build` round-trips a box from scratch. |
| `mqlab box gc [--dry-run]` | deregister the retired box names and delete their dead caches (§1, the one-time rename), then reclaim orphaned base volumes from re-bakes (§3). |

A **cold-boot staleness nudge** rides these surfaces: a write-once stamp records
the last full cold boot, and `box status` / `doctor` / `bootstrap` emit a banded
NOTICE as it ages — a reminder to nuke-and-rebake periodically rather than let the
fleet rot. (An automated build cadence is a follow-on.)

## 5. OS currency comes from rebuilding the box

The lab used to run a base-OS refresh (`dnf`/`apt` update) at instance-build time.
That was **removed** (#639/#641): a blanket "update everything" fought the pinned
baked stack — on RHEL it hit a hard LINBIT/Pacemaker depsolve conflict, and it
risked drifting the kernel out from under the matched `kmod-drbd` (§1).

OS currency now comes from **rebuilding the box**, not from updating at boot. The
14-day staleness refusal in §3 is the enforcement: a bake old enough to be missing
base-OS security updates is refused until it is re-baked from a fresh base. This
keeps the running lab reproducible and the RDQM kernel/module pin intact, while
still bounding how stale a box's base OS can get.

### No in-guest apt auto-updates (#1225)

The same reasoning rules out Ubuntu's in-guest auto-updater. Every baked Ubuntu
box (`obs-ubuntu24`, `infra-ubuntu24`, `mq-client-ubuntu24`, `mq-nativeha-ubuntu24`,
`pcmk-ubuntu24`) runs the `apt-autoupdate-off` role as the **first play** of its
bake. The role masks `apt-daily.timer`, `apt-daily-upgrade.timer`,
`apt-daily.service`, `apt-daily-upgrade.service` and
`unattended-upgrades.service`, and drops `/etc/apt/apt.conf.d/99lab-no-auto-upgrades`,
which sets every `APT::Periodic::*` knob to `"0"`. Nothing fires on first boot.

- **Why this is safe.** The boxes are short-lived and rebuilt cold from a fresh
  base roughly weekly. The staleness gate in §3 enforces this: a NOTICE at 7 days
  and a refusal at 14. That rebuild is the update path, so an in-guest updater
  adds nothing.
- **Why it matters.** Before #1225 the updater fired on every freshly booted guest
  and held the dpkg lock, and provision had to wait it out. In the #1200 runs that
  wait took 18 s, 73 s and 208 s on three otherwise-identical bootstraps. It was
  both a large cost and the main source of run-to-run variance in provision.
- **The provision-time guard stays.** `site-dns.yml` still carries the #1173
  mask-and-wait as a defensive path. It first checks with a read-only
  `systemctl is-enabled` query. When the units are already masked, as they are on
  any box baked after #1225, both #1173 steps are skipped and the wait costs about
  0 s. They only run on a box baked before this change, or on a host still on the
  plain cloud image (the SAN targets). That play only ever masks; it never unmasks
  or re-enables anything.

## 6. The RHEL DVD: one-time download, static archive, auto-stage

Every artifact the boxes bake is anonymously fetchable — IBM MQ and all the
Ubuntu/OSS pieces — with **one exception**: the RHEL DVD ISO (~12.7 GB) that
both RHEL box flavors attach as their offline BaseOS+AppStream dnf repo (§1). Red
Hat gates it behind authentication, and no automated credential-free fetch was ever
found. It is **operator-supplied**, and it is needed only on a **nuclear** rebuild
(a wiped `/vergil` — a VM rebuild and the stack loop both reuse the cached copy in
`build/state/`, per §4). Three mechanisms cooperate so this one manual artifact
costs the operator as little as possible.

### One-time download into a static archive

The operator downloads the DVD **once per RHEL version** from Red Hat and keeps it
in a **stable local archive directory** — not an ad-hoc `build/` copy that a wipe
would take with it. The default archive is `~/dev/software/rhel-dvds/`; override it
with the `MQLAB_RHEL_DVD_ARCHIVE` environment variable. The archived ISO must carry
the lab's canonical filename, the `iso:` value of its `os.rhel.<major>` entry in
[`lab/versions.yaml`](../../lab/versions.yaml), so it lands where the rest of the
tooling looks. New RHEL versions are just new ISOs dropped into the same
directory.

### Auto-stage on VM build (host-side rsync)

Because `vrg-vm create`/`rebuild` runs natively on the build host — arm64 (Apple
silicon) or x86, the one place with access to both the local archive and the
(cloud) build volume — `lab/scripts/stage-rhel-dvd-from-archive.sh` syncs the
archive into `build/state/`:

```bash
lab/scripts/stage-rhel-dvd-from-archive.sh            # rsync archive -> build/state/
lab/scripts/stage-rhel-dvd-from-archive.sh --dry-run  # show the planned rsync, copy nothing
```

It is an idempotent `rsync -a --ignore-existing`: an already-staged same-name ISO is
never re-copied, so re-runs are cheap despite the blob size, and it fails loud if the
archive directory is absent or holds no `*.iso`. It resolves `build/state/` through
the **main worktree** (git-common-dir), exactly as `stage-rhel-iso.sh` does, so it
works the same from any worktree. It is credential-less — the download is the only
step that touches Red Hat, and that stays manual.

The intent is for this script to run **automatically at the end of every VM build**,
declared as a post-build hook in `vergil.toml`. That hook is **not yet active**:
`vrg-vm` has no post-build-hook key today, so `vergil.toml` carries the declaration
as a clearly-commented, inert placeholder pending the cross-org capability
(vergil-project/vergil-tooling#2407, referenced in epic
`logical-minds-foundry/.github#91`). **Until it ships, run the script by hand after a
nuclear rebuild.**

### The `mqlab box` verify-and-guide backstop

The auto-stage is a convenience, not a guarantee — a first-ever run, a fresh archive
dir, or a not-yet-active hook can all leave the DVD missing. So the RHEL base-box
BUILD path (the sole DVD consumer) has a backstop: `mqlab box` **verifies** the ISO
is present at its canonical `build/state/` location and matches a **pinned SHA-256**
for the RHEL version. On a missing or mismatched ISO it emits fail-loud guidance —
the version, the Red Hat download URL, and the destination path — and stops before a
doomed build. No credential handling ever enters the tool; it only checks and guides.

Concretely, `box.verify_rhel_dvd(entry)` (the catalog's `os.rhel.<major>` entry:
its `point` names the release, its `iso` the file) runs from the build core
(`build_boxes`) whenever a RHEL base box is about to be **built or force-built** —
including when a RHEL fat box pulls in an uncached base — never on a
REUSE, since a cached box attaches no ISO. It resolves the ISO exactly as
`stage-rhel-iso.sh` does (`MQLAB_RHEL_ISO` → `RHEL_ISO` → the `build/state/`
default) and compares its SHA-256 against `box.RHEL_DVD_SHA256`, a point-release→checksum
map the **operator** fills in from Red Hat's published value (the checksum is never
fabricated in-tree). The verdict is three-way: a **missing** ISO blocks the build,
a **pinned-but-mismatched** ISO blocks it, and an **unpinned** version emits a loud
`NOTICE` and **proceeds** — verification stays off until someone pins the checksum,
so turning the check on never regresses the pre-existing no-SHA cold rebuild.
