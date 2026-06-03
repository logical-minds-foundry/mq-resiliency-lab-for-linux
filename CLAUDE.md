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

> The `mq-lab` Vergil profile depends on the per-repo VM-spec feature tracked in
> Phase 0a of `docs/plans/2026-06-03-phase-0-dev-environment-bootstrap.md`. Until
> that lands in `vergil-vm`, `--vm mq-lab` will not resolve.

## Design

The authoritative design is `docs/specs/2026-06-03-mq-cluster-lab-design.md`.
Lab work proceeds per its §10 phases (A→G), each its own spec→plan→build.

## Workflow (Vergil-managed)

This repo is Vergil-managed (`vergil.toml`). The branching model is
`library-release`: `develop` is the integration branch, `main` is the release
branch, and **both are protected** — no direct commits.

- **Use `vrg-git` / `vrg-gh`, never raw `git` / `gh`.** The wrappers enforce
  subcommand allowlists, flag deny-lists, and credential selection. Raw `git`
  and `gh` are denied by the permission model. If something isn't available
  through a wrapper, ask the human to run it via `! <command>`.
- **All work flows through a feature branch off `develop`**, named
  `feature/<issue>-<slug>` (the `<issue>` is a GitHub issue number — open one
  first). Open a PR into `develop`.
- **Commit with `vrg-commit`** (conventional commits):
  `vrg-commit --type <type> --scope <scope> --message <msg> [--body <body>]`.
- **Validation is `vrg-container-run -- vrg-validate`** — the only validation
  command. Don't run individual linters/formatters outside it.

## Conventions

- Secrets never enter git: no MQ entitlement/license artifacts, no credentials,
  no keystores. `.gitignore` covers `*.env`, `secrets/`, `licenses/`.
  Content-plane (`pymqrest`) credentials are runtime-injected, never committed.
- Memory files require human approval before writing (per global policy).
