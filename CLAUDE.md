# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## Project overview

mq-resiliency-lab-for-linux is a signed, downloadable lab that stands up IBM MQ
high-availability and disaster-recovery topologies end to end. Its core is
`mqlab` (`src/mqlab/`), a Python orchestrator, with the Ansible roles under
`ansible/` that provision each node. The branching model is `library-release`:
`develop` integrates, `main` releases.

## Development environment

The repo is developed inside an ephemeral Vergil VM declared as the
`[vm.vergil-user]` profile in [`vergil.toml`](vergil.toml). The VM commands are
in the README's [Development](README.md#development) section.

- The VM is reproducible: never hand-customize it. Change tooling by editing the
  profile and rebuilding the VM, never by `apt install` inside a live VM.
- Run `mqlab` as `uv run mqlab …` from a checkout, so the venv's tools resolve
  (see [develop.md](docs/site/docs/develop.md)).
- All working state lives in the gitignored, host-mounted `build/` directory,
  split into the `cache/`, `state/`, `work/` and `temp/` buckets. Never hardcode
  a `build/<X>` path: resolve a bucket with `uv run mqlab build path <bucket>`.
  Bucket semantics and `mqlab build` verbs:
  [`docs/development/build-layout.md`](docs/development/build-layout.md).

## Design

The authoritative design is
[`docs/specs/2026-06-03-mq-cluster-lab-design.md`](docs/specs/2026-06-03-mq-cluster-lab-design.md).
Its "Decision & Pivot (2026-06-15)" section supersedes the framing of §0–§2 and
§10. Each piece of lab work gets its own spec → plan → build under
`docs/specs/` and `docs/plans/`.

## Secrets

Secrets never enter git: no MQ entitlement or license artifacts, no
credentials, no keystores. `.gitignore` covers `*.env`, `secrets/` and
`licenses/`. The mqweb (MQ Console and admin REST API) secret is
`mqweb_aes_passphrase`, from which each node's AES key for the encoded
`mqwebuser.xml` passwords is made; it is runtime-injected, never committed. The
three mqweb logins (`mqadmin`, `mqreadonly`, `mqoperator`) use password =
username by lab convention; they are lab-only sample logins, not secrets.

## External documentation (IBM Docs)

IBM Docs return HTTP 403 to the built-in `WebFetch` (a bot user-agent block, not
auth). Fetch `ibm.com/docs/...` pages with the repo tool instead:

```bash
python3 tools/ibm_doc_cache.py "https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=..."
```

It caches the canonical text in the main checkout's shared cache bucket, under
`refs/ibm-docs/<product>/<version>/<slug>/` (old-style
`/docs/<SScode>_<version>/...` URLs are keyed `<SScode>/<version>/`). Cite
`content.txt`, with the `source_url` from `meta.json`.

The tool resolves `ibm.com/docs/...` only. Plain `curl` with a browser
`User-Agent` (the headers the tool sends) works for IBM Support pages
(`ibm.com/support/pages/...`) and the SPCR compatibility-report JSON endpoints
(`getTSROsSupportSummary`, `getDeliverableChain`; see
[`docs/reference/os-version-support-matrix.md`](docs/reference/os-version-support-matrix.md)
§6.1 and §8). Cite what you fetch that way and list each such page as a
human-verify item, so a person confirms it in a browser. JavaScript-rendered
pages (for example IBM announcements) still return HTTP 403; fetch those
manually (paste the text, or download a PDF into `build/temp/`).

<!-- vergil:template:claude-md:begin -->
## Memory

Never write, edit or delete memory files without explicit human approval.
Propose the entry and where it belongs first, then wait for the decision.

## Worktrees

- Sessions always start at the project root, never inside `.worktrees/`.
- One worktree per issue: `.worktrees/issue-<N>-<slug>/` on branch
  `feature/<N>-<slug>`. Remove it once its branch lands.
- The main worktree is read-only; every edit goes through a worktree.
- Read, Edit and Write use the worktree's absolute path. Bash commands `cd`
  into the worktree first or use absolute paths.
- The full convention: <https://github.com/vergil-project/vergil-tooling/blob/develop/docs/specs/worktree-convention.md>.
- Brief each parallel sub-agent with its [Agent prompt contract](https://github.com/vergil-project/vergil-tooling/blob/develop/docs/specs/worktree-convention.md#agent-prompt-contract).

## Git and GitHub

Use `vrg-git` and `vrg-gh` only; the hook guard blocks raw `git` and `gh`. If a
command is not available through the wrappers, explain why and ask the human to
run it with `! <command>` in the prompt.

## Validation

`vrg-container-run -- vrg-validate` is the only validation command. Don't run
linters, formatters or tests outside it; a tool it doesn't invoke is not part
of the pipeline.

## PR handoff

- Agents never run `vrg-submit-pr`. Submitting, merging and finalizing a PR are
  human actions.
- Hand off with `vrg-pr-workflow report-ready --issue <N> --title --summary
  --notes`. It pushes the branch itself. Leave `--linkage` at its default (`Ref`).
- A delta touching `.github/workflows/**` makes GitHub refuse the agent push.
  That is expected: report-ready records the state anyway and the human's
  `vrg-submit-pr` pushes the branch.
- After report-ready the branch is frozen: no new commits and no push that
  advances it; more work is a new follow-up issue. Re-running report-ready to
  fix the prose is allowed. `vrg-pr-workflow unfreeze` is the only way to lift it.
- The report-ready title, summary and notes are public (the relay ref is
  world-readable): never put secrets in them.

## Local settings

`.claude/settings.local.json` must stay absent or exactly `{}`; the agent tools
refuse otherwise. See the [ownership model](https://github.com/vergil-project/vergil-tooling/blob/develop/docs/site/docs/guides/claude-config-ownership.md).

## Engineering principles

- Correctness over cost over speed. Never ship known-broken code with a
  follow-up fix; if something is wrong, fix it now.
- No silent failures: no swallowed exceptions, no `except: return ""`, no
  fallbacks that hide errors. A failure hidden in code becomes a hallucination
  at the AI layer.

## paad

paad usage policy and deviations:
<https://github.com/vergil-project/vergil-claude-plugin/blob/develop/docs/paad-policy.md>.
<!-- vergil:template:claude-md:end -->
