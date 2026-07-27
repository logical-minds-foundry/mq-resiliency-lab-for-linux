# The `build/` directory layout

`build/` is the gitignored, host-mounted tree that holds every piece of working
state the lab produces or consumes. It is split into four buckets, each with
fixed **keep / nuke / share** semantics. The split exists so a routine cleanup
can reclaim derived junk without ever touching irreplaceable lab state, and so
parallel git worktrees share one lab instead of forking it.

`src/mqlab/paths.py` is the authority for bucket paths in Python; `mqlab build
path <bucket>` is the same authority for shell and other non-Python consumers.
**Never hardcode a `build/<name>` path** — go through a bucket.

## The four buckets

| Bucket   | Scope  | Lifecycle (when `clean` removes it)        | Holds |
|----------|--------|---------------------------------------------|-------|
| `cache/` | shared | only `mqlab build clean --cache`            | re-fetchable downloads — MQ tarballs (`mq/`), doc refs (`refs/`), `ansible_collections/` |
| `state/` | shared | only `mqlab build clean --state --yes-destroy-state` | irreplaceable, lifecycle-coupled facts — the RHEL DVD ISO, `snapshots/`, `boxes/` (arch-suffixed `<box>-<arch>.box` + `<box>-<arch>.manifest-hash` pairs), `secrets/`, `fence_key*`, `runs/`, `reports/`, `dr-runs/`, the `vagrant/` dotfile (domain↔vagrant mapping + keys), manifest selection pins |
| `work/`  | local  | **every** `mqlab build clean`               | deterministic renders — `inventory.ini`, `lab/topology.resolved.yaml`, `box-versions.json`, `versions.json`, `grafana/`, `prometheus/`, `obs/`, `salt/`, manifest overlays |
| `temp/`  | local  | **every** `mqlab build clean`               | scratch, junk, and the screenshot handoff dir |

**Keep vs nuke.** `work/` and `temp/` are pure derivations — every `mqlab build
clean` recreates them empty, and a VM rebuild re-renders them. `cache/` and
`state/` survive a routine clean: `cache/` because re-downloading is slow,
`state/` because it cannot be re-fetched at all (the RHEL ISO and RHEL-HA repo
are entitlement-gated; snapshots and secrets are the live lab). Dropping
`state/` therefore demands the explicit `--yes-destroy-state` confirmation.

**Shared vs local.** `cache/` and `state/` are *shared*: in a git worktree they
are symlinks back to the main checkout's `build/`, so every worktree drives the
**one** lab and reuses the **one** download cache. `work/` and `temp/` are
*local*: each checkout renders its own, so parallel branches never fight over an
inventory or a resolved topology.

The lab's vagrant state lives in `state/vagrant` (not a per-checkout
`lab/.vagrant`): `mqlab` points vagrant there via `VAGRANT_DOTFILE_PATH`, so the
domain↔vagrant mapping is shared like the rest of `state/`. A worktree can thus
drive — and `vagrant ssh` into — a lab any checkout created, and the mapping
survives a worktree being cleaned up after its branch merges (#355).

## What does *not* live in `build/`: the VM image pool

The lab's libvirt guest images — the per-domain `lab_*.img` overlays — live in
libvirt's **default pool (`/var/lib/libvirt/images`) on the VM's ephemeral boot
disk**, never under `build/`. They are the most ephemeral state the lab has:
re-created from the base boxes on every `vrg-vm rebuild`, so their lifecycle is
the boot disk's, not the persistent data disk's.

This is the one place the bucket model does **not** bend. `cache/` and `state/`
are *persistent* — in the cloud instance they live on the `/vergil` data disk
that outlives the VM — so redirecting the image pool into `build/` puts
wipe-on-rebuild overlays onto a never-wiped disk. That lifecycle/location
mismatch is exactly what #376 did (`MQLAB_LIBVIRT_POOL` →
`build/work/libvirt-images`): orphaned volumes survived a crash/rebuild and broke
the next `vagrant up` with `Volume for domain is already created`, unrecoverable
by `teardown` or `bootstrap --from vms`. Reverted in #385/#386. The correct
shape: images stay on the ephemeral boot disk, and the boot disk is sized to fit
(cloud: `boot_disk = "100GiB"`, #388). **Persistent disks hold persistent data
only — never VM overlays.**

This persistent-vs-ephemeral split is exactly what makes the **baked-box rebuild
tiers** cheap. The baked per-role box images live in `state/boxes/` on the
persistent disk — each keyed by arch as a `<box>-<arch>.box` +
`<box>-<arch>.manifest-hash` pair — while the image pool is ephemeral, so a
`vrg-vm rebuild` wipes
the pool but keeps the baked boxes, and the lab re-registers them from cache
instead of re-baking. See [`box-model.md`](box-model.md) for the box taxonomy,
the build pipeline, and the three rebuild tiers (nuclear / VM rebuild / stack
loop).

## The `mqlab build` commands

| Command | What it does |
|---------|--------------|
| `mqlab build ensure`  | Create the buckets; in a worktree, symlink `cache/`+`state/` to main. Idempotent. Runs automatically before any lab-loading verb. |
| `mqlab build status`  | List the buckets and whether each is `local` or a `symlink->main`. |
| `mqlab build path <cache\|state\|work\|temp>` | Print a bucket's absolute path — the seam shell/Ansible/Ruby consumers use. |
| `mqlab build clean [--cache] [--state --yes-destroy-state]` | Nuke `work/`+`temp/` (and any stray non-bucket entry at the `build/` root). `--cache` also drops downloads; `--state` (guarded) drops live lab state. |
| `mqlab build migrate [--dry-run]` | One-time: move an existing pre-bucket `build/` into the buckets (rename, so instant even for multi-gigabyte snapshots). |

> **Stray entries are disposable.** `mqlab build clean` removes anything at the
> `build/` root that is not one of the four buckets. Any artifact worth keeping
> must live inside a bucket, not at the `build/` root.
