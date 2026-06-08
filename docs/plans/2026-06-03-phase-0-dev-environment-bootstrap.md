# Phase 0 — Development Environment Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a reproducible, correctly-sized Linux dev VM — provisioned with the MQ-lab toolchain — that both the agent and the human can shell into, before any lab code is written.

**Architecture:** Extend the Vergil VM tooling so one identity (credentials) can drive multiple **VM profiles** (footprint + package set), with the footprint/package truth declared *in the consuming repo* (`.vergil/vm-spec.toml`, "Fork B") and the credentials/profile-selection declared at the user level (`~/.config/vergil/identities.toml`). Then "Virgilize" this repo against that interface and build the `mq-lab` VM. The VM stays **ephemeral, data-less, and 100% reproducible** — the only working space is the gitignored `build/` directory (the host repo, mounted in).

**Tech stack:** Vergil VM (Lima + Apple Virtualization.framework, Ubuntu 24.04), `vergil-tooling` (Python 3.11+ CLI: `vrg-vm`), TOML config, cloud-init provisioning. Conventions of the `vergil-vm`/`vergil-tooling` repos apply to all work in Phase 0a (commit format `type(scope): msg (#issue)`, `vrg-git`/`vrg-gh` wrappers, `.worktrees/issue-<N>-<slug>/`, validation via `vrg-container-run -- vrg-validate`).

---

## Contents

- [Why this is "Phase 0" and not part of the lab plans](#why-this-is-phase-0-and-not-part-of-the-lab-plans)
- [The interface contract (both sides depend on this — settle it first)](#the-interface-contract-both-sides-depend-on-this--settle-it-first)
- [Phase 0a — Vergil VM "profile" feature (design + handoff to the vergil-vm repo)](#phase-0a--vergil-vm-profile-feature-design--handoff-to-the-vergil-vm-repo)
- [Phase 0b — Virgilize `mq-cluster-tooling` (this repo, fully specified)](#phase-0b--virgilize-mq-cluster-tooling-this-repo-fully-specified)
- [Phase 0c — Build & verify the `mq-lab` VM (gated on 0a landing)](#phase-0c--build--verify-the-mq-lab-vm-gated-on-0a-landing)
- [Self-review notes](#self-review-notes)

## Why this is "Phase 0" and not part of the lab plans

The lab itself (spec §10 phases A→G) gets its own per-phase spec→plan→build cycles. Phase 0 is the prerequisite: it produces the *environment* the lab is built inside. From the `vrg-vm` CLI's perspective the deliverable is "`vrg-vm create --vm mq-lab` yields a reproducible box with libvirt/QEMU/Vagrant/containerd, into which `build/` is mounted." Nothing lab-specific is built here.

## The interface contract (both sides depend on this — settle it first)

This is the agreed "Fork B" shape. The schema below is the contract between the Vergil extension (Phase 0a, built in the `vergil-vm`/`vergil-tooling` repos) and this repo's spec file (Phase 0b). It must not drift between the two.

**User-level — `~/.config/vergil/identities.toml`** (credentials + which profiles exist; never in source control):

```toml
default_identity = "vergil"
vergil = "v2.0"

[identities.vergil]
vm_instance = "vergil-agent"
auth_type = "app"
app_id = "..."
private_key_path = "~/.config/vergil/keys/key.pem"
claude_token_path = "~/.config/vergil/keys/claude-oauth-token"
projects_dir = "/Users/pmoore/dev/projects"

# NEW: a VM profile. References an identity for credentials; pulls footprint +
# packages from the named workspace's .vergil/vm-spec.toml at create time.
[vms.mq-lab]
identity  = "vergil"                 # whose credentials to bake in
workspace = "logical-minds-foundry/mq-cluster-tooling"  # relative to projects_dir
vm_instance = "vergil-mq-lab"        # separate long-lived Lima instance
```

**Source-controlled — `<repo>/.vergil/vm-spec.toml`** (the reproducible reference: footprint + required packages):

```toml
[vm]
cpus   = 12
memory = "64GiB"
disk   = "300GiB"
# Extra apt packages layered onto the Vergil base image at provision time.
# The base already supplies: curl, jq, ripgrep, zsh, git, gh, node, uv,
# python3, rootless containerd. Only additive lab tooling goes here.
packages = [
  "qemu-system-x86",
  "qemu-system-arm",
  "qemu-utils",
  "libvirt-daemon-system",
  "libvirt-clients",
  "bridge-utils",
  "dnsmasq-base",
  "genisoimage",
  "ruby-dev",            # vagrant-libvirt plugin build dep
  "libvirt-dev",         # vagrant-libvirt plugin build dep
  "pkg-config",
  "gcc",
  "make",
]
```

Notes captured for the Phase-0a brainstorm (not decisions to make here):
- **Vagrant + the `vagrant-libvirt` plugin** are not single apt packages on 24.04; the brainstorm must decide how they install reproducibly (HashiCorp apt repo for `vagrant`, then `vagrant plugin install vagrant-libvirt` in a provisioning step). The `packages` array above carries the plugin's *build deps*; the install *commands* are a separate template concern.
- **Nested virtualization** (KVM inside the Lima vz VM) is required for the arm64 lab nodes and is an open enablement question (macOS 15 + M3+ nested-virt pass-through). Whether the profile needs a `nested = true` knob, or it's a base-template setting, is for the brainstorm. This is the same risk flagged in spec §7.2 / §11 and is re-validated in lab Phase A.
- **`build/` performance:** the gitignored `build/` is a host-mounted dir holding nested-VM images. We are explicitly **not** pre-optimizing this (no RAM-disk, no VM-local volume) — assume the M5 SSD + Lima caching suffice, and only revisit if measured to be slow.

---

## Phase 0a — Vergil VM "profile" feature (design + handoff to the vergil-vm repo)

**This phase is deliberately not bite-sized TDD code.** The feature lives in the `vergil-vm`/`vergil-tooling` repos, which have their own brainstorm→spec→plan→build flow, conventions, and test suite. Per the user's instruction, it gets its **own brainstorm** there. This plan seeds that brainstorm with the agreed design and files the tracking issues; it does not fabricate the implementation steps that belong in the other repo.

**Files (in the `vergil-project` repos, for reference — implemented there, not here):**
- Modify: `vergil-tooling/src/vergil_tooling/lib/identity.py` (add `VmProfile` parsing + repo-spec resolution)
- Modify: `vergil-tooling/src/vergil_tooling/bin/vrg_vm.py` (`--vm <profile>` selection; merge profile → identity → template)
- Modify: `vergil-tooling/src/vergil_tooling/lib/lima.py` (apply footprint; layer packages into provisioning)
- Modify: `vergil-vm/templates/agent.yaml` (accept an additive package list at create time)
- Test: `vergil-tooling/tests/...` (profile resolution, spec-file parsing, merge precedence) + `vergil-vm/tests/test_tools.sh` (packages present in a profile VM)

- [ ] **Step 1: Draft the GitHub issues for `vergil-vm`** (do not file yet)

Draft two issues capturing the agreed design so the user can review wording before anything is created (issues are shared state — get approval first):

  1. **`feat: per-repo VM profiles (footprint + packages) decoupled from identity`**
     - Problem: identity currently conflates *who* (credentials) with *what kind of box*. Stripped-min base is correct for Claude Code, but consuming repos (e.g. an MQ lab) need extra tooling + a bigger footprint.
     - Proposal (Fork B): introduce a `[vms.<name>]` table in `identities.toml` that references an `identity` for credentials and a `workspace`; read footprint + `packages` from that workspace's source-controlled `.vergil/vm-spec.toml` at create time. Select with `vrg-vm create --vm <name>` / `vrg-vm session <ws> --vm <name>`.
     - Boundary to preserve: rules + repo requirements live in source; credentials + profile-selection live in user config; VM stays ephemeral/reproducible (packages declared, never hand-installed into a live VM).
     - Open sub-questions: small inline overrides on top of the repo spec; how the package list layers into `agent.yaml` so re-provisioning is identical; Vagrant/plugin install commands vs. apt `packages`; profile-name vs. workspace-name collisions; nested-virt enablement knob.
  2. **`docs: define the `.vergil/vm-spec.toml` consuming-repo contract`**
     - Document the schema (the contract block above) so any repo can opt in.

- [ ] **Step 2: Get user approval on the issue text, then file them**

Present the drafts. On approval, file with `vrg-gh` from the `vergil-vm` repo (the user runs it, or approves the command). Record the issue numbers — they become the `(#N)` in Phase-0a commits and the seed for the brainstorm there.

- [ ] **Step 3: Hand off to a dedicated brainstorm in the `vergil-vm` repo**

Start a brainstorming session **in `vergil-project/vergil-vm`** (its home, its conventions) using this plan's contract block as the design seed. That session produces the feature's own spec + implementation plan and builds it TDD-style there. **Do not implement the feature from this repo.**

- [ ] **Step 4: Verify the feature is usable before proceeding**

Acceptance for leaving 0a: from the host, `vrg-vm create --vm mq-lab --help` (or equivalent) recognizes the profile mechanism, and a throwaway profile pointed at a tiny test spec builds a VM with one extra package present. Phase 0c depends on this being true.

---

## Phase 0b — Virgilize `mq-cluster-tooling` (this repo, fully specified)

**Files:**
- Create: `.vergil/vm-spec.toml`
- Create: `.gitignore` entries for `build/` and `.vergil` secrets (modify existing `.gitignore`)
- Create: `build/.gitkeep`
- Create: `CLAUDE.md`
- Modify: `README.md` (note the dev-environment entry point)

- [ ] **Step 1: Write the repo VM spec**

Create `.vergil/vm-spec.toml` with exactly the contract block above (the `[vm]` table: `cpus = 12`, `memory = "64GiB"`, `disk = "300GiB"`, and the `packages` array). This file is the source-of-truth "reference" the Vergil profile pulls from.

- [ ] **Step 2: Create the gitignored working directory**

```bash
mkdir -p build
printf '# Keeps the gitignored build/ dir present in a fresh clone.\n' > build/.gitkeep
```

- [ ] **Step 3: Update `.gitignore`**

Add (the repo already ignores `.superpowers/`, `*.env`, `secrets/`, `licenses/`, `.vagrant/`, `*.box`, `*.log`):

```gitignore
# Ephemeral lab working tree — nested-VM images, vagrant boxes, libvirt disks.
# The dev VM mounts this; nothing here is precious or version-controlled.
build/
!build/.gitkeep

# Vergil per-repo spec is committed, but never commit resolved secrets.
.vergil/*.local.toml
```

- [ ] **Step 4: Write `CLAUDE.md`**

Create `CLAUDE.md` documenting the dev-environment entry point and the repo conventions a session needs. Content:

```markdown
# mq-cluster-tooling — agent & contributor guide

## Development environment

This repo is developed inside a Vergil VM profile, **not** on macOS directly.

- Build the VM once: `vrg-vm create --vm mq-lab`
- Work in it: `vrg-vm session logical-minds-foundry/mq-cluster-tooling --vm mq-lab`
- The VM is **ephemeral and 100% reproducible**. Do not hand-customize it.
  Re-provision freely to stay fresh: `vrg-vm rebuild --vm mq-lab`.
- All working state lives in the gitignored `build/` directory (mounted from
  the host). Nothing in the VM outside `build/` is precious.
- Footprint and installed packages are declared in `.vergil/vm-spec.toml`.
  Change tooling by editing that file and rebuilding — never by `apt install`
  inside a live VM.

## Design

The authoritative design is `docs/specs/2026-06-03-mq-cluster-lab-design.md`.
Lab work proceeds per its §10 phases (A→G), each its own spec→plan→build.

## Conventions

- Secrets never enter git: no MQ entitlement/license artifacts, no
  credentials, no keystores. `.gitignore` covers `*.env`, `secrets/`,
  `licenses/`. Content-plane (`pymqrest`) credentials are runtime-injected.
- Memory files require human approval before writing (per global policy).
```

- [ ] **Step 5: Note the entry point in `README.md`**

Add a short "Development environment" line to `README.md` pointing at `vrg-vm create --vm mq-lab` and `.vergil/vm-spec.toml`, so a fresh reader knows how to get a working box.

- [ ] **Step 6: Commit**

```bash
git add .vergil/vm-spec.toml build/.gitkeep .gitignore CLAUDE.md README.md
git commit -m "chore: virgilize repo — add .vergil/vm-spec.toml, build/, CLAUDE.md" -m "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 0c — Build & verify the `mq-lab` VM (gated on 0a landing)

**Files:** none in this repo — this is user-environment configuration + verification.

- [ ] **Step 1: Add the profile to user config**

In `~/.config/vergil/identities.toml`, add the `[vms.mq-lab]` table from the contract block (references the `vergil` identity, `workspace = "logical-minds-foundry/mq-cluster-tooling"`, `vm_instance = "vergil-mq-lab"`). This is user-level config the user edits/approves; the repo never contains it.

- [ ] **Step 2: Create the VM**

```bash
vrg-vm create --vm mq-lab
```
Expected: a new Lima instance `vergil-mq-lab` provisions with the `.vergil/vm-spec.toml` footprint (12 CPU / 64 GiB / 300 GiB) and the extra packages.

- [ ] **Step 3: Verify footprint and toolchain**

```bash
vrg-vm session logical-minds-foundry/mq-cluster-tooling --vm mq-lab
# inside the VM:
nproc; free -h; df -h /          # footprint matches the spec
which qemu-system-x86_64 virsh vagrant containerd
vagrant plugin list | grep libvirt
ls -la build/                     # gitignored host dir is mounted
```
Expected: CPU/memory/disk match the spec; all lab tools resolve; `build/` is present and writable.

- [ ] **Step 4: Verify nested virtualization (the spike checkpoint)**

```bash
# inside the VM:
ls -l /dev/kvm && kvm-ok 2>/dev/null || egrep -c '(vmx|svm)' /proc/cpuinfo
```
Expected: `/dev/kvm` exists (arm64 KVM accelerated). If absent, this is the spec §7.2/§11 nested-virt risk surfacing — stop and resolve nested-virt enablement (a `vergil-vm` template/Lima setting) before lab Phase A. **This is a discovery checkpoint, not a task to force.**

- [ ] **Step 5: Verify reproducibility**

```bash
vrg-vm rebuild --vm mq-lab
```
Expected: the VM is destroyed and recreated to an identical state from `.vergil/vm-spec.toml` alone, with `build/` (host-mounted) intact. Confirms the "ephemeral, data-less, reproducible" property holds end-to-end.

---

## Self-review notes

- **Spec coverage:** Phase 0 covers the two "step zero" items (Vergil extension via 0a; Virgilize via 0b) plus the base-VM build (0c). It does **not** cover lab spec §10 A→G — those are intentionally separate plans.
- **No fabricated cross-repo code:** 0a is a design+issues+handoff phase by intent; the feature's TDD implementation belongs to the `vergil-vm` repo's own brainstorm/plan, as the user directed. The interface contract is fully specified so the two sides cannot drift.
- **Type/name consistency:** profile selection verb is `--vm <name>` throughout; the profile table is `[vms.<name>]`; the repo spec is `.vergil/vm-spec.toml` with a `[vm]` table — consistent across 0a/0b/0c.
- **Deferred (not in scope):** `build/` performance optimization (RAM-disk/VM-local volume) — revisit only if measured slow. Nested-virt enablement detail — owned by the `vergil-vm` brainstorm + lab Phase A spike.
