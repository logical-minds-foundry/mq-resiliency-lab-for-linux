# Shared Business-B svc counterparty (`SVCQM`) — design

- **Issue:** [#446](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/446)
- **Epic:** `logical-minds-foundry/.github#15` (Messaging-layer cockpit)
- **Related:** #423 (per-stack wildcarded exporters, surfaced this), #429
  (always-on app-requester round-trip signal), #431 (per-stack messaging
  board), #434, #443 (same #423-era family), #351 /
  `2026-06-25-qm-naming-convention-design.md` (short-derived QM names).
- **Status:** approved design (pushback-reviewed); implementation handed off to
  the live-lab (macOS) side per the issue — this session produces the spec,
  then the plan.

## Problem

The exporter and the deployment disagree on whether the Business-B svc
counterparty is **shared** or **per-stack**, and the disagreement produces
`MQRC 2058 (MQRC_Q_MGR_NAME_ERROR)` on every stack's svc exporter except
`nativeha-rhel`.

`src/mqlab/scrape.py` models **one svc QM per stack**: the svc QM name is
derived per stack as `qm_svc = f"{short}SVC"` (→ `NHARSVC`, `NHAUSVC`,
`PCMKSVC`, `RDQMSVC`), while the connection is a single hardcoded constant
`SVC_EXPORTER_CONN = "10.60.0.50(1414)"` used verbatim for every stack. But
`10.60.0.50` is `svc-sim`, a single host answering as exactly **one** queue
manager. An exporter connecting with QM name `NHAUSVC` to an endpoint that
answers as a different QM gets 2058 at `MQCONNX` (connect time — before TLS,
before the MAXHANDS gate of #443).

The same per-stack model is baked into **provisioning**, and it collides on a
single `svc-sim` in three distinct ways once more than one stack is up
(exactly the state #423 introduced by making all per-stack boards live at
once):

1. **Listener collision.** Every stack's `ansible/site-*.yml` imports
   `ansible/site-distributed-shared.yml` with `qm_svc = {short}SVC`, and that
   runs the `mq-qmgr` role on the single `svc` host — which always does
   `DEFINE LISTENER(L1414) ... START LISTENER(L1414)`. Two svc QMs cannot both
   own `:1414`.
2. **Channel mismatch.** The winning QM answers as (say) `NHARSVC`; a second
   stack's app-side SDR channel (`{APP}.{short}SVC`) has no matching RCVR on
   it, so its messaging fails.
3. **Responder collision.** `mq-inter-qm` writes a **fixed-named** systemd unit
   `/etc/systemd/system/mq-svc-responder.service` with `--qm {{ qmgr_name }}`;
   run per stack, each provision overwrites it — last-writer-wins on which QM
   the one responder targets.

So this is not an exporter-only defect: the per-stack svc model is broken for
messaging and for the responder too, the moment two stacks run. The original
intent was clearly per-stack **independence** (each stack made its own svc QM
+ responder); the single-host / single-port / fixed-unit-name reality broke
it.

## Decision

**Keep one shared svc queue manager, `SVCQM`, on `svc-sim`, but keep each
stack's request path independent.** Concretely (option "1b"):

- **One QM / host / listener** — a single `SVCQM` on `svc-sim:1414`. One
  listener, one server keystore/cert, one svc exporter.
- **Per-stack message path** — each stack gets its **own** request queue
  (`{SHORT}.SVC.REQUEST`) on `SVCQM`, serviced by its **own** responder
  (a templated `mq-svc-responder@{stack}` systemd unit). Inter-QM channel
  pairs are already per app QM (`{APP}.SVCQM` / `SVCQM.{APP}`).

This separates two axes the naive "share everything" fix conflated:

| Axis | Choice | Why |
|------|--------|-----|
| QM / host / listener | **shared** (`SVCQM`) | Business B is one external partner; one `svc-sim` with one `:1414` is what the topology can run; a QM-per-mechanism is conceptually wrong and cannot run in parallel anyway. |
| Request queue + responder | **per-stack** | The lab's point is watching one mechanism's flow survive failover *independently*. A shared `SVC.REQUEST` + one responder would interleave every stack's #429 stream, sum `SVC.REQUEST` depth across stacks (killing per-stack round-trip observability), and couple all stacks to one responder's health. |

A single partner QM exposing a distinct request queue per client stream is
realistic, restores per-stack independence, and costs only a template
variable + systemd instancing over the fully-shared variant.

### Reply-routing invariant (load-bearing correctness property)

Replies route to the correct stack **without** relying on unique queue names,
because they key on the message descriptor, not the queue:

- The requester stamps `MQMD.ReplyToQMgr` = its own app QM (`NHARAPP`,
  `PCMKAPP`, …) and `ReplyToQ` = `APP.REPLY`.
- The stack's responder replies with `MQPUT1(ObjectQMgrName = ReplyToQMgr)`.
  On `SVCQM` that resolves to the per-app transmission queue `QLOCAL({APP})
  USAGE(XMITQ)` → `SDR SVCQM.{APP}` → the right app QM's `APP.REPLY`.

So the per-stack request queue is what buys **independence and
observability**; the reply leg is unambiguous in every variant. This is a
checkable property for the cold-rebuild acceptance, not tribal knowledge.

### Alternatives rejected

- **1a — fully shared** (one `SVCQM`, one `SVC.REQUEST`, one responder).
  Simplest provisioning, but couples all stacks at the svc layer: `SVC.REQUEST`
  depth sums across stacks, a stuck responder breaks every stack's round-trip,
  and the per-stack cockpit can't isolate one stack's flow. Reply routing is
  still correct, but the observability/independence loss is unacceptable for a
  resiliency lab.
- **2 — per-stack svc QMs** (distinct QMs on distinct ports on `svc-sim`, or
  distinct hosts). Maximum independence + realism, closest to the original
  intent, but the heaviest provisioning: per-port listeners, per-stack
  topology-derived exporter conns (mirror `_app_qm_conn`), N svc exporters and
  N svc certs. 1b delivers the same request-path independence with one QM.

## Naming

The counterparty gets a first-class identity that honors the #351 rule
("names live in the short token; no hardcoded QM literals"):

- Business-B **short token: `SVC`**.
- Counterparty **QM name: `SVCQM`** (short + `QM` suffix, paralleling the
  app QMs' `{short}APP`).
- **Per-stack request queue: `{SHORT}.SVC.REQUEST`** (e.g. `NHAR.SVC.REQUEST`),
  derived from each app stack's `short`.
- Inter-QM channel pairs stay per app QM: `{APP}.SVCQM` (SDR from the app)
  and `SVCQM.{APP}` (SDR back). Channels are named per QM-pair, so they do
  not collide when many app QMs share one `SVCQM`.

## Design by component

### 1. Topology — a first-class Business-B block

Add a top-level `svc:` block to `lab/topology.yaml`, sibling to `stacks:`
and `commons:`, owning everything about the counterparty in one place:

```yaml
svc:
  short: SVC              # → QM name SVCQM (parallels {short}APP)
  conn: 10.60.0.50        # the inter-business WAN CONNAME (was per-stack svc_conn)
  listener_port: 1414
  exporter_port: 9158     # ONE shared svc exporter port (reused from a freed per-stack port)
```

Remove the now-redundant per-stack svc fields:

- `svc_conn` from each stack's `qm:` block (svc is no longer a per-stack
  property; the value lives once in the `svc:` block). The inline
  `svc_conn: 10.60.0.50` constants in each `site-*.yml` collapse to reading
  the single `svc.conn` value.
- `exporter_svc_port` from each stack's `alloc:` block (there is one shared
  svc exporter, not one per stack). One freed port becomes the shared
  `svc.exporter_port`; the rest are released. **Note:** `scrape.py` is the
  only consumer of `exporter_svc_port` (`cli.py` reads `exporter_app_port`),
  so freeing the ports is safe outside `scrape.py` — but see §4 for the
  skip-guard that must change with it.

### 2. `src/mqlab/stacks.py` — decouple the svc name from the stack short

- `QmConfig.svc` stops being `f"{short}SVC"`. It is populated from the `svc:`
  block (`SVCQM`) for **every** stack. `_qm_from_stack` takes the shared svc
  identity as input (read once from topology).
- Add the per-stack request-queue name (`{SHORT}.SVC.REQUEST`) as a derived
  property so the provisioning MQSC, the responder unit, and the board tile
  all read one source.
- `chl_to_svc` / `chl_to_app` remain per-app: `{APP}.SVCQM` / `SVCQM.{APP}`.
- `svc_conn` on `QmConfig` (if retained) resolves to the shared `svc.conn`.

### 3. Provisioning — create `SVCQM` once, add per-stack queue + responder

- `ansible/site-distributed-shared.yml` runs the `mq-qmgr` role with
  `qmgr_name: SVCQM`. `crtmqm` is idempotent, so the first stack provisioned
  creates it and later stacks find it present; `mq-qmgr` already applies the
  QM tuning (`MAXHANDS(512)`, `MONQ`/`STAT*`) that the concurrent responders +
  exporter need (the #443 tuning — inherited for free, `SVCQM` is created
  through `mq-qmgr`).
- `mq-inter-qm` (their-side), still run per `our_qm`, adds for **each** app QM:
  the channel pair `{APP}.SVCQM` (RCVR) / `SVCQM.{APP}` (SDR) + the
  `QLOCAL({APP})` XMITQ back, **and** the per-stack request queue
  `QLOCAL({SHORT}.SVC.REQUEST)` on `SVCQM`. These accumulate onto the one
  `SVCQM` rather than colliding.
- The responder unit becomes a **templated** `mq-svc-responder@{stack}.service`
  (systemd `@` instance) differing only by `--in-queue {SHORT}.SVC.REQUEST`
  and `--qm SVCQM` (shared `SVC.SVRCONN` channel). This gives each stack an
  independent responder **and** fixes the fixed-name last-writer-wins
  collision (problem 3 above) by construction.
- Each `ansible/site-*.yml` app-side MQSC threads the per-stack request queue
  into `QREMOTE({SHORT}.SVC.REQUEST) RNAME({SHORT}.SVC.REQUEST) RQMNAME(SVCQM)
  XMITQ(SVCQM)`, plus its `{APP}.SVCQM` SDR / `SVCQM.{APP}` RCVR pair and its
  local `QLOCAL(SVCQM)` XMITQ. The #351 QM extra-var threading
  (`src/mqlab/phases.py`, `src/mqlab/cli.py`) now resolves `qm_svc` to `SVCQM`
  for all stacks.
- **DR/CRR:** the nativeha stacks' `_nativeha-dr-replication.yml` CRR-inherits
  these app-QM svc-facing objects into the site-B Recovery group. They all
  reference the stable `SVCQM` and the per-stack request-queue name, so a DR
  cutover recovers pointing at the right counterparty with **no DR-side
  change**. (Nativeha's `our_conn` being site-A-only is a pre-existing DR
  limitation, out of scope here.)

### 4. Exporter (`src/mqlab/scrape.py`) — one svc instance, decoupled emission

Restructure `mq_exporter_instances` into two independent sources so app-side
emission is not coupled to a per-stack svc port (today `scrape.py:131` guards
the whole loop with `if not short or not app_port or not svc_port: continue`,
which — once `exporter_svc_port` is removed — would silently drop the app
exporters too):

- The **per-stack loop** emits **app** instances only, guarded on `app_port`
  alone.
- A **separate path** reads the top-level `svc:` block and emits the **one**
  `SVCQM` instance: `qm="SVCQM"`, `conn` from `svc.conn`,
  `port=svc.exporter_port`, labels `stack="commons"`, `role="svc"`.
- Delete the `SVC_EXPORTER_CONN` module constant.

Because the Prometheus `ibmmq` series are `qmgr`-keyed (verified: the boards
query `ibmmq_*{qmgr="{qm}"}`), the single `SVCQM` series feeds every per-stack
board's SVC-QM tile regardless of the `stack="commons"` file_sd label.

### 5. Cockpit / boards

- `src/mqlab/messagingboard.py` — `stack.qm.qm_svc` returns `SVCQM` for every
  stack (SVC-QM status + channel tiles read the shared QM). The
  `SVC.REQUEST`-depth tile must key on the **per-stack** request-queue name
  `{SHORT}.SVC.REQUEST` (via the new `stacks.py` property) so each board shows
  **its own** round-trip depth — this is the observability that 1b buys back.
- `src/mqlab/dashboard.py` (~L126-128) hardcodes `PCMKSVC` and its channel
  names — **retire** the legacy clusterboard `PCMKSVC` reference (consistent
  with the component-extraction roadmap superseding the old clusterboard).

### 6. Certs / inventory (`src/mqlab/cli.py`)

- The svc cert-CN dedup (`seen_svc`, ~L551-579) now genuinely collapses to
  **one** svc CN (`SVCQM`) across all stacks — the behavior the code already
  anticipates. One svc keystore/cert is generated instead of four; the
  `mq-inter-qm` role wires that one shared `SVCQM` server keystore.

### 7. Tests

- Add the regression test #446 asks for: `mq_exporter_instances` yields
  **exactly one** svc instance, with `qm == "SVCQM"` and `conn` equal to the
  `svc:` block conn (name and address consistent) — so a per-stack svc target
  cannot silently return. Assert the per-stack **app** instances are still
  emitted after `exporter_svc_port` is removed (guards against the §4
  skip-guard regression).
- Add a test that each stack derives a distinct `{SHORT}.SVC.REQUEST` and that
  the messaging board's svc-depth tile queries it.
- Update existing tests that assert per-stack `{short}SVC` names / per-stack
  svc exporter instances / per-stack `exporter_svc_port`.

### 8. Validation & acceptance

- `vrg-container-run -- vrg-validate` — the only validation command.
- Per the repo's cold-rebuild acceptance gate (provisioning-touching change,
  so lint-green is not "done"): full VM cold rebuild with **≥2 stacks up**,
  proving in one pass:
  - `svc-sim` hosts exactly one QM, `SVCQM`, owning `:1414`;
  - each running stack has its own `{SHORT}.SVC.REQUEST` + its own
    `mq-svc-responder@{stack}` unit, and its app messaging round-trips
    (`{SHORT}.SVC.REQUEST` → reply on `APP.REPLY`) **independently** of the
    other stacks;
  - the single svc exporter reads live (no `MQRC 2058`);
  - every per-stack messaging board's SVC tiles read live, and each board's
    `SVC.REQUEST`-depth tile reflects **that stack's** traffic, not the sum.

## Scope

One cohesive change spanning topology, the naming source (`stacks.py`),
provisioning (`site-distributed-shared.yml` + `mq-inter-qm` + each
`site-*.yml`), the exporter (`scrape.py`), the boards, the cert/inventory path
(`cli.py`), and tests. Cross-cutting but single-purpose (it resolves one bug's
architecture fork), so it is a **single task/spec**, not an epic. Because it
touches provisioning it is accepted only after a cold rebuild proves it;
implementation is on the live-lab side per the #446 handoff note.

## Non-goals

- No change to the app-side HA/DR mechanisms or their QMs (`{short}APP`).
- No change to how the app client reaches its app QM (`_app_qm_conn` stays
  per-stack and topology-derived — it was already correct).
- No new svc hosts or multi-port svc topology (that is the rejected option 2).
- No DR-side object changes — the Recovery group inherits the `SVCQM`-facing
  objects by CRR unchanged; nativeha's site-A-only `our_conn` DR limitation is
  pre-existing and out of scope.
