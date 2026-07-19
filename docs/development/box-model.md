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

## 1. The five baked boxes

Each box is a **minimal per-role fat box** — it carries only the install surface
that role needs, nothing more. The taxonomy is **role × platform**:

| Box | Base | Role(s) that boot it | Bakes |
|-----|------|----------------------|-------|
| `mq-rdqm-rhel9` | `rhel/9.6-x86_64` (locally built) | `rdqm-a1..3`, `rdqm-b1..3` | MQ product + RDQM stack (DRBD/Pacemaker, kernel-matched `kmod-drbd`) + node-exporter + alloy + the journald diagnostic default |
| `obs-ubuntu2404` | `cloud-image/ubuntu-24.04` | `obs` | Prometheus + Grafana + Loki + node-exporter + alloy, plus the slow cgo `mq_prometheus` build + MQ SDK |
| `infra-ubuntu2404` | `cloud-image/ubuntu-24.04` | `infra-client`, `infra-svc` | BIND9 + `/etc/bind/zones` scaffolding + node-exporter + alloy |
| `mq-ubuntu2404` | `cloud-image/ubuntu-24.04` | the MQ commons — `svc-sim` (svc), `app-client` (app), `mon-probe` (probe) | Ubuntu MQ product (server + client + SDK + samples) + node-exporter + alloy + the cgo `mq_prometheus` build + `acl` |
| `mq-nativeha-rhel9` | `rhel/9.6-x86_64` (locally built) | `nha-rhel-a1..3`, `nha-rhel-b1..3` | base MQ product (**no** RDQM/DRBD — Native HA replicates in the raft log, so **no kernel pin**) + node-exporter + alloy |

The three shared Ubuntu MQ commons (svc / app / probe) all boot the **one**
`mq-ubuntu2404` box: its server-set install carries the client and SDK too, so a
single baked image serves the simulated upstream (server + QM), the application
client (client + SDK for pymqi), and the probe's exporter (cgo SDK).

### The RHEL9 flavors (the kernel-pin dilemma)

There are **two *fat* RHEL 9.6 boxes plus the bare base**, and the split is the
answer to a kernel-pin problem:

- **`mq-rdqm-rhel9`** — the fat RDQM box. RDQM's DRBD kernel module
  (`kmod-drbd`) must match the running kernel exactly. Baking it solves the pin
  **by construction**: the box is baked from this exact base, so its kernel and
  its baked `kmod-drbd` are matched from birth — there is no separate kernel pin
  to maintain, and no way for a boot-time update to drift the kernel out from
  under the module (see §5).
- **`mq-nativeha-rhel9`** — the fat Native-HA box. Native HA replicates in MQ's
  own raft log, not DRBD, so there is **no kernel module and no pin** — it bakes
  the base MQ product on the stock `rhel/9.6` base (no RDQM/Pacemaker stack, no
  `extra_disk`). The two fat RHEL boxes are distinct not by kernel flavor but
  because native HA omits the entire RDQM/DRBD stack.
- **`rhel96-x86_64`** — the *bare* RHEL 9.6 base box, still booted un-baked by the
  RHEL arms that are **not yet** baked (`pcmk-rhel-*`, `san-a-rhel`). These still
  pay the full per-run install; baking the Pacemaker RHEL arm is deferred to a
  follow-on epic. (The Native-HA RHEL arm was baked in `logical-minds-foundry/.github#88`.)

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

## 3. The build pipeline (`build-fatbox.sh`)

`lab/boxes/build-fatbox.sh` builds a box by **provision-then-snapshot**:

1. Ensure the base box is present (the RHEL base is itself locally built by
   `rhel96/build-box.sh`; the Ubuntu base comes from Vagrant Cloud).
2. Full-copy the base disk into the libvirt pool as a transient build disk, boot
   a throwaway `fatbox-<box>-build` domain, and wait for its DHCP lease + sshd.
3. Run the box's bake playbook (`ansible/bake-<role>.yml`) against it **over SSH**
   — the same per-node access the lab uses, but to this one build VM.
4. **Reset `/etc/machine-id`** (empty the file, drop the dbus copy) so every clone
   regenerates a unique machine-id on first boot. A fixed baked machine-id makes
   systemd derive an identical DHCP client-id on every clone; libvirt's dnsmasq
   keys leases on client-id, so two clones of the same Ubuntu fat box (e.g.
   `infra-client` + `infra-svc`) would collide on one IP and break netplan. This
   is why the reset is generic across every box.
5. Power off, `qemu-img convert -c` the disk into the host-durable cache, and
   `vagrant box add` it.

### The host-durable cache

The baked `.box` artifacts live in **`build/state/boxes/`** on the persistent
`/vergil` data disk (resolved via the *main* worktree, so every git worktree
shares one cache). Each box has a sibling `<box>.manifest-hash` stamp. Because
`state/` outlives the VM, a baked box survives a VM rebuild without a re-bake —
this is the pivot the rebuild tiers in §4 turn on.

### Staleness: manifest-hash + graduated age

The builder REUSEs a cached box only while it is still valid on two axes:

- **Manifest hash** — a sha256 over the version-pin set and the bake inputs. If it
  differs from the stamped hash, the cache is void and the box is rebuilt.
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
  the BUILD path and re-bakes all five boxes (and re-acquires the entitlement-gated
  state media). This is the only tier that pays the full bake cost.
- **VM rebuild** — `vrg-vm rebuild` re-provisions the dev VM, wiping the boot disk;
  the image pool and registered Vagrant boxes are gone, but the `.box` cache on the
  persistent `/vergil` disk is untouched. `build-fatbox.sh` hits REUSE and just
  runs `vagrant box add` from cache — **no re-bake**.
- **Stack loop** — the everyday inner loop: tear the lab down and bootstrap it
  again. The VM and its registered boxes stay; only the guest VMs are recreated,
  and bootstrap runs just the per-run configure halves.

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
| `mqlab box status [BOXES…]` | read-only fleet table: per-box `CACHED` / `AGE` / `HASH` (match\|mismatch) / `REGISTERED` / `DECISION` (REUSE\|BUILD\|STALE\|FORCE). No side effects. |
| `mqlab box build [BOXES…]` | ensure each box is present — REUSE a valid cache, else bake. `--all` for the whole fleet. Shares the ensure-box core with `bootstrap`. |
| `mqlab box rebuild [BOXES…]` | **force a fresh bake in place** (`--rebuild-box`), overwriting the cache — the targeted "rebake one box" operation, no disk wipe. |
| `mqlab box clean [BOXES…]` | pristine cache removal + Vagrant deregister (`--all` is confirm-guarded). `clean` then `build` round-trips a box from scratch. |

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

## 6. The RHEL DVD: one-time download, static archive, auto-stage

Every artifact the boxes bake is anonymously fetchable — IBM MQ and all the
Ubuntu/OSS pieces — with **one exception**: the RHEL 9.6 DVD ISO (~12.7 GB) that
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
the lab's canonical filename (`rhel-9.6-x86_64-dvd.iso`) so it lands where the rest
of the tooling looks. New RHEL versions are just new ISOs dropped into the same
directory.

### Auto-stage on VM build (host-side rsync)

Because `vrg-vm create`/`rebuild` runs natively on the macOS host — the one place
with access to both the local archive and the (cloud) build volume —
`lab/scripts/stage-rhel-dvd-from-archive.sh` syncs the archive into `build/state/`:

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

Concretely, `box.verify_rhel_dvd(version)` runs from the build core (`build_boxes`)
whenever the RHEL base box is about to be **built or force-built** — never on a
REUSE, since a cached box attaches no ISO. It resolves the ISO exactly as
`stage-rhel-iso.sh` does (`MQLAB_RHEL_ISO` → `RHEL_ISO` → the `build/state/`
default) and compares its SHA-256 against `box.RHEL_DVD_SHA256`, a version→checksum
map the **operator** fills in from Red Hat's published value (the checksum is never
fabricated in-tree). The verdict is three-way: a **missing** ISO blocks the build,
a **pinned-but-mismatched** ISO blocks it, and an **unpinned** version emits a loud
`NOTICE` and **proceeds** — verification stays off until someone pins the checksum,
so turning the check on never regresses the pre-existing no-SHA cold rebuild.
