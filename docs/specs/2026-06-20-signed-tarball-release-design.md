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
- A static `scripts/bootstrap` onboarding script (prereq/dependency checks →
  `uv sync` → print the next command).
- A users-first README rewrite (Intro → Getting Started → Development).
- A shipped public key (`RELEASE-KEY.asc`) and documented `gpg --verify` flow.

**Out of scope / deferred:**

- **The future "MQ resiliency tooling" repo.** A separate, dependent repo will
  ship generic cluster-management utilities as a proper pip-installable Python
  module. That is a different product with a different (PyPI) channel and is not
  addressed here.
- **PyPI publication of `mqlab`.** We deliberately do not publish to PyPI.
- **Windows support in `bootstrap`.** macOS + Linux are the targets; Windows is
  a maybe-someday.
- **Host/MQ-entitlement validation inside `bootstrap`.** Kept lean; deep host
  validation stays a documented `mqlab doctor` follow-up (§7).

## 3. Architecture — three committed pieces + one CI job

The release is driven entirely by **committed files**. There is no staging,
copying, or file injection at package time: everything the consumer receives is
either a tracked file or is excluded by a tracked attribute. The version is
**asserted**, never stamped.

1. **`.gitattributes`** — declares the curation boundary via `export-ignore`.
2. **`scripts/bootstrap`** — the static consumer onboarding entrypoint.
3. **`README.md`** — rewritten users-first; carries the Getting Started and the
   `gpg --verify` instructions.
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
consumer: download → (gpg --verify) → tar xzf → ./scripts/bootstrap → mqlab run <setup>
```

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
- The **public key ships in-repo as `RELEASE-KEY.asc`** and is uploaded to a
  keyserver. The README documents the fingerprint and the verify one-liner:

  ```
  gpg --import RELEASE-KEY.asc        # first time, or fetch from keyserver
  gpg --verify SHA256SUMS.asc SHA256SUMS
  sha256sum -c SHA256SUMS             # checks the tarball
  # (or directly) gpg --verify mq-cluster-tooling-vX.Y.Z.tar.gz.asc mq-cluster-tooling-vX.Y.Z.tar.gz
  ```

Verification is an **optional extra step** on the consumer's happy path — the
download → unpack → bootstrap flow is identical whether or not they verify.

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

## 7. Consumer onboarding — `scripts/bootstrap`

A static, committed script (sibling of `scripts/fetch-mq.sh`). Responsibilities:

1. **Prereq / dependency checks** — `uv` present; Python 3.12 available; report
   actionable, fail-loud messages on anything missing (no silent fallback).
2. **`uv sync`** — materialize the environment.
3. **Print the exact next command** — e.g. `uv run mqlab run <setup>` (or how to
   activate the venv and call `mqlab`).

Design constraints:

- **Cross-platform aspiration:** works on macOS and Linux. Windows is out of
  scope for now.
- **Lean:** deep host validation (libvirt, nested virt, MQ entitlement
  artifacts) is **not** in `bootstrap`; it is a documented `mqlab doctor`
  follow-up so the consumer can validate the host before bringing the lab up.
- **Glass-box:** the README documents precisely what `bootstrap` does so a
  consumer can run the steps by hand and understand them.

## 8. README rewrite (users-first)

Users will vastly outnumber developers, so the README leads with them:

1. **Intro** — a paragraph or two: what this is and what it is for.
2. **Getting Started** — the happy path: *(optionally) verify the signature →
   `./scripts/bootstrap` → `mqlab run <setup>` → sit back*, immediately followed
   by a **"What `bootstrap` does"** subsection enumerating the steps for manual
   execution and understanding.
3. **Development** — the current Vergil-VM development workflow (today's README
   body), relocated to the bottom for the rare contributor.

## 9. Open integration point (resolve in the plan)

`vergil.toml` currently sets `[publish] release = false`, and CD delegates to the
shared `vergil-project/vergil-actions` `cd-docs.yml`. The plan must verify
**one** of:

- keep `release = false` and add a **bespoke** release job (this design's
  default assumption); or
- hook into a **vergil-actions custom-artifact release path** if one exists and
  fits.

This is a verification task, not a guess — chosen during planning against the
actual `vergil-actions` v2.1 capabilities.

## 10. Testing & acceptance

- **Version-guard test:** a tag/version mismatch fails the job; a match passes.
- **Curation test:** assert the produced tarball **excludes** every dev-only
  path (§4) and **includes** the product paths + `scripts/bootstrap` +
  `RELEASE-KEY.asc`. Drives the `export-ignore` list to correctness.
- **Signature test:** `gpg --verify` of the produced `.asc` against the tarball
  succeeds with the published public key; `sha256sum -c` passes.
- **Bootstrap smoke:** on a clean macOS and Linux host with `uv` present,
  `./scripts/bootstrap` reaches a working `mqlab --help`.
- **Acceptance (end-to-end):** pushing a `vX.Y.Z` tag produces a GitHub Release
  whose tarball a fresh consumer can verify, unpack, `./scripts/bootstrap`, and
  reach `mqlab run <setup>` — with no clone and no new tooling beyond `gpg`,
  `uv`, and `tar`.
