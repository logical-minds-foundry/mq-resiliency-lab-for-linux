# Docs site + architecture documentation — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a coherent published documentation site (the existing
mkdocs + `cd-docs` pipeline) whose centerpiece is an Architecture page that
walks the lab top to bottom through a set of iframe-embedded HTML diagrams.

**Architecture:** Config + content only — no toolchain changes (the CI/CD
container provides mkdocs/mike). We align `docs/site/mkdocs.yml` with the
Vergil template, wire the docs build into CI so PRs catch breakage, author
the Home / Getting Started / Architecture / Design & Specs pages, and add
five self-contained HTML diagrams grounded in `lab/topology.yaml`.

**Tech Stack:** MkDocs + Material theme, `mike` (versioned deploy),
`vrg-docs-stage` / `vrg-docs-patch-nav` (changelog/release-notes
generation), GitHub Actions (`ci-docs.yml@v2.1`, `cd-docs.yml@v2.1`),
`vrg-container-run` for in-container local builds.

**Spec:** `docs/specs/2026-06-08-docs-site-and-architecture-design.md`

**Working location:** worktree
`.worktrees/issue-33-docs-site-architecture/` on branch
`feature/33-docs-site-architecture`. Run every command from inside the
worktree; use `vrg-git` / `vrg-commit`, never raw `git`.

---

## Conventions used in every task

**The local docs build is our "test".** There are no unit tests for
markdown; the equivalent red/green signal is a strict MkDocs build that
mirrors CI exactly. The canonical recipe (confirmed empirically in Task 1):

```bash
# Run from the worktree root. Builds docs the way CI does.
vrg-container-run -- bash -c '
  set -euo pipefail
  vrg-docs-stage --docs-dir docs/site/docs
  vrg-docs-patch-nav --mkdocs-yml docs/site/mkdocs.yml --releases-dir docs/site/docs/releases
  uv run mkdocs build --strict -f docs/site/mkdocs.yml
'
```

`mkdocs --strict` turns every warning (broken link, page not in nav,
missing reference) into a build failure — that is the safety net.

**After each local build, clean the generated/throwaway artifacts** so they
never get committed (Task 1 adds them to `.gitignore`; `mkdocs.yml` is
tracked, so it is restored rather than deleted):

```bash
vrg-git restore docs/site/mkdocs.yml            # undo patch-nav's in-place edit
rm -rf docs/site/docs/changelog.md docs/site/docs/releases docs/site/site
```

**Visual verification of diagrams** is done by opening the standalone
`.html` file in a browser (the diagrams are self-contained) — the strict
build does not validate iframe contents.

---

## File structure

Created/modified in this plan (paths relative to the worktree root):

| Path | Responsibility |
|------|----------------|
| `docs/site/mkdocs.yml` | Site config + nav. Aligned to the Vergil template; gains the `Releases` block and the new pages. |
| `.github/workflows/ci.yml` | Gains a `docs` job calling `ci-docs.yml@v2.1` so PRs verify the strict build. |
| `.gitignore` | Ignore generated docs artifacts (`changelog.md`, `releases/`, `site/`). |
| `docs/site/docs/index.md` | Home / orientation. |
| `docs/site/docs/getting-started.md` | Nothing → a running stack. |
| `docs/site/docs/design-and-specs.md` | Annotated index into `docs/specs/` + findings. |
| `docs/site/docs/architecture/index.md` | The centerpiece walkthrough; embeds the five diagrams. |
| `docs/site/docs/architecture/diagrams/_diagram.css` | Shared CSS vocabulary for all diagrams. |
| `docs/site/docs/architecture/diagrams/01-host-and-vms.html` | Diagram 1 — macOS host + identity VMs. |
| `docs/site/docs/architecture/diagrams/02-inside-lab-vm.html` | Diagram 2 — nested virt + network fabric + node fleet. |
| `docs/site/docs/architecture/diagrams/03-rdqm-arm.html` | Diagram 3 — RDQM 3+3 HA/DR (refresh of the existing diagram, as-built). |
| `docs/site/docs/architecture/diagrams/04-standalone-qm.html` | Diagram 4 — Phase B standalone QM message path. |
| `docs/site/docs/architecture/diagrams/05-pacemaker-san.html` | Diagram 5 — Phase D Pacemaker/SAN arm. |

---

## Table of contents

- [Task 1: Spike — establish the docs build harness + Releases plumbing](#task-1-spike--establish-the-docs-build-harness--releases-plumbing)
- [Task 2: Wire the docs build into CI](#task-2-wire-the-docs-build-into-ci)
- [Task 3: Home page](#task-3-home-page)
- [Task 4: Getting Started page](#task-4-getting-started-page)
- [Task 5: Architecture page scaffold + shared diagram CSS](#task-5-architecture-page-scaffold--shared-diagram-css)
- [Task 6: Diagram 1 — host & identity VMs](#task-6-diagram-1--host--identity-vms)
- [Task 7: Diagram 2 — inside the lab VM](#task-7-diagram-2--inside-the-lab-vm)
- [Task 8: Diagram 3 — RDQM arm (refresh existing)](#task-8-diagram-3--rdqm-arm-refresh-existing)
- [Task 9: Diagram 4 — standalone QM arm](#task-9-diagram-4--standalone-qm-arm)
- [Task 10: Diagram 5 — Pacemaker/SAN arm](#task-10-diagram-5--pacemakersan-arm)
- [Task 11: Design & Specs page](#task-11-design--specs-page)
- [Task 12: Full-site verification + PR](#task-12-full-site-verification--pr)

---

## Task 1: Spike — establish the docs build harness + Releases plumbing

**Why first:** Everything downstream depends on a reproducible local strict
build that matches CI. We prove the build harness against the *current stub
site first*, capture the exact working command sequence, then leave the site
in a green, template-aligned state. Spike-first, fail-loud.

**Files:**
- Modify: `docs/site/mkdocs.yml`
- Modify: `.gitignore`

- [ ] **Step 1: Confirm the build harness works on the current site (red/green baseline)**

Run from the worktree root:

```bash
vrg-container-run -- bash -c '
  set -euo pipefail
  vrg-docs-stage --docs-dir docs/site/docs
  vrg-docs-patch-nav --mkdocs-yml docs/site/mkdocs.yml --releases-dir docs/site/docs/releases
  uv run mkdocs build --strict -f docs/site/mkdocs.yml
'
```

Expected: build succeeds (`INFO - Documentation built in …`). Capture two
facts for reuse: (a) whether `vrg-docs-patch-nav` modified `mkdocs.yml`
(`vrg-git status`), and (b) which files `vrg-docs-stage` generated under
`docs/site/docs/` (expected: `changelog.md`, `releases/`).

If `vrg-docs-stage`/`vrg-docs-patch-nav`/`uv run mkdocs` are not found in
the container, STOP and report — the toolchain assumption is wrong and the
rest of the plan must be revised (do not paper over it).

- [ ] **Step 2: Clean the generated artifacts**

```bash
vrg-git restore docs/site/mkdocs.yml
rm -rf docs/site/docs/changelog.md docs/site/docs/releases docs/site/site
```

Run `vrg-git status` — the tree must be clean.

- [ ] **Step 3: Add generated-artifact ignores to `.gitignore`**

Append to `.gitignore` (after the "Test/coverage artifacts" block):

```gitignore
# Generated docs artifacts — produced at build time by vrg-docs-stage and
# mkdocs; never committed (CI regenerates them from git history each run).
docs/site/site/
docs/site/docs/changelog.md
docs/site/docs/releases/
```

- [ ] **Step 4: Align `docs/site/mkdocs.yml` with the Vergil template + add the Releases/nav blocks**

Replace the whole file with:

```yaml
site_name: "mq-cluster-tooling"
site_description: "Reproducible nested-virtualization IBM MQ cluster lab for exercising HA and DR topologies"
repo_url: https://github.com/logical-minds-foundry/mq-cluster-tooling
repo_name: logical-minds-foundry/mq-cluster-tooling
docs_dir: docs
strict: true
edit_uri: ""

extra:
  version:
    provider: mike

theme:
  name: material
  palette:
    - media: "(prefers-color-scheme: light)"
      scheme: default
      primary: indigo
      accent: indigo
      toggle:
        icon: material/brightness-7
        name: Switch to dark mode
    - media: "(prefers-color-scheme: dark)"
      scheme: slate
      primary: indigo
      accent: indigo
      toggle:
        icon: material/brightness-4
        name: Switch to light mode
  features:
    - navigation.tabs
    - navigation.sections
    - navigation.indexes
    - navigation.top
    - content.code.copy
    - search.highlight
    - search.suggest

plugins:
  - search

markdown_extensions:
  - admonition
  - pymdownx.details
  - pymdownx.highlight:
      anchor_linenums: true
  - pymdownx.superfences
  - pymdownx.tabbed:
      alternate_style: true
  - pymdownx.snippets
  - tables
  - toc:
      permalink: true

nav:
  - Home: index.md
  - Releases:
      - Changelog: changelog.md
      - Release Notes:
          - releases/index.md
  - Getting Started: getting-started.md
  - Architecture: architecture/index.md
  - Design & Specs: design-and-specs.md
```

- [ ] **Step 5: Verify the template-aligned site still builds strict**

Run the canonical recipe from "Conventions" above. Expected: success. The
nav now references `architecture/index.md` and `design-and-specs.md`, which
do not exist yet — so this build is **expected to FAIL** with
"page not found" for those two entries. That failure is the proof that
`strict` is enforcing our nav. Note it and proceed (Tasks 3–5/11 create the
missing pages; the build goes green again in Task 12). To get an interim
green signal now, temporarily comment out the `Architecture` and
`Design & Specs` nav lines, rebuild (expect success), then restore them.

- [ ] **Step 6: Clean + commit**

```bash
vrg-git restore docs/site/mkdocs.yml   # only if patch-nav modified it
rm -rf docs/site/docs/changelog.md docs/site/docs/releases docs/site/site
vrg-git add docs/site/mkdocs.yml .gitignore
vrg-commit --type docs --scope docs --message "align mkdocs config with template; add Releases nav + artifact ignores"
```

---

## Task 2: Wire the docs build into CI

**Why:** `cd.yml` deploys docs on push to `develop`/`main` (post-merge),
but `ci.yml` has no docs job — so a PR with broken docs passes CI and only
fails after merge. Adding `ci-docs` makes the strict build a PR gate.

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add a `docs` job to `ci.yml`**

Append this job to the `jobs:` map in `.github/workflows/ci.yml` (sibling of
`audit`, `quality`, etc.):

```yaml
  docs:
    uses: vergil-project/vergil-actions/.github/workflows/ci-docs.yml@v2.1
    with:
      container-tag: '3.12'
      container-suffix: python
```

(If `ci-docs.yml@v2.1` does not accept `container-tag`/`container-suffix`,
fall back to no `with:` block — the workflow defaults to
`docs/site/mkdocs.yml` and the prod container. Confirm by checking the
workflow's `inputs` in `vergil-actions`.)

- [ ] **Step 2: Validate the workflow file parses**

```bash
vrg-container-run -- uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml OK')"
```

Expected: `ci.yml OK`.

- [ ] **Step 3: Commit**

```bash
vrg-git add .github/workflows/ci.yml
vrg-commit --type ci --scope docs --message "gate PRs on the strict docs build (ci-docs)"
```

---

## Task 3: Home page

**Files:**
- Modify: `docs/site/docs/index.md`

- [ ] **Step 1: Write the Home page**

Replace `docs/site/docs/index.md` with:

```markdown
# mq-cluster-tooling

A reproducible, **nested-virtualization IBM MQ cluster lab** for exercising
high-availability (HA) and disaster-recovery (DR) topologies end to end —
from the host machine down to running queue managers and a live message
path.

The whole lab is declared as code and rebuilt from scratch on demand: a
[Vergil](https://github.com/vergil-project) VM provides the host
environment, a single `topology.yaml` describes the guest fleet and its
networks, and Ansible plus a declarative content plane bring the MQ services
up. Nothing is hand-built; everything is repeatable.

## What you can do here

- Stand up a standalone queue manager and prove a message path survives a
  reboot.
- Form a 3-node **RDQM** HA group with synchronous replication and
  automatic failover, then drive an asynchronous DR cutover to a second site.
- Compare storage-replicated HA (RDQM) against shared-SAN HA
  (Pacemaker) on the same network fabric.

## Start here

- **[Getting Started](getting-started.md)** — build the environment and
  bring up your first running stack.
- **[Architecture](architecture/index.md)** — the layered walkthrough of
  how the whole lab fits together, with diagrams.
- **[Design & Specs](design-and-specs.md)** — how the lab was engineered.
```

- [ ] **Step 2: Build strict (temporarily scoping nav)**

Because `architecture/` and `design-and-specs.md` still do not exist,
verify just this page builds by running the canonical recipe with the
`Architecture` and `Design & Specs` nav lines temporarily commented out.
Expected: success. Restore the nav lines and clean artifacts afterward.

- [ ] **Step 3: Commit**

```bash
vrg-git add docs/site/docs/index.md
vrg-commit --type docs --scope docs --message "write the Home / orientation page"
```

---

## Task 4: Getting Started page

**Files:**
- Modify: `docs/site/docs/getting-started.md`

- [ ] **Step 1: Write the Getting Started page**

Replace `docs/site/docs/getting-started.md` with:

```markdown
# Getting Started

This page takes you from nothing to a running MQ stack. It stays at
orientation altitude and points to the real commands; per-arm operational
detail lives in the design spec and (later) the Operations section.

## 1. Build and enter the lab VM

The lab runs inside an ephemeral Vergil VM declared by
[`vergil.toml`](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/main/vergil.toml)
(the `[vm.vergil-user]` profile). It is 100% reproducible — rebuild it
freely.

```bash
vrg-vm create logical-minds-foundry/mq-cluster-tooling --identity vergil-user
vrg-vm session logical-minds-foundry/mq-cluster-tooling --identity vergil-user
```

All working state lives under the gitignored `build/` directory, mounted
from the host. Nothing else in the VM is precious.

## 2. Bring up the network fabric and a node set

The lab's shape is a single source of truth:
[`lab/topology.yaml`](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/main/lab/topology.yaml).
It defines the libvirt networks (data, heartbeat, WAN, client, DTCC, SAN)
and every guest's NICs and platform. The Vagrant/libvirt harness reads it
to create networks and boot nodes.

See the [Architecture](architecture/index.md) walkthrough for what each
network is for and why the fleet is shaped the way it is.

## 3. Stand up one stack end to end

The **standalone queue manager** path (Phase B) is the simplest proof that
the stack works: a single queue manager, a simulated upstream
(`dtcc-sim`), and an application client exchanging messages over the client
network. Bring it up, send a message, and confirm it survives a guest
reboot.

From there, the [Architecture](architecture/index.md) page walks the
higher-order arms: the RDQM 3+3 HA/DR cluster and the Pacemaker/SAN
alternative.

## Building these docs locally

The site builds the same way CI does, inside the project container:

```bash
vrg-container-run -- bash -c '
  vrg-docs-stage --docs-dir docs/site/docs &&
  vrg-docs-patch-nav --mkdocs-yml docs/site/mkdocs.yml --releases-dir docs/site/docs/releases &&
  uv run mkdocs build --strict -f docs/site/mkdocs.yml
'
```
```

- [ ] **Step 2: Build strict** (same temporary-nav-scoping approach as Task 3, Step 2). Expected: success.

- [ ] **Step 3: Commit**

```bash
vrg-git add docs/site/docs/getting-started.md
vrg-commit --type docs --scope docs --message "write the Getting Started page"
```

---

## Task 5: Architecture page scaffold + shared diagram CSS

**Why before the diagrams:** establishes the page that embeds them and the
shared style vocabulary, so each diagram task is purely "author HTML + embed
+ verify". The page is created with all five embed slots, each pointing at a
diagram file created in Tasks 6–10. Until those exist the iframes 404 at
runtime but the **strict build still passes** (MkDocs does not validate
iframe `src`; `.html` files are copied as static assets, not nav pages).

**Files:**
- Create: `docs/site/docs/architecture/index.md`
- Create: `docs/site/docs/architecture/diagrams/_diagram.css`

- [ ] **Step 1: Create the shared diagram CSS**

This is the style vocabulary lifted from the existing diagram so all five
read as a family. Create
`docs/site/docs/architecture/diagrams/_diagram.css`:

```css
:root { color-scheme: dark; }
body { background:#11151a; color:#e6e8eb; font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
       margin:0; padding:24px 28px; line-height:1.45; }
h2 { margin:0 0 4px 0; }
.subtitle { opacity:.8; margin:0 0 8px 0; font-size:14px; }
.wrap { display:flex; flex-direction:column; gap:20px; margin-top:10px; }
.panel { border:2px solid #888; border-radius:12px; padding:14px 16px; }
.panel h3 { margin:0 0 4px 0; font-size:16px; }
.panel .sub { font-size:12px; opacity:.75; margin:0 0 12px 0; }
.row { display:flex; gap:14px; align-items:stretch; flex-wrap:wrap; }
.box { flex:1; min-width:160px; border:2px solid #5a7fb0; border-radius:12px; padding:12px; background:rgba(74,144,217,0.05); }
.box.b { border-color:#b08a5a; background:rgba(176,138,90,0.05); }
.box h4 { margin:0 0 8px 0; font-size:14px; letter-spacing:.03em; }
.group { border:1px dashed #4ade80; border-radius:9px; padding:8px; margin:6px 0; background:rgba(74,222,128,0.06); }
.group.dr { border-color:#d98a4a; border-style:dotted; background:rgba(217,138,74,0.07); }
.group .glabel { font-size:11px; text-transform:uppercase; letter-spacing:.05em; opacity:.85; margin-bottom:6px; }
.nodes { display:flex; gap:8px; flex-wrap:wrap; }
.node { border:1px solid #aaa; border-radius:7px; padding:6px 9px; min-width:62px; text-align:center;
        background:rgba(255,255,255,0.05); font-family:ui-monospace,monospace; font-size:12px; }
.node.active { border-color:#4ade80; box-shadow:0 0 0 1px #4ade80 inset; }
.wanlink { display:flex; flex-direction:column; align-items:center; justify-content:center; min-width:130px; text-align:center; }
.wanlink .arrow { font-size:20px; }
.wanlink small { font-size:11px; opacity:.85; }
.net { font-family:ui-monospace,monospace; font-size:11px; padding:2px 7px; border-radius:5px; display:inline-block; margin:2px 4px 2px 0; }
.net-priv { background:#143b2e; border:1px solid #3fae7a; }
.net-wan  { background:#52331f; border:1px solid #d98a4a; }
.net-data { background:#1b2c4a; border:1px solid #5a7fb0; }
.net-san  { background:#3a234a; border:1px solid #9a6fb0; }
.callout { border-left:4px solid #e05a5a; background:rgba(224,90,90,0.08); padding:10px 14px; border-radius:6px; font-size:13px; }
.callout b { color:#ff8a8a; }
.ok { border-left:4px solid #4ade80; background:rgba(74,222,128,0.07); padding:10px 14px; border-radius:6px; font-size:13px; }
.tag { font-size:10px; opacity:.7; }
code { background:rgba(255,255,255,0.08); padding:1px 4px; border-radius:4px; }
```

- [ ] **Step 2: Create the Architecture page with prose + five embed slots**

Create `docs/site/docs/architecture/index.md`. Each diagram is embedded
with a fixed-height iframe; heights are starting values to tune visually in
the diagram tasks.

```markdown
# Architecture

This page walks the lab from the outside in: the host machine, the virtual
machines on it, the network fabric and node fleet inside the lab VM, and
finally the three MQ-service arms built on top. Everything shown here is
**as-built** and traces back to
[`lab/topology.yaml`](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/main/lab/topology.yaml).

## Layer 1 — The host and its VMs

The lab lives on an Apple-silicon Mac. Vergil runs two identity VMs — a
`vergil-user` VM where work happens and a `vergil-audit` VM that reviews it
— plus the large lab VM ("the big special one") that hosts the entire MQ
cluster lab. Isolating the lab in its own VM is what makes it disposable and
reproducible.

<iframe src="diagrams/01-host-and-vms.html" style="width:100%;height:360px;border:0;border-radius:8px;" title="Host and identity VMs"></iframe>

## Layer 2 — Inside the lab VM

Inside the lab VM the lab is itself virtualized — nested virtualization
(Apple silicon → macOS Virtualization → Lima → KVM/TCG) runs the guest
fleet. Those guests sit on a fabric of isolated libvirt networks: per-site
data and heartbeat networks, a WAN that links the two sites, the
client/DTCC application networks, and the SAN networks. The networks are
designed to be **severable** so failures can be injected cleanly.

<iframe src="diagrams/02-inside-lab-vm.html" style="width:100%;height:520px;border:0;border-radius:8px;" title="Inside the lab VM"></iframe>

## Layer 3 — The RDQM arm (HA + DR building block)

The RDQM arm is the headline topology: a 3-node Replicated Data Queue
Manager HA group in Data Center A (synchronous replication, automatic
failover, RPO 0 inside the site) with an asynchronous DR relationship to a
matching 3-node group in Data Center B. HA never stretches across the WAN —
synchronous replication would tie every commit to inter-site latency and
turn a DC-to-DC partition into a cluster collapse; cross-site resilience is
always a separate, asynchronous DR relationship with a manual cutover.

<iframe src="diagrams/03-rdqm-arm.html" style="width:100%;height:480px;border:0;border-radius:8px;" title="RDQM HA/DR arm"></iframe>

## Layer 3 — The standalone QM arm (the message path)

The simplest arm proves the message path itself: a single queue manager
(`qm-main`) exchanging messages with a simulated upstream (`dtcc-sim`) and
an application client over the client network. This is the foundation the
HA/DR arms build on.

<iframe src="diagrams/04-standalone-qm.html" style="width:100%;height:320px;border:0;border-radius:8px;" title="Standalone QM message path"></iframe>

## Layer 3 — The Pacemaker/SAN arm (the HA contrast)

The Pacemaker arm provides the same single-site HA goal as RDQM but with a
different mechanism: shared SAN storage (`san-a`) fronted by a Pacemaker
cluster (`pcmk-a1..3`) instead of block-level replication. Running both on
the same fabric lets the lab compare replicated-storage HA against
shared-storage HA directly.

<iframe src="diagrams/05-pacemaker-san.html" style="width:100%;height:360px;border:0;border-radius:8px;" title="Pacemaker/SAN arm"></iframe>
```

- [ ] **Step 3: Build strict (full nav now resolvable except design-and-specs)**

Run the canonical recipe with the `Design & Specs` nav line temporarily
commented out (it is created in Task 11). Expected: success — the
architecture page exists and its iframes are not validated by the build.

- [ ] **Step 4: Commit**

```bash
vrg-git add docs/site/docs/architecture/index.md docs/site/docs/architecture/diagrams/_diagram.css
vrg-commit --type docs --scope docs --message "scaffold the Architecture page + shared diagram CSS"
```

---

## Task 6: Diagram 1 — host & identity VMs

**Files:**
- Create: `docs/site/docs/architecture/diagrams/01-host-and-vms.html`

**Data to encode (as-built):** outer box = "macOS host (Apple silicon)";
inside it three VM boxes — `vergil-user` (work), `vergil-audit` (review),
and the lab VM (labelled "lab VM — hosts the entire MQ cluster lab",
resourced per `vergil.toml`: 12 vCPU / 64 GiB / 300 GiB, `nested = true`).
Show that the lab VM is the drill-down target for Diagram 2.

- [ ] **Step 1: Author the diagram**

Self-contained HTML that links the shared CSS via
`<link rel="stylesheet" href="_diagram.css">`, then renders the host →
three-VM nesting using `.panel` / `.box` / `.tag` classes. Structure:

```html
<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Lab architecture — host & VMs</title>
<link rel="stylesheet" href="_diagram.css"/>
</head><body>
<h2>Layer 1 — the host and its VMs</h2>
<p class="subtitle">Apple-silicon Mac → two Vergil identity VMs + the lab VM.</p>
<div class="wrap">
  <div class="panel">
    <h3>macOS host (Apple silicon, M-series)</h3>
    <div class="row">
      <div class="box"><h4>vergil-user VM</h4><div class="sub">where work happens (this agent)</div></div>
      <div class="box"><h4>vergil-audit VM</h4><div class="sub">independent review identity</div></div>
      <div class="box b"><h4>lab VM — the MQ cluster lab</h4>
        <div class="sub">12 vCPU · 64 GiB · 300 GiB · nested virt on</div>
        <div class="tag">drill down → Layer 2</div>
      </div>
    </div>
  </div>
</div>
</body></html>
```

- [ ] **Step 2: Visually verify** — open the file in a browser; confirm the
three VMs render inside the host box and the lab VM is visually
distinguished. Tune the iframe height in `architecture/index.md` if needed.

- [ ] **Step 3: Build strict** (canonical recipe, `Design & Specs` line
still commented). Expected: success.

- [ ] **Step 4: Commit**

```bash
vrg-git add docs/site/docs/architecture/diagrams/01-host-and-vms.html docs/site/docs/architecture/index.md
vrg-commit --type docs --scope docs --message "add diagram 1 — host & identity VMs"
```

---

## Task 7: Diagram 2 — inside the lab VM

**Files:**
- Create: `docs/site/docs/architecture/diagrams/02-inside-lab-vm.html`

**Data to encode (from `lab/topology.yaml`, as-built):**

- Nested-virt stack (top band): `M-series → macOS Virtualization (vz) → Lima → KVM/TCG`.
- Network fabric (one tag per network, with role):
  - `net-data-a` (10.10.1.0/24) / `net-data-b` (10.10.2.0/24) — per-site data
  - `net-hb-a` (172.16.1.0/24) / `net-hb-b` (172.16.2.0/24) — per-site heartbeat
  - `net-wan` (10.99.0.0/24) — cross-site WAN (DR + severable link)
  - `net-dtcc` (10.20.0.0/24) — upstream/DTCC application net
  - `net-client` (10.30.0.0/24) — application client net
  - `net-san-a` (10.40.1.0/24) / `net-san-b` — shared-SAN nets (Phase D)
- Node fleet grouped by arm (names only, IPs optional): Phase A placeholders
  `node-a1..3` / `node-b1..3`; Phase B `qm-main`, `dtcc-sim`, `app-client`;
  Phase C `rdqm-a1..3` / `rdqm-b1..3`; Phase D `san-a`, `pcmk-a1..3`.

- [ ] **Step 1: Author the diagram** — self-contained HTML linking
`_diagram.css`. Layout: a top `.panel` for the nested-virt band; a second
`.panel` "Network fabric" listing the networks as `.net` tags grouped into
DC-A / DC-B / cross-site / application / SAN columns using `.row`/`.box`; a
third `.panel` "Node fleet" with one `.group` per arm containing `.node`
chips. Every network tag and node name must match `topology.yaml` exactly.

- [ ] **Step 2: Visually verify** — open in browser; cross-check every
network CIDR and node name against `lab/topology.yaml`. Tune iframe height.

- [ ] **Step 3: Build strict** (canonical recipe). Expected: success.

- [ ] **Step 4: Commit**

```bash
vrg-git add docs/site/docs/architecture/diagrams/02-inside-lab-vm.html docs/site/docs/architecture/index.md
vrg-commit --type docs --scope docs --message "add diagram 2 — inside the lab VM (nested virt + fabric + fleet)"
```

---

## Task 8: Diagram 3 — RDQM arm (refresh existing)

**Files:**
- Create: `docs/site/docs/architecture/diagrams/03-rdqm-arm.html`
- (Decision) Modify or leave: `docs/specs/diagrams/topology-rdqm-ha-dr.html`

**Source:** `docs/specs/diagrams/topology-rdqm-ha-dr.html`. We produce an
**as-built** version: keep panel ① (the HA + DR building block), **remove
the entire "Active/active vision" panel** (the second `.panel`, lines 93–132
of the source — the `<!-- Active/active vision -->` block), and convert the
open "1+1 vs 3+3" decision into the as-built statement (DC-B is a full
3-node group: 3+3).

- [ ] **Step 1: Create `03-rdqm-arm.html` from the source**

Copy the source file's `<head>`/`<style>` and panel ① verbatim, then:

1. Update `<title>` and `<h2>` to drop "+ active/active vision" → e.g.
   `RDQM arm — HA inside a site, async DR across the WAN`.
2. Delete the `<!-- Active/active vision -->` panel block entirely (source
   lines 93–132).
3. In panel ①, relabel nodes to the real names: `a1..a3` → `rdqm-a1..3`,
   `b1..b3` → `rdqm-b1..3`.
4. Replace the trailing "Decision point" sentence (source line 90) with an
   as-built statement:
   `Inside a DC: automatic HA failover, RPO 0 (synchronous). Between DCs:
   manual rdqmdr cutover, small async window. As built: DC-B is a full
   3-node HA group (3+3).`
5. The diagram may keep its inline `<style>` (it is already self-contained)
   **or** switch to `<link rel="stylesheet" href="_diagram.css">`; prefer
   the shared CSS for consistency, adjusting class names if the source used
   `.dc`/`.dcrow` (map to `.box`/`.row`).

- [ ] **Step 2: Resolve the canonical-home decision** — the spec calls for
one canonical copy. Recommended: leave the original
`docs/specs/diagrams/topology-rdqm-ha-dr.html` untouched as a
spec-historical artifact (it still carries the vision panel, which belongs
with the design rationale), and treat `03-rdqm-arm.html` as the published,
as-built canonical. Record this choice in the commit message. (If the team
prefers a single file, instead `vrg-git mv` the original into the site tree
and delete the vision panel there.)

- [ ] **Step 3: Visually verify** — open `03-rdqm-arm.html`; confirm only
the building-block panel remains, node names are `rdqm-*`, and no
vision/hypothesis content survives.

- [ ] **Step 4: Build strict** (canonical recipe). Expected: success.

- [ ] **Step 5: Commit**

```bash
vrg-git add docs/site/docs/architecture/diagrams/03-rdqm-arm.html docs/site/docs/architecture/index.md
vrg-commit --type docs --scope docs --message "add diagram 3 — RDQM 3+3 HA/DR (as-built; vision panel dropped)"
```

---

## Task 9: Diagram 4 — standalone QM arm

**Files:**
- Create: `docs/site/docs/architecture/diagrams/04-standalone-qm.html`

**Data to encode (from `topology.yaml`, Phase B):** `qm-main`
(net-client 10.30.0.10, net-dtcc 10.20.0.10) ↔ `dtcc-sim`
(net-dtcc 10.20.0.50) and `app-client` (net-client 10.30.0.60). Show the
message path: app-client → qm-main → dtcc-sim, labelling the two networks.

- [ ] **Step 1: Author the diagram** — self-contained HTML linking
`_diagram.css`. One `.panel` titled "Standalone QM message path" with three
`.box`/`.node` elements (`app-client`, `qm-main`, `dtcc-sim`) connected
left-to-right, `.net` tags for `net-client` and `net-dtcc`, and a short
`.ok` caption noting this proves reboot-survival of the message path.

- [ ] **Step 2: Visually verify** — open in browser; cross-check names/IPs
against `topology.yaml`. Tune iframe height.

- [ ] **Step 3: Build strict** (canonical recipe). Expected: success.

- [ ] **Step 4: Commit**

```bash
vrg-git add docs/site/docs/architecture/diagrams/04-standalone-qm.html docs/site/docs/architecture/index.md
vrg-commit --type docs --scope docs --message "add diagram 4 — standalone QM message path"
```

---

## Task 10: Diagram 5 — Pacemaker/SAN arm

**Files:**
- Create: `docs/site/docs/architecture/diagrams/05-pacemaker-san.html`

**Data to encode (from `topology.yaml`, Phase D):** `san-a`
(net-san-a 10.40.1.5) as shared storage; `pcmk-a1..3` each on
`net-data-a` (10.10.1.51-53), `net-hb-a` (172.16.1.51-53),
`net-san-a` (10.40.1.51-53), `net-wan` (10.99.0.51-53). Show a 3-node
Pacemaker cluster sharing the SAN, contrasted with RDQM's replicated
storage (a one-line `.callout` making the contrast explicit).

- [ ] **Step 1: Author the diagram** — self-contained HTML linking
`_diagram.css`. One `.panel` "Pacemaker/SAN HA arm" with a `.group` holding
`pcmk-a1..3` `.node` chips above a `san-a` `.box` (use `.net net-san` tag),
plus a `.callout` contrasting shared-SAN HA vs RDQM block replication.

- [ ] **Step 2: Visually verify** — open in browser; cross-check names/IPs
against `topology.yaml`. Tune iframe height.

- [ ] **Step 3: Build strict** (canonical recipe). Expected: success.

- [ ] **Step 4: Commit**

```bash
vrg-git add docs/site/docs/architecture/diagrams/05-pacemaker-san.html docs/site/docs/architecture/index.md
vrg-commit --type docs --scope docs --message "add diagram 5 — Pacemaker/SAN arm"
```

---

## Task 11: Design & Specs page

**Files:**
- Create: `docs/site/docs/design-and-specs.md`

- [ ] **Step 1: Write the page**

Create `docs/site/docs/design-and-specs.md`. Links point at the repo's
`docs/specs/` and `docs/reports/` on GitHub (the site documents *what/how*;
the specs remain the *why*). Confirm each filename against the repo before
committing.

```markdown
# Design & Specs

The published site documents *what the lab is and how to run it*. The design
specs and findings reports below are the *why* — the engineering record of
how it was built. They live in-repo under `docs/specs/`, `docs/plans/`, and
`docs/reports/`.

## Design specs

- **[MQ cluster lab design](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/main/docs/specs/2026-06-03-mq-cluster-lab-design.md)**
  — the authoritative design; the lab is built out in its §10 phases (A→G).
- **[DR/HA validation framework design](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/main/docs/specs/2026-06-08-dr-ha-validation-framework-design.md)**
  — how disaster-recovery and high-availability behavior is validated.
- **[Docs site + architecture design](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/main/docs/specs/2026-06-08-docs-site-and-architecture-design.md)**
  — the design behind this documentation site.

## Findings reports

- **[Phase A provider spike](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/main/docs/reports/2026-06-06-phase-a-provider-spike.md)**
  — the virtualization-provider findings (UEFI/arm64, CPU mode, the
  weak-host-model test-design lesson).
- **[Phase C RDQM findings](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/main/docs/reports/2026-06-06-phase-c-rdqm-findings.md)**
  — RDQM cluster formation and fault-drill results.
- **[Phase D Pacemaker findings](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/main/docs/reports/2026-06-07-phase-d-pacemaker-findings.md)**
  — the Pacemaker/SAN arm findings.

## Related tooling

- **Operations & observability (#32)** — health checks and a single
  lab-status verb for the running stacks (planned; this page will link the
  Operations section once it lands).
```

- [ ] **Step 2: Restore the full nav** — uncomment the `Design & Specs`
nav line in `docs/site/mkdocs.yml` (and confirm `Architecture` is present).
The nav is now complete.

- [ ] **Step 3: Build strict** (canonical recipe — full nav, nothing
commented). Expected: success.

- [ ] **Step 4: Commit**

```bash
vrg-git add docs/site/docs/design-and-specs.md docs/site/mkdocs.yml
vrg-commit --type docs --scope docs --message "write the Design & Specs index; complete the nav"
```

---

## Task 12: Full-site verification + PR

**Files:** none (verification + PR).

- [ ] **Step 1: Full strict build from a clean tree**

```bash
vrg-git status   # must be clean
vrg-container-run -- bash -c '
  set -euo pipefail
  vrg-docs-stage --docs-dir docs/site/docs
  vrg-docs-patch-nav --mkdocs-yml docs/site/mkdocs.yml --releases-dir docs/site/docs/releases
  uv run mkdocs build --strict -f docs/site/mkdocs.yml
'
```

Expected: success with Changelog + Release Notes generated and every page
in nav. Confirm the build output references `index.html`,
`getting-started/`, `architecture/`, `design-and-specs/`, `changelog/`, and
`releases/`.

- [ ] **Step 2: Visual pass on the built site**

Open `docs/site/site/index.html` (and the architecture page) in a browser.
Confirm all five diagrams render inside their iframes, links resolve, and
the light/dark theme toggle does not corrupt the (dark, self-contained)
diagrams.

- [ ] **Step 3: Clean generated artifacts**

```bash
vrg-git restore docs/site/mkdocs.yml   # if patch-nav modified it
rm -rf docs/site/docs/changelog.md docs/site/docs/releases docs/site/site
vrg-git status   # clean
```

- [ ] **Step 4: Run repo validation**

```bash
vrg-container-run -- vrg-validate
```

Expected: pass. (Docs content is markdown/HTML; this confirms nothing else
regressed.)

- [ ] **Step 5: Push and open the PR into `develop`**

```bash
vrg-git push -u origin feature/33-docs-site-architecture
```

Open a PR into `develop` referencing issue #33, summarizing: site config
aligned to template, CI docs gate added, Home/Getting Started/Architecture/
Design & Specs pages, and the five as-built diagrams. After merge, confirm
the `cd-docs` run publishes the live site with Changelog + Release Notes
populated.

---

## Self-review (completed during planning)

- **Spec coverage:** IA (Task 1), publishing/changelog/release-notes (Tasks
  1–2, 12), Home (Task 3), Getting Started (Task 4), Architecture +
  five-diagram set (Tasks 5–10), Design & Specs / spec integration (Task
  11), as-built accuracy + strict build + per-section commits + CI gate
  (throughout, Task 12). The "local docs-build parity" open item from the
  spec is resolved by Task 1's spike + the canonical recipe. The "two
  copies of the RDQM diagram" risk is resolved in Task 8 Step 2. All spec
  sections map to tasks.
- **Placeholder scan:** no TBD/TODO; diagram tasks specify exact data from
  `topology.yaml` plus structure, shared CSS, and acceptance — the HTML is
  authored at execution under visual review (the correct treatment for a
  hand-drawn visual artifact, not a placeholder).
- **Type/name consistency:** the canonical build recipe, file paths, nav
  entries, diagram filenames, and CSS class names are identical across
  tasks; node/network names are pinned to `lab/topology.yaml`.
