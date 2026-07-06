# Scope exporter provisioning to the deployed stack — design

- **Task:** `mq-resiliency-lab-for-linux#503`, under epic `logical-minds-foundry/.github#21`
- **Status:** design (revised — supersedes the supervisor approach), approved
- **Date:** 2026-07-06

## Problem

mon-probe runs a client-mode `mq_prometheus` exporter unit per app queue manager.
After a fresh rebuild in which **only the nha-ubuntu stack was provisioned**, mon-probe
was nonetheless running exporters for **all four** stacks — `pcmkapp`, `rdqmapp`, and
`nharapp` crash-loop (exit 10, 900+ restarts) because their queue managers were never
built. This spams the journal, churns CPU, and — because the units cycle through
`auto-restart`/`failed` rather than staying `active` — makes the Watcher's probe
"service" pill flap.

## Root cause (verified in the code)

- `mq_exporter_instances(topo)` (`src/mqlab/scrape.py`) emits **one app exporter per
  stack in the topology** + one shared `svc` (SVCQM) exporter — driven by the
  *declared* stacks, not by what is *deployed*.
- The **observe phase** (`src/mqlab/phases.py:_observe_build_steps`) is *per-stack*, but
  it renders the **full-topology** list (`mqlab obs targets`) and applies it via
  `site-obs.yml -e @mq_exporters`. So observing **any one stack** stands up exporter
  units for **all** stacks.

The exporters are coupled to the topology, not to provisioning. Separately verified:
`mq_prometheus`'s `keepRunning` (default true) already keeps an exporter alive across a
queue manager that goes down **after a first successful connect** — so a *deployed*
stack whose QM later stops (failover, DR, manual stop) never crash-loops. The **only**
crash-loop is an exporter for a stack that was **never provisioned**.

## Why not the supervisor we first sketched

An earlier design added a retry/backoff supervisor around the exporter. That was
compensating for running exporters we should never have started. Fixing the coupling
removes the crash-loop *at the source*, and the exporter's built-in `keepRunning`
already covers the deployed-then-down case. This is simpler and is the production-shaped
behavior (exporter lifecycle follows queue-manager lifecycle) the supervisor spec had
wrongly traded away. No new runtime component.

## Design

**Scope exporter-unit deployment to the stack being provisioned.**

- `mq_exporter_instances(topo, stack=None)` gains an optional stack filter: with a
  stack, it returns **that stack's app exporter + the shared `svc` commons exporter**;
  with `None` it returns the full list (unchanged).
- `mqlab obs targets --stack <name>` scopes **only** the exporter *deployment* list
  (`mq_exporters`) to that stack. The node and `ibmmq` Prometheus scrape-target files
  stay **full-topology** — a scrape of a not-yet-deployed exporter simply reads *down*,
  which is benign and correct, and those files are global (must not be clobbered
  per-stack).
- The observe phase passes `--stack <stack.name>` to its `mqlab obs targets` step, so
  `site-obs.yml` creates **only this stack's** exporter. `site-obs.yml`'s other plays
  (obs box, node-exporter) are idempotent global setup and are unaffected; the role's
  create-loop over a shorter list simply makes fewer units (Ansible does not remove the
  others, so deployed stacks accumulate their own exporters across observes).

**Result:** only deployed stacks have exporter units → the never-connected crash-loop
cannot happen → the probe "service" pill (a `min` over the exporter units) reads a stable
"running." `keepRunning` covers a deployed stack's QM going down. This satisfies #503's
"no crash-loop / stable probe pill" goal without a supervisor.

## Components & boundaries

- **`src/mqlab/scrape.py` (modified):** the `stack` filter on `mq_exporter_instances`,
  threaded through `render_mq_exporters` / `lab_mq_exporters`. Pure functions.
- **`src/mqlab/cli.py` (modified):** `obs targets --stack <name>` scopes the exporter
  deployment render only.
- **`src/mqlab/phases.py` (modified):** the observe phase passes `--stack` for the
  current stack.
- **No `mq-exporter` role / unit change** — the fix is purely *which* instances the
  role is asked to create.

## Testing

- Unit tests to the repo's 100%-branch gate: `mq_exporter_instances(topo, stack=X)`
  returns exactly X's app exporter + the `svc` commons entry (and nothing for other
  stacks); `stack=None` returns the full list; the render/`lab_` wrappers thread the
  scope; the observe phase emits the `--stack` argument for its stack.
- `ansible-lint` via `vrg-validate` (no role change, but the pipeline covers it).
- **Cold-boot proof (operator):** fresh rebuild, bootstrap only the nha-ubuntu stack →
  mon-probe carries only the `nhauapp` + `svcqm` units (both `active`, no restart
  churn), the journal is quiet, and the Watcher probe pill reads a stable "running";
  bootstrapping another stack later stands up *its* exporter and no others.

## Out of scope

- **Teardown removal.** Destroying a *provisioned* stack currently leaves its exporter
  unit; because that exporter connected once, `keepRunning` makes it idle-and-reconnect
  rather than crash-loop, so removal is cleanliness, not correctness — deferred.
- The **app-client requester** may have a similar "crashes when its target app-QM is
  down" coupling — worth checking separately, not widened into this task.
