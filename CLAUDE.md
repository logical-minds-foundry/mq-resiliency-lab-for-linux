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

## Conventions

- Secrets never enter git: no MQ entitlement/license artifacts, no credentials,
  no keystores. `.gitignore` covers `*.env`, `secrets/`, `licenses/`.
  Content-plane (`pymqrest`) credentials are runtime-injected, never committed.
- Memory files require human approval before writing (per global policy).
