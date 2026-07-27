# The SAN install-half deb cache (`build/cache/san-debs/`)

The two SAN target VMs (`san-a` / `san-b`) provide the shared iSCSI LUN the
Pacemaker arm's cluster nodes mount, with host-based DRBD replicating that LUN
cross-site. Unlike the six Pacemaker **cluster** nodes — which boot the baked
`pcmk-ubuntu` fat box — the SAN targets carry no IBM MQ payload, so they **stay on
the host-resolved base Ubuntu box** rather than a box of their own.

## Why the base box stays (no `san-ubuntu` fat box)

Baking a `san-ubuntu` fat box was considered and **rejected as disproportionate**
(decision `logical-minds-foundry/.github#108`, the epic #103 closing brainstorm).
Two payload-light VMs whose install-half is ~1–2 min of mostly-parallel `apt`
would drag in the entire per-arch bake-and-validate pipeline #103 built for the MQ
arms — a poor trade for the saving. The base-box model has been reliable; this is
a **performance / reproducibility** optimization, not a reliability fix.

## What the cache holds and why

Keeping the base box means the SAN install-half is installed **per run** by the
`drbd-san` and `iscsi-target` roles. Left as a plain network `apt install`, a cold
rebuild pays a ~100 MB internet download on the SAN targets every time — dominated
by `linux-modules-extra` (the in-tree DRBD kernel module). To kill that pull
without a fat box, the install-half debs are **pre-cached on the Ansible
controller** and installed from a local mgmt-network copy, mirroring the MQ-tarball
convention (`src/mqlab/artifact.py` → `build/cache/mq/`).

`build/cache/san-debs/` holds the three SAN packages, fetched via `apt-get
download` (pulls the `.deb` without installing):

| Package | Role | Kernel-coupled? |
|---------|------|-----------------|
| `drbd-utils` | `drbd-san` (DRBD userland) | no |
| `linux-modules-extra-<kernel>` | `drbd-san` (in-tree DRBD module) | **yes** — keyed by kernel version |
| `targetcli-fb` | `iscsi-target` (LIO admin tool) | no |

It lives in the nuke-safe, shared `cache/` bucket (re-fetchable, symlinked across
worktrees) — see [`build-layout.md`](build-layout.md). Address it through
`mqlab.paths.san_deb_cache_dir()`, never a hardcoded `build/…` path.

## How it is populated and consumed

- **Populate (host side).** The `mqlab bootstrap` provision phase declares a `san`
  prerequisite (`phases.py`); the sequencer runs `sandeb.ensure_san_debs()` before
  the pcmk provision playbook, `apt-get download`-ing any missing deb into the
  cache. It runs **only for a stack that has SAN targets** (`stack_san_targets`), so
  the RDQM and Native-HA stacks skip it entirely. The `linux-modules-extra` deb is
  keyed to the **controller's running kernel** — the best host-side proxy for the
  base box's kernel without booting it.
- **Consume (target side).** The `drbd-san` and `iscsi-target` roles look up their
  packages in `san_media_dir` (default `…/build/cache/san-debs`), copy any cached
  deb to the target, and `apt install` it offline.

## The network fallback (self-healing)

Pre-caching is **best-effort, never load-bearing.** Anything absent from the cache
falls back to a normal network `apt install <name>` on the target, so the path
self-heals:

- If a deb cannot be pre-fetched, `ensure_san_debs` reports it loudly and records
  it as `unavailable` (it does **not** abort the bootstrap — unlike the MQ tarball,
  which has no fallback).
- The kernel-coupled `linux-modules-extra` is the one that drifts: when the base
  box ships a newer kernel than the cached deb, the role's exact-kernel lookup
  misses and the package installs from the network. A subsequent bootstrap
  re-caches the new kernel's deb under its own name (the cache is keyed by kernel),
  restoring the fast path automatically.

One nuance: only the three named debs are cached, not their full dependency
closure. `linux-modules-extra`'s sole dependency (the matching `linux-image`) is
already installed on the base box, so it installs fully offline. `targetcli-fb`'s
small Python dependencies (e.g. `python3-rtslib-fb`) are not cached and resolve
from the network on a from-scratch box — a few MB, versus the ~100 MB the
`linux-modules-extra` pre-cache eliminates.
