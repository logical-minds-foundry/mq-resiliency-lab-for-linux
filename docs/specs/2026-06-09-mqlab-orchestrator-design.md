# `mqlab` — Operator Orchestrator for the MQ Cluster Lab — Design

- **Date:** 2026-06-09
- **Status:** Design — brainstormed; pushback applied 2026-06-09
- **Issue:** #32 — broadened during the brainstorm from "ops/observability tooling"
  (health checks + a `status` verb) to **the full operator orchestrator**, with
  observability as one slice. This riff supersedes the original issue body.
- **Relationship:** realizes the **§8.4 operator verbs** and **§8.6/§8.7
  operational standards** of `2026-06-03-mq-cluster-lab-design.md`; the
  health-check/gate primitives are the ones issue #32 seeded from `pymqrest`'s
  example tools; it drives the scripts/playbooks built across Phases A–D and the
  `dr/` framework of `2026-06-08-dr-ha-validation-framework-design.md`. It does
  **not** replace any of them — it runs and exposes them.

## Table of contents

- [1. Problem & motivation](#1-problem--motivation)
- [2. Goals & non-goals](#2-goals--non-goals)
- [3. Design principles — the inversion](#3-design-principles--the-inversion)
- [4. Architecture](#4-architecture)
  - [4.1 The step model](#41-the-step-model)
  - [4.2 Run modes — run-through and `--step`](#42-run-modes--run-through-and---step)
  - [4.3 Transcript capture & evidence](#43-transcript-capture--evidence)
  - [4.4 Rendering — treatment A](#44-rendering--treatment-a)
  - [4.5 Checks are gates are health — one primitive](#45-checks-are-gates-are-health--one-primitive)
  - [4.6 Granularity follows risk](#46-granularity-follows-risk)
- [5. Command surface](#5-command-surface)
- [6. Technology & code structure](#6-technology--code-structure)
- [7. First vertical slice — `mqlab net`](#7-first-vertical-slice--mqlab-net)
- [8. Relationship to the lab design & issue #32](#8-relationship-to-the-lab-design--issue-32)
- [9. Open questions](#9-open-questions)
- [10. Success criteria](#10-success-criteria)

---

## 1. Problem & motivation

The lab now produces real running stacks: networks, guests, a standalone QM and
message path, the RDQM HA arm, the Pacemaker/SAN arm, DR in flight, and a
continuous-flow DR/HA validation framework. Today an **AI agent** drives all of
it — bringing nets up and down, standing up clusters, installing and configuring
MQ, running fault drills. A **human** cannot yet sit down, get a bash shell in the
VM, and drive the same lifecycle easily.

The obvious fix is "wrap it in a CLI." The non-obvious part — and the whole point
of this tool — is that it must do the **opposite of what tooling normally does.**
Every other tool in this ecosystem (and Vergil's tooling broadly) exists to
**encapsulate** complexity behind a clean interface so you need not think about
it. `mqlab` exists to **expose** complexity so you are forced to see it.

**Why.** The headline deliverable of this engagement is an objective, defensible
recommendation between two HA/DR approaches — **RDQM on RHEL** vs. **Pacemaker/SAN
on Ubuntu** (§10-E). The vendor is pushing Red Hat; the client wants to know
whether Ubuntu is genuinely viable. The recommendation can only be made — and
defended — in the client's interest if we can **show, mechanic-for-mechanic, what
each arm actually costs to stand up and operate.** A tool that hides "look how
easy the cluster is" would destroy the very evidence we are here to produce. So
`mqlab` is an **instrument for generating transparent, reproducible evidence**,
not a convenience layer.

## 2. Goals & non-goals

**Goals.**

- Let a human drive the full lab lifecycle from a bash shell with a small,
  memorable verb set.
- **Expose** every underlying command — shell scripts, `virsh`, Ansible plays,
  `runmqsc`, `pymqrest` calls — verbatim as they run, so a human can watch,
  understand, then reproduce them by hand in a login shell.
- Produce a durable, diffable transcript of every run so the **same verb against
  each arm** can be compared and quoted in the §10-E write-up.
- Provide both a watch-it-all run-through and a step-by-step mode for poking at
  the live system between steps.
- Provide ground-truth precondition checks usable both standalone (inspect) and
  inline (gate a sequence) — fail-loud, exit-non-zero.
- Keep the getting-started walkthrough current with each slice — a shipped verb
  is documented as a watchable walkthrough, not just code.

**Non-goals.**

- **Not** a reusable framework. `mqlab` is bespoke to *this* architecture.
  Replicability is a **pattern** outcome: a future lab repo copies the shape and
  gets its own bespoke entrypoint named for that instance. No plugin layer, no
  "any lab" abstraction. YAGNI, hard.
- **Not** a production operations tool. It is the lab instrument only (§1 of the
  lab design puts security hardening out of scope; the real client environment
  will demand re-implementation).
- **Not** an encapsulation of the mechanics. It never hides what it runs. A
  quiet/summary mode is the *only* thing that would ever be an opt-in add-on —
  the inverse of a normal CLI's `--verbose`.
- **Not** a re-implementation of the verified procedures. Where a procedure is
  complex and proven, `mqlab` wraps and streams it rather than re-risking it
  (§4.6).

## 3. Design principles — the inversion

1. **Expose, don't encapsulate.** Visibility of the mechanics is the product.
2. **Orchestrator, not CLI.** The command layer is a thin veneer; the substance
   is sequencing and exposing the real scripts/Ansible underneath. Do not
   over-build the command surface.
3. **Wrappers over real mechanics.** The scripts and playbooks remain the source
   of truth for *how* things are done. We groom them to be more human-legible and
   standalone-runnable as needed; `mqlab` runs them and shows them.
4. **Verbose-by-default is the product**, not a debug flag.
5. **Reproducibility is a headline feature.** Everything is redo-able infinitely.
   Breaking the lab is fine — `mqlab nuke` and rebuild from scratch. The lab is a
   disposable illusion; all credentials are auto-generated, throwaway, and
   reproducible, so transcripts capture everything freely with no scrubbing.
6. **Fail loud.** No swallowed exceptions, no silent stderr suppression, bounded
   waits, assertions against **ground truth** (not against `mqlab`'s own
   wrappers). A non-zero exit from any underlying command fails the run loudly and
   propagates; exit codes are never masked.
7. **Overkill welcome.** Where a capability is cheap and serves the evidence
   goal, build it.

## 4. Architecture

`mqlab` is a Python orchestrator that turns each operator **verb** into an ordered
list of **steps**, runs them while rendering exactly what is happening (§4.4),
and tees the whole thing to a transcript (§4.3).

### 4.1 The step model

A verb resolves to a list of steps. A step is one of three kinds:

- **Command step** — a shell script, `virsh`/`vagrant`/`ansible-playbook`
  invocation, `runmqsc`, or a `pymqrest` call. The literal command line is echoed
  verbatim (treatment A), then executed with output streamed raw, line by line.
  The step's exit status is checked; non-zero fails the run (principle 6).
- **Gate step** — a ground-truth check (§4.5) that must pass before the sequence
  proceeds. On failure it halts the run loudly with the unmet condition stated.
- **Advisory step** — printed guidance: what just happened, the recommended next
  step, or a flag that an obvious next step is one the operator runs **manually**.
  Advisories never block.

Of these, the **`net` slice implements command steps only**. Gate and advisory
steps are introduced by the first slice that needs them (e.g. the `dr`
convergence gate); building them earlier would be speculative (principle 2).

**The `CommandRunner` seam.** Every command step executes through a single
`CommandRunner` protocol — the *only* component that touches `subprocess`. The
real runner spawns, streams stdout/stderr line by line, and returns the exit
status; a fake/recording runner is injected in tests so the entire orchestration
core (step sequencing, stream handling, exit-code propagation, the `--step`
boundary) is unit-testable with no live lab. This seam is deliberately
MQ-agnostic: it — with the renderer, transcript tee, and step model — is the
**liftable nucleus** a future lab repo could copy, while the verbs and recipes
stay bespoke. We keep it clean for that reason, but build no reuse machinery now
(non-goal: not a framework). The same discipline that makes it testable makes it
portable.

### 4.2 Run modes — run-through and `--step`

- **Run-through (default).** Execute all steps back-to-back; the operator watches
  the stream.
- **`--step`.** Pause after each step and wait for the operator to continue. This
  is the *learn/poke* mode: between steps you read the generated config files,
  inspect the process table, look at how the hosts file was built, SSH into a
  guest and prod it. Both modes are first-class.

Gate steps halt in **both** modes — they are preconditions, not teaching pauses.

**Interactivity.** Run-through is fully non-interactive — it never reads input, so
it is safe in tests, CI, and scripts. `--step` requires an interactive terminal
and reads the continue keypress from **`/dev/tty`** (not `stdin`), so it survives
the transcript tee and never consumes piped input; with no TTY it **fails fast**
with a clear message rather than hanging. The pause boundary is covered in tests
through the same runner seam (§4.1).

### 4.3 Transcript capture & evidence

Every run tees its full output — the echoed commands plus raw output, exactly what
scrolled past — to `build/runs/<UTC-timestamp>-<verb>.log`. **`mqlab` only ever
writes transcripts under the gitignored `build/` tree**; it never writes to a
committed path. This makes the same verb against each arm diffable and quotable in
the §10-E comparison while keeping the permanent record clean.

Because the lab's credentials are throwaway and reproducible, the disposable
`build/` capture needs no scrubbing. But that freedom stops at the git boundary:
nothing credential-shaped should enter committed history (repo Secrets policy).
So there is **no automatic promotion into `docs/reports/`** — keepable runs simply
live in the build tree ("we wrote them here, come get them"). Archiving a specific
run for the record is a deliberate, out-of-band human action (save it, open an
issue, integrate it properly), not an `mqlab` feature. The guard is **structural**:
`build/*` is gitignored and `mqlab` writes transcripts nowhere else, so evidence
cannot accidentally land in git.

### 4.4 Rendering — treatment A

The renderer is the **annotated transcript** (selected during the brainstorm over
a status-tree/side-panel alternative): full terminal width, each underlying
command printed verbatim and visually distinct (copy-pasteable), raw output
beneath it, a per-item status marker and elapsed time, and a compact running
summary line. Full width is non-negotiable — these commands and their output are
long, and the point is to see them whole. Rich provides the styling; the content
is the literal mechanics, never a glossed summary.

For verbs that **wrap** a script (§4.6), the running summary is per-*step* (steps
completed, total elapsed); per-*item* counts (e.g. "N of 9 networks") are not
synthesized by `mqlab`, because the wrapped script owns the loop — its own
per-item lines stream through as output.

### 4.5 Checks are gates are health — one primitive

A **check** asserts one condition against ground truth and exits non-zero when
unmet — e.g. QM liveness, channel state, queue depth / DLQ, HA role with
exactly-one-primary, DRBD sync state, DR replication lag / convergence-to-zero.
The same check object serves three roles:

- **Standalone** (`mqlab check <name>`) — inspect a condition and decide for
  yourself. Alert-friendly exit codes.
- **Inline gate** (a gate step in a sequence) — refuse to let an operation proceed
  until the condition holds (e.g. §8.5's clean DR cutover refuses unless
  replication has converged to zero).
- **Aggregated** (`mqlab status`) — the topology/HA/DR/VIP dashboard composes many
  checks into one picture.

This unifies issue #32's health checks with the orchestrator's safety mechanism:
**one library of checks, three uses.** QM-facing checks build on `pymqrest`'s
example tools (`health_check`, `channel_status`, `queue_depth_monitor`,
`dlq_inspector`); host-level checks parse `rdqmstatus`/`dspmq`/`virsh`/`pcs`
directly. Checks assert against the real tools, never against `mqlab`'s wrappers.

### 4.6 Granularity follows risk

Granularity is chosen **per verb**, and the default is to **wrap an existing
script rather than re-implement its loop** — this keeps a single source of truth
and honors principle 3 (wrappers over real mechanics):

- **Default — wrap a groomed, self-echoing script.** Where a script already
  encodes the command sequence (and may be used elsewhere — e.g. `net-down.sh` is
  also a DR fault injector), `mqlab` wraps and streams it. The script is groomed to
  **echo each command verbatim before running it** (a `run() { echo "+ $*"; "$@"; }`
  helper, as `rdqm-qm-create.sh` already does), so treatment A surfaces the
  script's own commands — the most faithful "reproduce by hand" reference, because
  the commands shown *are* the script's. This covers `net up`/`down` and the
  Ansible bring-ups (Ansible's play/task stream supplies granularity). Re-risking
  verified procedures (HA formation, DR cutover/failback) by re-implementing them
  is explicitly avoided this way.
- **Own the loop only when warranted** — when no script exists, or when per-step
  `--step` pausing genuinely adds value. A read like `net status` (no script
  exists) is driven directly by `mqlab`.

The tradeoff of wrapping is coarser `--step` granularity (the whole script is one
step); for cheap sequences like network bring-up, pausing mid-sequence has little
value, so this is the right thing to give up.

## 5. Command surface

Domain groups, with the **arm named explicitly** wherever an operation is
arm-specific (both arms have site-A nodes, so the arm cannot be inferred). Same
operator verb across both arms (§8.4); the differing mechanics become visible when
you watch it run — that contrast is the apples-to-apples teaching moment.

| Group | Verbs | Wraps / drives |
|---|---|---|
| `mqlab net` | `up · down · status` | `virsh` per network; `lab/scripts/net-up.sh` / `net-down.sh` as reference |
| `mqlab vms` | `up · down · status · ssh` | `vagrant` lifecycle over `lab/topology.yaml` |
| `mqlab setup` | `--arm rdqm\|pcmk [--site a\|b]` | the Ansible bring-up plays (`ansible/site-*.yml`) |
| `mqlab ha` | `form-group · status · failover · suspend · resume --arm …` | `rdqm-qm-create.sh` / pcmk plays |
| `mqlab dr` | `cutover · failback · status --arm …` | `pcmk-dr-cutover.sh` + the convergence gate |
| `mqlab test` | `smoke · e2e · fault <name> · dr-validate` | `smoke-test.sh` / `e2e-test.sh` / the `dr/` framework |
| `mqlab status` | aggregate topology / HA / DR / VIP dashboard | composed checks (§4.5) |
| `mqlab check` | `<name>` — run one ground-truth check standalone | the check/gate/health primitives |
| `mqlab nuke` | tear the whole lab down for a clean rebuild | `vms down --destroy` + `net down` |

Global flags available on every verb: `--step` (§4.2). The verb set grows
incrementally; each group past the first slice is its own spec→plan→build.

## 6. Technology & code structure

- **Language:** Python, extending the existing `mqlab` package (`src/mqlab/`).
- **Command tree:** **Typer** — clean grouped subcommands, good `--help`.
- **Rendering:** **Rich** — treatment-A annotated transcript (§4.4). Both are new
  dependencies added to `pyproject.toml`.
- **Likely module shape** (to be firmed in the plan):
  - `mqlab.cli` — Typer app, the domain groups, global `--step`.
  - `mqlab.runner` — the `CommandRunner` protocol and its real + fake/recording
    implementations; the only code that touches `subprocess` (§4.1). The
    MQ-agnostic, liftable nucleus.
  - `mqlab.orchestrator` — the step model (§4.1), the run loop and `--step`
    boundary (§4.2), exit-code propagation (principle 6).
  - `mqlab.render` — the Rich treatment-A renderer (§4.4).
  - `mqlab.transcript` — the `build/runs/` tee; never writes outside `build/` (§4.3).
  - `mqlab.checks` — the check/gate/health library (§4.5), reused by `check`,
    gate steps, and `status`.
- **Testing:** the orchestrator core (step model, run loop, transcript, exit-code
  propagation, the `--step` pause boundary, check pass/fail) is unit-tested with
  the command layer abstracted behind a runner seam so tests do not require a live
  lab. The repo's gates apply: `uv run pytest`, 100% branch coverage, ruff
  (StrEnum/`UP042`, magic-comma), mypy strict, via `vrg-container-run -- vrg-validate`.

## 7. First vertical slice — `mqlab net`

Build `mqlab net up | down | status` end-to-end first. It has the lowest
dependencies, is run constantly, and exercises the **entire** pattern, proving the
skeleton before any heavier arm:

- **`net up` / `net down`** — wrap the groomed, self-echoing `net-up.sh` /
  `net-down.sh` (§4.6): `mqlab` streams the script while treatment A surfaces each
  `virsh net-define`/`net-start`/`net-autostart` (and the inverse) verbatim, with
  per-network status and elapsed time. Grooming the two scripts to echo each
  command is part of this slice; idempotency stays where it already is, in the
  scripts.
- **`net status`** — no script exists, so `mqlab` drives `virsh net-list --all`
  directly and renders which lab networks are defined / active / autostart, showing
  the `virsh` command it ran (a `check`-style observe, §4.5).

This slice delivers the reusable core: the step model and the `CommandRunner` seam
(§4.1), the Rich treatment-A renderer, the transcript tee, the run-through/`--step`
loop, and the Typer app skeleton with the `net` group. `net-up.sh` / `net-down.sh`
remain the single source of the bring-up sequence and the hand-runnable reference;
grooming them to self-echo is part of this slice.

**Done when:** a human can run `mqlab net up`, `mqlab net down`, and
`mqlab net status` in the VM; watch every `virsh` command and its output; replay
them by hand; find a complete transcript in `build/runs/`; and step through with
`--step`. Then we take the learnings and design the remaining groups.

## 8. Relationship to the lab design & issue #32

- Realizes the **§8.4** operator verbs (`form-group`, `status`, `failover`, …,
  cutover/failback) as the `mqlab ha` / `mqlab dr` groups.
- Realizes **§8.6** operational standards: the health checks become the
  check/gate/health primitive (§4.5); runbooks are the verbs themselves plus their
  transcripts.
- Realizes **§8.7** recovery & diagnostics as a later `mqlab` verb wrapping
  `runmqras` + `pymqrest` live-state capture (out of the first slice).
- **Issue #32** is broadened to this orchestrator; its original observability
  scope (`status` + checks) is the slice that lands immediately after `net`.
- The `mqlab test dr-validate` verb drives the framework specified in
  `2026-06-08-dr-ha-validation-framework-design.md`.

## 9. Open questions

- **Step granularity for `setup` / `ha` / `dr`.** Resolve per §4.6 when those
  slices are built, informed by the `net` slice — in particular whether any
  verified script is decomposed for finer `--step` pauses or wrapped whole.
- **`vms ssh` ergonomics.** Thin pass-through to `vagrant ssh`, or a richer
  multi-host helper? Defer to the `vms` slice.
- **Arm selection default.** Whether `--arm` is always required or can default
  from a session/context setting. Defer to the `setup`/`ha` slices.

## 10. Success criteria

1. The `net` slice meets its done bar (§7).
2. Every command `mqlab` runs is visible verbatim and reproducible by hand from
   the transcript.
3. A run leaves a complete transcript in `build/runs/`, and `mqlab` writes
   transcripts nowhere else (no committed path).
4. Run-through is non-interactive; `--step` halts between steps and resumes
   cleanly via `/dev/tty`, fails fast with no TTY. (Gate/advisory step kinds and
   the "gates halt in both modes" rule are forward-looking — first implemented by
   the slice that needs them; the `net` slice's step model is command-steps-only.)
5. A failed underlying command fails the `mqlab` run loudly with a non-zero exit;
   nothing is swallowed.
6. The command surface stays thin — no abstraction beyond what the verbs need
   (principle 2, non-goal: not a framework).
7. The orchestrator core (the `CommandRunner` seam, step loop, `--step` boundary,
   checks) is unit-tested with no live lab via the fake/recording runner.
8. `vrg-validate` is green (ruff, mypy strict, 100% branch coverage).
