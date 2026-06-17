# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

**Project name**: mq-cluster-tooling

## Development environment

This repo is developed inside an ephemeral Vergil VM, **not** on macOS directly.
The VM's footprint and installed packages are declared as the `[vm.vergil-user]`
profile in [`vergil.toml`](vergil.toml) — there is no separate spec file.

- Build the VM once:
  `vrg-vm create logical-minds-foundry/mq-cluster-tooling --identity vergil-user`
- Work in it:
  `vrg-vm session logical-minds-foundry/mq-cluster-tooling --identity vergil-user`
- The VM is **ephemeral and 100% reproducible**. Do not hand-customize it.
  Re-provision freely to stay fresh:
  `vrg-vm rebuild logical-minds-foundry/mq-cluster-tooling --identity vergil-user`.
- All working state lives in the gitignored `build/` directory (mounted from
  the host). Nothing in the VM outside `build/` is precious.
- Change tooling by editing the `[vm.vergil-user]` profile in `vergil.toml` and
  rebuilding — never by `apt install` inside a live VM.

> The `vergil-user` profile depends on the per-repo VM-profile feature shipped in
> Vergil v2.1 (`apt_repos` + `vagrant_plugins`); this repo pins `vergil = "v2.1"`.

## Design

The authoritative design is `docs/specs/2026-06-03-mq-cluster-lab-design.md`.
Lab work proceeds per its §10 phases (A→G), each its own spec→plan→build.

## Workflow (Vergil-managed)

This repo is Vergil-managed (`vergil.toml`). The branching model is
`library-release`: `develop` is the integration branch, `main` is the release
branch, and **both are protected** — no direct commits.

- All work flows through a feature branch off `develop`, named
  `feature/<issue>-<slug>` (the `<issue>` is a GitHub issue number — open one
  first). Open a PR into `develop`.
- Commit with `vrg-commit` (conventional commits):
  `vrg-commit --type <type> --scope <scope> --message <msg> [--body <body>]`.

## Secrets

Secrets never enter git: no MQ entitlement/license artifacts, no credentials,
no keystores. `.gitignore` covers `*.env`, `secrets/`, `licenses/`.
Administrative REST API (`pymqrest`) credentials — the `MQWEB_ADMIN_*` mqweb
login — are runtime-injected, never committed.

## Memory management

Memory is allowed with human approval. The authoritative policy is in
the user's global `~/.claude/CLAUDE.md` — agents must propose memory
writes and suggest a destination (repo memory, global CLAUDE.md, or
plugin/skill issue) before writing. See that file for the full
workflow.

Available skills:
- `/vergil:memory-init` — set up or update the policy header
  in a project's `MEMORY.md`.
- `/vergil:memory-audit` — structured collaborative review
  of memory files.

## Parallel AI agent development

This repository supports running multiple Claude Code agents in parallel via
git worktrees. The convention keeps parallel agents' working trees isolated
while preserving shared project memory (which Claude Code derives from the
session's starting CWD).

**Canonical spec:**
[`vergil-tooling/docs/specs/worktree-convention.md`](https://github.com/vergil-project/vergil-tooling/blob/develop/docs/specs/worktree-convention.md)
— full rationale, trust model, failure modes, and memory-path implications.
The canonical text lives in `vergil-tooling`; this section is the local
on-ramp.

### Structure

```text
<project-root>/                              ← sessions ALWAYS start here
  .git/
  CLAUDE.md, …                               ← main worktree (usually `develop`)
  .worktrees/                                ← container for parallel worktrees
    issue-<N>-<short-slug>/                  ← worktree on feature/<N>-<short-slug>
    …
```

### Rules

1. **Sessions always start at the project root.**
   Never start Claude from inside `.worktrees/<name>/`. This keeps the
   memory-path slug stable and shared.
2. **Each parallel agent is assigned exactly one worktree.** The session
   prompt names the worktree (see Agent prompt contract below).
   - For Read / Edit / Write tools: use the worktree's absolute path.
   - For Bash commands that touch files: `cd` into the worktree first,
     or use absolute paths.
3. **The main worktree is read-only.** All edits flow through a worktree
   on a feature branch — the logical endpoint of the standing
   "no direct commits to develop" policy.
4. **One worktree per issue.** Don't stack in-flight issues. When a
   branch lands, remove the worktree before starting the next.
5. **Naming: `issue-<N>-<short-slug>`.** `<N>` is the GitHub issue
   number; `<short-slug>` is 2–4 kebab-case tokens.

### Agent prompt contract

When launching a parallel-agent session, use this template (fill in the
placeholders):

```text
You are working on issue #<N>: <issue title>.

Your worktree is: <project-root>/.worktrees/issue-<N>-<slug>/
Your branch is:   feature/<N>-<slug>

Rules for this session:
- Do all git operations from inside your worktree:
    cd <absolute-worktree-path> && vrg-git <command>
- For Read / Edit / Write tools, use the absolute worktree path.
- For Bash commands that touch files, cd into the worktree first
  or use absolute paths.
- Do not edit files at the project root. The main worktree is
  read-only — all changes flow through your worktree on your
  feature branch.
- When you need to run validation, run it from inside your worktree
  (vrg-container-run mounts the current directory).
```

All fields are required.

## Shell command policy

Use `vrg-git` instead of `git` for all git operations. Use `vrg-gh`
instead of `gh` for all GitHub CLI operations. These wrappers enforce
subcommand allowlists, flag deny lists, and credential selection.

Raw `git` and `gh` are denied by the permission model. If a command
is not available through the wrappers, explain the situation to the
human who can run it directly via `! <command>` in the prompt.

## Validation

```bash
vrg-container-run -- vrg-validate
```

This is the **only** validation command. Do not run individual linters,
formatters, or other tools outside of `vrg-validate`. If a tool is not
invoked by `vrg-validate`, it is not part of the validation pipeline.
