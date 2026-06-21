# Signed Tarball Release — Design

**Date:** 2026-06-20
**Issue:** #299
**Status:** Design (brainstormed; pending plan)

## 1. Why this exists

This repository is an unusual product. It is **not** a Python module you would
`pip install` from PyPI — `mqlab` (the `src/mqlab` package) is only the
orchestrator. The deliverable is the **whole lab repo**: the Ansible roles, the
libvirt/Vagrant lab definitions, the manifests, the setups, the docs, and the
`mqlab` CLI that drives them. You need the entire support tree to stand the lab
up, not an importable module.

The people who want this code are enterprise MQ shops who want to **bring it
inside their walls**. For them, cloning is hard to get set up (git/SSH auth,
proxies, credential provisioning), but **downloading a signed artifact over
HTTPS is a viable gateway**. A signed tarball:

- verifies with tooling they already trust (`gpg`) — no new tool to whitelist
  through a security review;
- works **offline / air-gapped** — fetch the public key once, carry it inside,
  verify forever;
- gives them a fixed, named, reproducible version coordinate (`vX.Y.Z`).

So we publish the repo as a **signed, semver-tagged tarball** attached to a
GitHub Release, produced automatically by CI on a tag push.

## 2. Scope & non-goals

**In scope:**

- A committed curation boundary (`.gitattributes export-ignore`) that excludes
  developer-only paths from the published tree.
- A CI release pipeline that, on a `vX.Y.Z` tag push, builds → version-guards →
  checksums → PGP-signs → publishes a GitHub Release with the tarball and its
  verification artifacts.
- A static `scripts/setup` onboarding script (prereq/dependency checks →
  `uv sync` → print the next command).
- A users-first README rewrite (Intro → Getting Started → Development), including
  a **Prerequisites** note (§7.1) and out-of-band signature-verification steps.
- A shipped public key (`RELEASE-KEY.asc`) as a convenience copy, with the trust
  root being the **fingerprint + out-of-band key fetch** (§5).
- **A single-command lab bring-up verb — `mqlab bootstrap <setup>`** (§7.2). The
  consumer happy path is `./scripts/setup` → `mqlab doctor` → `mqlab bootstrap
  <setup>` → sit back. No such one-command bring-up exists today; this work adds
  it as a thin orchestration wrapper over the existing verbs. "bootstrap" is
  reserved for **lab** bring-up; the environment-setup script is therefore named
  `scripts/setup`, not `scripts/bootstrap`, to avoid overloading the term.
- **An operator release runbook** (`docs/development/release-runbook.md`) — the
  durable home for the operator-only steps §5 implies: signing-key generation, CI
  secret setup, and the cut-a-release procedure.

**Out of scope / deferred:**

- **The future "MQ resiliency tooling" repo.** A separate, dependent repo will
  ship generic cluster-management utilities as a proper pip-installable Python
  module. That is a different product with a different (PyPI) channel and is not
  addressed here.
- **PyPI publication of `mqlab`.** We deliberately do not publish to PyPI.
- **Windows support in `scripts/setup`.** macOS + Linux are the targets; Windows
  is a maybe-someday.
- **Host validation inside `scripts/setup`.** Kept lean; deep host validation is
  the job of `mqlab doctor` / `_prepare_lab()`, which already hard-gate on host
  prerequisites outside Vergil (§7).

## 3. Architecture — three committed pieces + one CI job

The release is driven entirely by **committed files**. There is no staging,
copying, or file injection at package time: everything the consumer receives is
either a tracked file or is excluded by a tracked attribute. The version is
**asserted**, never stamped.

1. **`.gitattributes`** — declares the curation boundary via `export-ignore`.
2. **`scripts/setup`** — the static consumer environment-setup entrypoint.
3. **`README.md`** — rewritten users-first; carries the Getting Started, the
   Prerequisites note, and the out-of-band `gpg --verify` instructions.
4. **A CI release job** — orchestrates archive → guard → checksum → sign →
   publish. Thin plumbing, not a packaging layer.

### 3.1 Data flow

```
maintainer bumps pyproject.toml + VERSION, commits, tags vX.Y.Z, pushes tag
        │
        ▼
CI release job (on push: tags ['v*'])
        │  1. checkout at the tag
        │  2. version guard: tag (vX.Y.Z) == pyproject.toml version == VERSION  ── fail loud on mismatch
        │  3. git archive --prefix=mq-cluster-tooling-vX.Y.Z/ -o <tarball> vX.Y.Z
        │        (.gitattributes export-ignore drops dev-only paths)
        │  4. sha256sum <tarball> > SHA256SUMS
        │  5. import release private key (CI secret); gpg --detach-sign the
        │        tarball and SHA256SUMS  →  .asc files
        │  6. gh release create vX.Y.Z  <tarball> <tarball>.asc SHA256SUMS SHA256SUMS.asc
        ▼
GitHub Release vX.Y.Z with four assets
        │
        ▼
consumer: download → (verify: out-of-band key + fingerprint) → tar xzf
          → ./scripts/setup → mqlab bootstrap <setup> → sit back
```

`mqlab bootstrap <setup>` is the single-command lab bring-up verb added by this
work (§7.2). `mqlab run <setup>` is a separate, post-bring-up baseline test
driver and is **not** part of the happy path.

## 4. Curation — what ships, what does not

Approach: **committed `.gitattributes` with `export-ignore`** consumed by
`git archive`. The boundary travels with the repo, and `git archive` emits
exactly the non-ignored tracked tree. No allowlist script, no staging directory.

**Excluded (developer-only) — initial list, finalized in the plan:**

- Agent/session scaffolding: `.claude/`, `.worktrees/`, `.vergil/`,
  `.superpowers/`
- Dev-environment definition: `vergil.toml`
- Tests: `tests/`
- CI config: `.github/`
- Internal design history not needed to run the lab: candidate `docs/specs/`
  (TBD — some specs are operator-relevant; the plan decides the precise cut)

**Included (the product):** `src/mqlab/`, `ansible/`, `lab/`, `manifests/`,
`clients/`, `content/`, `scripts/`, `tools/`, `docs/` (operator-facing),
`pyproject.toml`, `uv.lock`, `VERSION`, `LICENSE`, `CHANGELOG.md`, `README.md`,
`RELEASE-KEY.asc`.

Notes:

- `git archive` already omits `.git` and anything gitignored (e.g. `build/`),
  so those need no `export-ignore` line.
- The exact `docs/specs/` cut is the one genuinely judgement-heavy item and is
  resolved in the plan, not assumed here.

## 5. Signing & verification

**Model:** PGP detached signature + published public key. Chosen over Sigstore/
cosign because the target consumers already verify OS/IBM artifacts with `gpg`,
need no new tool, and can verify air-gapped. (Cosign's keyless model is the
better cloud-native answer but imposes a tool install and a heavier trust
conversation on exactly the people we are trying to make this easy for.)

- A **dedicated PGP release subkey**, used only for release signing.
- The **private key lives in a GitHub Actions secret**; CI imports it for the
  signing step. Mitigations for the long-lived-key risk: release-only subkey,
  fingerprint published in the README, rotate if ever compromised.

**Trust root — the fingerprint + an out-of-band key, never the bundled copy.**
A copy of the public key also rides *inside* the signed tarball; verifying the
tarball against that copy proves nothing (an attacker who tampers the tarball
swaps the key and re-signs). So the **root of trust** is the **full fingerprint
published in the README** plus the key fetched from a channel **independent of
the tarball**:

- **keyserver by fingerprint** — `gpg --recv-keys <FULL-FINGERPRINT>`; and/or
- **the key file from the GitHub repo/release page over HTTPS**.

The in-tree `RELEASE-KEY.asc` is a **convenience copy** (useful once the key is
already trusted, e.g. air-gapped re-verification) — explicitly **not** the trust
root. The README's verify steps therefore read:

```
# 1. obtain the key out-of-band, then confirm its fingerprint matches the README
gpg --recv-keys <FULL-FINGERPRINT>          # or import the HTTPS-fetched key file
gpg --fingerprint <FULL-FINGERPRINT>        # cross-check against the README
# 2. verify
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum -c SHA256SUMS                       # checks the tarball
# (or directly) gpg --verify mq-cluster-tooling-vX.Y.Z.tar.gz.asc mq-cluster-tooling-vX.Y.Z.tar.gz
```

Verification is an **optional extra step** on the consumer's happy path — the
download → unpack → setup flow is identical whether or not they verify.

## 6. Release workflow & versioning

- **Trigger:** `push: tags: ['v*']`. The tag is the single source of truth for
  the version; the tarball and Release are named from it.
- **Version guard (fail loud):** before building, CI asserts the tag version
  equals `pyproject.toml`'s `project.version` **and** the `VERSION` file. A
  mismatch fails the job — no silent stamping, no drift. This relies on the
  normal tagged-release flow: bump the version in a commit, then tag that
  commit.
- **Placement:** a new `release` job, either added to `.github/workflows/cd.yml`
  alongside the existing docs job or as a dedicated `release.yml`. Decided in
  the plan together with §9.
- **Idempotence/failure:** if the job fails after partial work, re-running the
  tag's workflow must converge (e.g. `gh release create` vs. `gh release
  upload --clobber` on an existing Release). The plan specifies the exact `gh`
  invocation.

## 7. Consumer onboarding — `scripts/setup`

A static, committed script (sibling of `scripts/fetch-mq.sh`). It sets up the
**Python environment** so `mqlab` runs; it does **not** bring up the lab (that is
`mqlab bootstrap <setup>`). Responsibilities:

1. **Prereq / dependency checks** — `uv` present; Python 3.12 available; report
   actionable, fail-loud messages on anything missing (no silent fallback).
2. **`uv sync`** — materialize the environment.
3. **Print the exact next command** — `mqlab bootstrap <setup>` (and, for the
   curious, `mqlab doctor` to pre-flight the host).

Design constraints:

- **Cross-platform aspiration:** works on macOS and Linux. Windows is out of
  scope for now.
- **Lean — env only:** `scripts/setup` does **not** validate the virtualization
  host. That is already owned by `mqlab doctor` / `_prepare_lab()`
  (`src/mqlab/cli.py`), which hard-gate on host prerequisites (arch, KVM,
  required tools) outside Vergil and **fail loud** naming what's missing (#276).
  `scripts/setup` simply points the consumer at `mqlab doctor`.
- **Glass-box:** the README documents precisely what `scripts/setup` does so a
  consumer can run the steps by hand and understand them.

### 7.1 Prerequisites (Getting Started note)

Almost everything the lab needs is fetched automatically — IBM **MQ Advanced for
Developers** (no-charge) is pulled by `scripts/fetch-mq.sh` into `build/mq/` for
both arms; the virtualization stack is checked (not installed) by `mqlab doctor`.
The README's Getting Started states the small, honest set of things the consumer
must provide:

- **A virtualization-capable host with root/sudo** — the lab creates nested
  libvirt/QEMU/Vagrant guests and needs a beefy host (cf. `vergil.toml`'s dev
  profile: ~12 vCPU / 64 GiB, nested virt). `mqlab doctor` is the pre-flight gate
  that reports any missing host bits.
- **A RHEL box/subscription** — the **only** genuinely manual artifact, required
  by the RHEL-based arms (pcmk-rhel, RDQM). Ubuntu arms need nothing extra.

Getting Started routes the consumer through `mqlab doctor` **before**
`mqlab bootstrap <setup>`, so missing prerequisites fail loud up front.

### 7.2 The bring-up verb — `mqlab bootstrap <setup>`

A new top-level command that gives the consumer the "one command, then sit back"
experience. It is a **thin orchestration wrapper** over verbs that already exist;
it adds sequencing, not new bring-up logic.

- **What it wraps.** For the named setup, in order: resolve/apply the manifest
  selection (#266) → `net create` (the setup's networks) → `vm create` (which
  already *creates + provisions* the guests, including QM/HA bring-up via Ansible)
  → `obs up` (the shared observability stack). The exact verb list per arm is
  finalized in the plan from `setups.py` / `topology.yaml`.
- **How it's built.** It assembles `CommandStep`s from the existing per-verb step
  builders and runs them through the existing `run_steps` orchestrator
  (`src/mqlab/orchestrator.py`) — same fail-loud-on-non-zero, same `--step`
  semantics. No new execution machinery.
- **Pre-flight.** It runs (or instructs the consumer to run) `mqlab doctor` first
  so host prerequisites fail loud before any guest is created.
- **Relationship to `mqlab run`.** `bootstrap` brings the lab *up*; `mqlab run
  <setup>` is the separate, post-bring-up baseline test driver. They are distinct
  verbs and must stay distinct.
- **Scope note.** This is deliberately a sequencing wrapper. Parallelizing the
  bring-up (concurrent VM boots) is the separate concern of #211 and is **not**
  pulled into this verb; `bootstrap` can adopt that speed-up later without an
  interface change.

## 8. README rewrite (users-first)

Users will vastly outnumber developers, so the README leads with them:

1. **Intro** — a paragraph or two: what this is and what it is for.
2. **Getting Started** — in order:
   - **Prerequisites** (§7.1) — virtualization host + root; RHEL box/subscription
     for the RHEL arms; everything else is fetched automatically.
   - **(Optional) verify the signature** — using the out-of-band key + fingerprint
     (§5), not the bundled key copy.
   - **The happy path** — `./scripts/setup` → `mqlab doctor` → `mqlab bootstrap
     <setup>` → sit back — immediately followed by a **"What `scripts/setup`
     does"** subsection enumerating the steps for manual execution and
     understanding.
3. **Development** — the current Vergil-VM development workflow (today's README
   body), relocated to the bottom for the rare contributor.

## 9. Publish-mechanism decision

`vergil.toml` keeps `[publish] release = false`; CD continues to delegate docs to
the shared `vergil-project/vergil-actions` `cd-docs.yml`, untouched. The signed
tarball is published by a **bespoke** `release.yml` job (§6), not a vergil-actions
release path.

Rationale: the bespoke job is self-contained and correct independent of whatever
vergil-actions offers, so it is the safe default. If vergil-actions later exposes
a custom-artifact release path that fits, migrating to it is a follow-up — not a
blocker for this work.

## 10. Testing & acceptance

- **Version-guard test:** a tag/version mismatch fails the job; a match passes.
- **Curation test:** assert the produced tarball **excludes** every dev-only
  path (§4) and **includes** the product paths + `scripts/setup` +
  `RELEASE-KEY.asc`. Drives the `export-ignore` list to correctness.
- **Signature test:** `gpg --verify` of the produced `.asc` against the tarball
  succeeds with the published public key (obtained out-of-band); `sha256sum -c`
  passes.
- **Setup smoke:** on a clean macOS and Linux host with `uv` present,
  `./scripts/setup` reaches a working `mqlab --help`.
- **Acceptance (end-to-end):** pushing a `vX.Y.Z` tag produces a GitHub Release
  whose tarball a fresh consumer can verify (via the out-of-band key +
  fingerprint), unpack, `./scripts/setup`, pass `mqlab doctor`, and bring the lab
  up with `mqlab bootstrap <setup>` — with no clone and no new tooling beyond
  `gpg`, `uv`, and `tar` (plus a RHEL box for the RHEL arms).
- **Bring-up verb test:** `mqlab bootstrap <setup>` issues the expected verb
  sequence (manifest → `net create` → `vm create` → `obs up`) and halts loud if
  any step exits non-zero (§7.2).
