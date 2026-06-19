# build/ reorganization — cache / state / work / temp buckets

- **Issue:** #286
- **Date:** 2026-06-19
- **Status:** design (brainstormed; pending implementation plan)
- **Blocks / sequences with:** #276 (x86 host portability). #276 is built but not
  merged; it touches `paths.py` and renders `build/lab/topology.resolved.yaml`.
  This work lands **first**; #276 then rebases onto it (its
  `resolved_topology_path` points at `work/`, and its `_prepare_lab` gains a
  `build ensure` call). The worktree fragility this spec removes is precisely what
  blocks #276's cold-rebuild acceptance.

## 1. Problem

`build/` (the gitignored, host-mounted working tree) has accreted into ~12.5 GB of
intermixed, unclassified content with **no keep-vs-nuke boundary**:

- re-fetchable downloads (`mq/` tarballs, `refs/`, `ansible_collections/`);
- irreplaceable live-lab state (`snapshots/` 22 GB, the licensed `rhel-…-dvd.iso`
  12 GB, built `boxes/`, `secrets/` + `fence_key*` + `*.env`);
- auto-generated renders (`inventory.ini`, `grafana/`, `prometheus/`, `obs/`,
  `salt/`, `box-versions.json`, `versions.json`, manifest overlays);
- stray junk (7× `Screenshot*.png`, `epic-body.md`, a build log, an empty
  `research/`).

Two structural faults underlie it:

1. **No keep/nuke boundary.** All of the above sit at `build/`'s top level, so a
   "nuke and start over" cold rebuild can't tell what to drop (stale renders
   survive) from what to preserve (downloads, snapshots) — rebuilds are not
   reproducible.
2. **No single authority.** `build/` subpaths are hardcoded across **~37 files** —
   8 Python modules (`paths`, `cli`, `dashboard`, `inventory`, `clusterboard`,
   `scrape`, `roster`, `runreport`), **`ansible/ansible.cfg`** (the inventory
   path), ~8 ansible playbooks, several roles, and ~8 `lab/scripts/` shell scripts
   (snapshot/restore/secret/box-build/dr). The layout grew by accretion with no
   owner. (`grep -rl "build/" src/ ansible/ lab/ scripts/` is the authoritative
   list the plan must work from — see §4.)

A direct consequence: **operating the lab from a git worktree requires fragile,
hand-wired symlinks** (there is one physical lab, so its state must be shared
across checkouts), and that fragility blocks the #276 cold-rebuild acceptance.

## 2. Decisions

- **D1 — Four top-level buckets, nothing else.** `build/` contains exactly
  `cache/ state/ work/ temp/` (plus bucket `.gitkeep`s). Anything at `build/`'s
  root that is not one of these is treated as disposable.
- **D2 — Fixed keep/nuke/share semantics per bucket** (§3). The bucket a thing
  lives in *is* its lifecycle contract.
- **D3 — One physical lab → `cache/` and `state/` are shared; `work/` and `temp/`
  are local.** In a worktree, `cache/` and `state/` symlink back to the main
  checkout; `work/` and `temp/` are real local dirs.
- **D4 — `build/` stays the home for all of this** (host-mounted, survives a VM
  rebuild for free). No second host mount, no `~/.cache` (that lives in the
  ephemeral VM and dies on rebuild).
- **D5 — `paths.py` is the single layout authority for Python; `mqlab build path
  <bucket>` is the single resolver for everything else.** Python routes every
  `build/` access through `paths.py`; the *many* non-Python consumers (Vagrantfile,
  ansible + `ansible.cfg`, and the `lab/scripts/` + `fetch-mq.sh` shell scripts)
  resolve bucket locations by calling `mqlab build path <bucket>` rather than
  re-deriving paths themselves (see §5; this folds in the existing
  `git-common-dir` main-resolution already duplicated across `lab-snapshot.sh` /
  `lab-restore.sh` / `build-box.sh`).
- **D6 — Wiring is an idempotent `mqlab build ensure`, hooked directly into the
  lab-driving verbs** (`vm create`/`vm up`/`obs up`/`vm ssh`). `#286` adds this
  hook itself and does **not** depend on #276's `_prepare_lab` (which is on an
  unmerged branch). When #276 later rebases on top, its `_prepare_lab` simply
  absorbs the `build ensure` call (order: `build ensure` → host/KVM checks →
  render `work/`). No magic path-getter side effects; no forgotten manual setup.

### 2.1 Non-goals

- Changing *what* artifacts the lab produces — only *where* they live and how they
  are wired/cleaned.
- Reducing the size of `snapshots/`/ISO, or changing the snapshot/restore feature.
- A second host mount or an out-of-`build/` cache location (rejected in D4).

## 3. The four-bucket model & semantics

```
build/
  cache/   # shared · re-fetchable downloads
    mq/   refs/   ansible_collections/   ibm-sysreq.pdf
  state/   # shared · facts that must persist for the live lab's lifetime
    iso/   snapshots/   boxes/   (rhel96-box build workdir)
    secrets/   fence_key*   *.env
    manifests/<setup>.yaml          # manifest selection pins
    runs/   reports/   dr-runs/     # the lab's audit trail
  work/    # LOCAL · pure renders (regenerated in seconds)
    inventory.ini   lab/topology.resolved.yaml   box-versions.json   versions.json
    manifests/<setup>.overlay.json  _obs.overlay.json
    grafana/   prometheus/   obs/   salt/
  temp/    # LOCAL · scratch / junk / host↔agent handoff (screenshots)
```

| Bucket | Re-creatable? | On cold rebuild | Worktree |
|--------|---------------|-----------------|----------|
| `cache/` | yes (re-download, slow) | **keep** (drop only on `clean --cache` / ground-up) | **shared** → symlink to main |
| `state/` | mixed (see below) | **keep** (drop only on guarded `clean --state`) | **shared** → symlink to main |
| `work/` | yes (seconds) | **nuke** every rebuild | **local** |
| `temp/` | n/a | **nuke** on explicit clean only | **local** |

**The discriminator for any new artifact:** is it a deterministic render of
committed code + topology + host facts? → `work/`. A fact about the live lab that
cannot be re-derived from code? → `state/`. A download? → `cache/`. None of the
above / disposable? → `temp/`.

**Notable consequences:**

- `build/manifests/` **splits**: the selection pin `<setup>.yaml` → `state/`
  (it records what the live lab was built against and guards mid-lifecycle
  manifest swaps — only meaningful for the one shared lab); the rendered overlays
  → `work/`.
- `runs/`/`reports/`/`dr-runs/` are **shared `state/`** — one audit trail of the
  one lab, not per-checkout.
- `temp/` is the sanctioned home for the operator's screenshot→agent handoff
  (host-mounted, so a file dropped there is readable in the VM); the cleanup tool
  must **not** auto-purge `temp/` mid-session — only on an explicit `clean`.

**Why `state/` is kept — two distinct rationales** (this sharpens the
`clean --state` guard message):

- *Irreplaceable* — `iso/` (licensed media, not re-downloadable) and `snapshots/`
  (22 GB of captured lab state). Losing these is hours-to-impossible to recover.
- *Lifecycle-coupled* — `secrets/`/`*.env`/`fence_key*`, the `manifests/<setup>.yaml`
  selection pins, and built `boxes/`. These *regenerate* on a true ground-zero
  wipe (`lab-secret.sh` says so explicitly), but a running lab was provisioned
  *with these specific values*; regenerating them mid-life breaks the live
  cluster. They must persist as long as the lab exists.

The `clean --state` confirmation names what's actually at stake (irreplaceable
snapshots/ISO vs. a running lab's coupled credentials), not a blanket
"irreplaceable."

**Correctness fix, not just a relocation:** `state/` consumers today disagree on
where `build/` is — `lab-snapshot.sh`/`lab-restore.sh`/`build-box.sh` resolve the
**main** worktree's `build/` (via `git-common-dir`), but `lab-secret.sh` writes the
**local** repo-root `build/secrets`. So from a feature worktree, a lab's snapshots
land in main while its secrets land in the worktree — a latent mismatch (a QM
provisioned against worktree-local secrets won't line up with a main-tree restore).
After the reorg **all `state/` consumers resolve via the one shared resolver
(`mqlab build path state`, §5)**, so secrets and snapshots are consistently the
single lab's. This is a bug fixed by the reorg, verified in acceptance — not a
faithful move of the existing split.

**Cold-rebuild story (now honest):** normal rebuild nukes the VM + `work/` +
`temp/` (+ any stray non-bucket files in `build/` root), keeps `cache/`+`state/`.
True ground-up additionally drops `cache/`. `state/` only goes behind an explicit,
guarded flag (it holds the 22 GB of snapshots and the licensed ISO).

## 4. The path authority (`paths.py`)

`paths.py` owns the layout. Bucket primitives plus named-artifact helpers:

```python
def build_root() -> Path:              # repo_root()/"build"
def cache(*parts: str) -> Path:        # build/cache/...
def state(*parts: str) -> Path:        # build/state/...
def work(*parts: str) -> Path:         # build/work/...
def temp_dir() -> Path:                # build/temp

# named artifacts move into their bucket; callers stop spelling "build/…":
def inventory_path() -> Path:          # work("inventory.ini")
def resolved_topology_path() -> Path:  # work("lab", "topology.resolved.yaml")
def box_versions_path() -> Path:       # work("box-versions.json")
def mq_cache_dir() -> Path:            # cache("mq")
def selection_state_path(setup) -> Path:  # state("manifests", f"{setup}.yaml")
def runs_dir() -> Path:                # state("runs")
def reports_dir() -> Path:             # state("reports")
```

The eight modules ask `paths` for a location instead of concatenating
`repo_root()/"build"/…`. After this, moving a bucket is a one-line change and a
write outside a bucket is obvious in review.

**The ripple — artifacts physically move into buckets, and EVERY consumer must be
updated.** This is not the ~5-item hand list an earlier draft implied: **`grep -rl
"build/" src/ ansible/ lab/ scripts/` is the authoritative work-list (~37 files)**,
and the plan updates every one. The categories:

- **Python (8 modules)** → via `paths.py` helpers.
- **`ansible/ansible.cfg`** → `inventory = ../build/work/inventory.ini`. **This one
  line is make-or-break** — miss it and every playbook loads no hosts.
- **Ansible playbooks + roles** (`site-pcmk*.yml`, `observability.yml`,
  `site-pki.yml`, `site-nativeha-spike.yml`, `gather-versions.yml`,
  `mq-install`/`mq-client`/`rdqm-install`, obs roles, `pcmk-stonith`, …) → bucket
  paths: `../build/cache/mq/…`, `../build/work/{inventory.ini,prometheus,grafana}/…`.
- **`lab/scripts/` + `scripts/`** (`lab-snapshot.sh`, `lab-restore.sh`,
  `lab-secret.sh`, `build-box.sh`, `dr-provision.sh`, `nativeha-fault-suite.sh`,
  `pcmk-dr-force.sh`, `fetch-mq.sh`) → resolve buckets via `mqlab build path
  <bucket>` (below), not hand-rolled paths.
- **Vagrantfile (Ruby)** → reads `../build/work/lab/topology.resolved.yaml` and
  `../build/work/box-versions.json`.

**Acceptance backstop:** after the migration, `grep -rn "build/" src/ ansible/ lab/
scripts/` shows no bare pre-bucket path (`build/mq`, `build/inventory.ini`, …) — only
bucket-qualified paths or `mqlab build path` calls.

**The non-Python resolver — `mqlab build path <bucket>`.** The shell scripts and
non-Python consumers must NOT each re-derive `build/`'s location. Three of them
(`lab-snapshot.sh`, `lab-restore.sh`, `build-box.sh`) already re-implement
`git-common-dir` main-resolution independently (citing #57) — exactly the drift this
reorg eliminates. So the tool exposes `mqlab build path cache|state|work|temp`,
which prints the correct absolute bucket path (main-resolved for `cache`/`state`,
local for `work`/`temp`). Shell scripts call it; the three existing main-resolvers
are folded into it. One authority for Python (`paths.py`) and non-Python (`build
path`), sharing the same resolution logic in `buildenv.py`.

## 5. The tool — `mqlab build`

A new command group backed by a focused, unit-testable `buildenv.py` (injected
filesystem root + git-dir resolver):

- **`mqlab build ensure`** — idempotent wiring:
  - creates `build/{cache,state,work,temp}` if missing (each with `.gitkeep`);
  - detects a linked worktree by comparing `git rev-parse --git-dir` vs
    `--git-common-dir`; if linked, **symlinks `cache/` and `state/` back to the
    main checkout's `build/`** (main resolved from the common-dir's parent),
    creating main's buckets first if needed; `work/` and `temp/` are always real
    local dirs;
  - never clobbers a correct dir/symlink;
  - **fails loud** if it is a linked worktree but main is unresolvable (rather than
    silently creating a local `cache/` that re-downloads).
- **`mqlab build path cache|state|work|temp`** — prints the resolved absolute
  bucket path (main-resolved for `cache`/`state`, local for `work`/`temp`); the
  one resolver every non-Python consumer calls (D5).
- **Hooked directly into the lab-driving verbs** (`vm create`/`vm up`/`obs up`/`vm
  ssh`) — `#286` adds this call itself, independent of #276. It runs first:
  `build ensure` → (later, after #276 rebases) host/KVM checks → render `work/`.
  Also runnable by hand.
- **`mqlab build clean`** — nukes `work/` + `temp/` (+ stray non-bucket files in
  `build/` root). The post-`vrg-vm rebuild` reset; `cache/`+`state/` survive.
- **`mqlab build clean --cache`** — additionally drops `cache/` (ground-up).
- **`mqlab build clean --state`** — guarded path to drop `state/`; requires a typed
  confirmation whose message names what's at stake: *irreplaceable* snapshots/ISO
  vs. a running lab's *lifecycle-coupled* secrets/pins (§3).
- **`mqlab build status`** — per-bucket size + whether `cache/`/`state/` are real
  (main) or symlinks (worktree). Answers "what's in there, and is it shared?".
- **`mqlab build migrate`** — one-time, idempotent, `--dry-run`. Moves existing
  top-level `build/` contents into buckets (§6). Because `build/` is one
  filesystem, the 22 GB `snapshots/` and 12 GB ISO relocate by rename (instant),
  which is safer and reproducible vs. hand-moving 34 GB.

## 6. Migration mapping (`mqlab build migrate`)

| From (today) | To |
|--------------|----|
| `mq/`, `refs/`, `ansible_collections/`, `*SystemRequirements*.pdf` | `cache/` |
| `rhel-*-dvd.iso` | `state/iso/` |
| `snapshots/`, `boxes/`, `rhel96-box/`, `rhel96-box-build.log` | `state/` |
| `secrets/`, `fence_key*`, `*.env` | `state/secrets/` (env files alongside) |
| `runs/`, `reports/`, `dr-runs/` | `state/` |
| `manifests/<setup>.yaml` | `state/manifests/` |
| `inventory.ini`, `lab/`, `box-versions.json`, `versions.json`, `grafana/`, `prometheus/`, `obs/`, `salt/`, `manifests/*.overlay.json` | `work/` |
| `Screenshot*.png`, `epic-body.md`, stray files | `temp/` |
| empty `research/` | delete |

`.gitignore` stays ignore-all under `build/` (`build/*` + `!build/.gitkeep`);
`ensure` lays down the bucket dirs with `.gitkeep`s so the structure
self-documents. The convention is documented in the `CLAUDE.md` "Development
environment" section (four buckets + keep/nuke/share + `temp/` role) and a short
`docs/` reference the non-Python consumers point at.

## 7. Error handling

Fail loud, no silent fallback (existing `InventoryError`/`StepFailedError` idiom):

- linked worktree but main checkout unresolvable → `ensure` hard-stops;
- `clean --state` without the typed confirmation → refuse;
- `migrate` collision (a destination already exists with different content) →
  stop and report, never overwrite irreplaceable state;
- a non-bucket path requested from `paths.py` → there is no such API (the only way
  to address `build/` is via a bucket helper).

## 8. Testing & acceptance

- **`buildenv` unit tests** (injected fs root + fake git resolver): worktree
  detection; main-build resolution; `ensure` creates buckets / symlinks
  `cache`+`state` in a worktree / no-ops when correct / **fails loud** when main is
  unresolvable; `clean` nukes `work`+`temp` only; `clean --cache`; guarded
  `clean --state`; `migrate` moves the old layout into buckets, idempotent,
  `--dry-run` reports without moving.
- **`paths.py` tests**: each helper returns its bucket-qualified path.
- **Regression**: update fixtures/consumers that referenced `build/inventory.ini`
  etc. to the new `work/` paths; full suite green at 100% branch coverage.
- **Grep backstop (consumer completeness)**: `grep -rn "build/" src/ ansible/ lab/
  scripts/` shows no bare pre-bucket path (`build/mq`, `build/inventory.ini`,
  `build/snapshots`, …) — only bucket-qualified paths or `mqlab build path` calls.
  This is the check that proves all ~37 consumers were updated.
- **Acceptance (cold-rebuild gate, arm64 — human-run):** `vrg-vm rebuild` →
  `mqlab build migrate` (once) → `mqlab build clean` → lab bring-up one-pass,
  proving `cache/`+`state/` survived and `work/` regenerated; plus a worktree
  `mqlab build ensure` wiring the symlinks and operating the shared lab. This
  harness is also exactly what unblocks the #276 acceptance.
- **Acceptance (state consistency, the Issue-3 fix)**: from a feature worktree,
  `mqlab build path state` and the secrets/snapshot scripts all resolve the **same**
  main-tree `state/` — i.e. a lab provisioned, snapshotted, and restored from a
  worktree keeps its credentials consistent (the latent secrets-local /
  snapshots-main split is gone).

## 9. Open items for the implementation plan

- Exact `buildenv.py` API (function names, the injected-resolver seam).
- Whether `secrets/`/`*.env` sit at `state/` root or under `state/secrets/`
  (mapping in §6 assumes `state/secrets/`).
- The `docs/` reference path for the non-Python bucket layout.
- Decide `mqlab build` subcommand help text / confirmation UX for `--state`.
- Run the full `grep -rl "build/" src/ ansible/ lab/ scripts/` enumeration (~37
  files) into the plan as the authoritative consumer work-list, and confirm none
  is missed (grep backstop, §8).
