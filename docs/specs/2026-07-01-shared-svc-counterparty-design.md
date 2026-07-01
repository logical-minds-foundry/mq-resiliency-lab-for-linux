# Shared Business-B svc counterparty (`SVCQM`) — design

- **Issue:** [#446](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/446)
- **Epic:** `logical-minds-foundry/.github#15` (Messaging-layer cockpit)
- **Related:** #423 (per-stack wildcarded exporters, surfaced this), #434, #443
  (same #423-era family), #351 / `2026-06-25-qm-naming-convention-design.md`
  (short-derived QM names).
- **Status:** approved design; implementation handed off to the live-lab
  (macOS) side per the issue — this session produces the spec, then the plan.

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

The same per-stack model is baked into **provisioning**, so this is not an
exporter-only defect. Every stack's `ansible/site-*.yml` imports
`ansible/site-distributed-shared.yml` with `qm_svc = {short}SVC`, and that
playbook runs the `mq-qmgr` role on the single `svc` host — which always does
`DEFINE LISTENER(L1414) ... START LISTENER(L1414)`. Provision two stacks and
`svc-sim` gets two svc QMs both trying to own `:1414`; only one wins, and the
second stack's app-side SDR channel (`{APP}.{short}SVC`) has no matching RCVR
on the QM that actually answers. **The per-stack svc model is broken for
messaging too, not just for the exporter, the moment more than one stack is
up** — which is exactly the state #423 (all per-stack boards live at once)
introduced.

## Decision

**Model Business B as a single shared counterparty: one svc queue manager,
`SVCQM`, on `svc-sim`.**

Rationale:

- **Domain.** Business B is one external partner. Each app stack
  (`pcmk-ubuntu`, `rdqm-rhel`, `nativeha-rhel`, `nativeha-ubuntu`) is a
  different HA/DR *implementation* of the same Business-A app QM role, all
  talking to the same Business B. Business B neither knows nor cares how
  Business A implements HA, so a separate svc QM per mechanism is
  conceptually wrong.
- **Deployment reality.** There is one `svc-sim` host with one `:1414`
  listener. A single shared svc QM is what the topology can actually run;
  the per-stack model can never have more than one svc QM live at a time.
- **Consistency with existing code.** The cert-CN builder in
  `src/mqlab/cli.py` (~L551) already *dedupes* the svc CN across stacks
  (`seen_svc`) — it was written anticipating a single shared svc identity.

Alternatives rejected:

- **Per-stack svc QMs on distinct ports.** Keep `{short}SVC`, give each svc
  QM its own port on `svc-sim`, derive the exporter conn per stack from
  topology, and provision N svc QMs. Larger topology/provisioning surface and
  models Business B as four partners — contradicts the story.
- **Exporter-only patch.** Point the exporter at "the" svc QM without
  touching provisioning. Fragile: which `{short}SVC` name actually exists is
  nondeterministic on a shared `svc-sim` (whichever stack grabbed `:1414`),
  so it is not a stable fix. Rejected — it papers over the contradiction.

## Naming

The counterparty gets a first-class identity that honors the #351 rule
("names live in the short token; no hardcoded QM literals"):

- Business-B **short token: `SVC`**.
- Counterparty **QM name: `SVCQM`** (short + `QM` suffix, paralleling the
  app QMs' `{short}APP`).
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
  property; the value lives once in the `svc:` block).
- `exporter_svc_port` from each stack's `alloc:` block (there is one shared
  svc exporter, not one per stack). One freed port becomes the shared
  `svc.exporter_port`; the rest are released.

The per-stack `svc_conn` currently also appears as an inline constant in each
`site-*.yml` (`svc_conn: 10.60.0.50`); those collapse to reading the single
`svc.conn` value.

### 2. `src/mqlab/stacks.py` — decouple the svc name from the stack short

- `QmConfig.svc` stops being `f"{short}SVC"`. It is populated from the `svc:`
  block (`SVCQM`) for **every** stack. `_qm_from_stack` takes the shared svc
  identity as input (read once from topology).
- `chl_to_svc` / `chl_to_app` remain per-app: `{APP}.SVCQM` / `SVCQM.{APP}`.
- `svc_conn` on `QmConfig` (if retained) resolves to the shared `svc.conn`.

### 3. Provisioning — create `SVCQM` once, accumulate channels

- `ansible/site-distributed-shared.yml` runs the `mq-qmgr` role with
  `qmgr_name: SVCQM`. `crtmqm` is idempotent, so the first stack to be
  provisioned creates it and later stacks find it already present. The
  `mq-inter-qm` (their-side) role adds each app QM's `{APP}.SVCQM` /
  `SVCQM.{APP}` channel pair + XMITQ onto the one shared `SVCQM`, so
  provisioning multiple stacks accumulates channel pairs rather than
  colliding.
- Each `ansible/site-*.yml` app-side MQSC threads `qm_svc = SVCQM` (shared)
  into `QREMOTE(SVC.REQUEST) RQMNAME(SVCQM)`, `XMITQ(SVCQM)`, and its
  `{APP}.SVCQM` SDR / `SVCQM.{APP}` RCVR pair. Each app QM keeps its own
  local `XMITQ(SVCQM)`.
- The #351 QM extra-var threading (`src/mqlab/phases.py`,
  `src/mqlab/cli.py`) now resolves `qm_svc` to `SVCQM` for all stacks.

### 4. Exporter (`src/mqlab/scrape.py`) — one svc instance total

- `mq_exporter_instances`: keep the per-stack **app** instances; emit the
  **svc** instance **once**, outside the per-stack loop —
  `qm="SVCQM"`, `conn` from the `svc:` block, `port=svc.exporter_port`,
  labels `stack="commons"`, `role="svc"`.
- Delete the `SVC_EXPORTER_CONN` module constant; read the conn from
  topology.
- Because the Prometheus `ibmmq` series are qmgr-keyed, every per-stack
  board's svc tile reads the single `SVCQM` series — one exporter feeds all
  boards.

### 5. Cockpit / boards

- `src/mqlab/messagingboard.py` — `stack.qm.qm_svc` now returns `SVCQM` for
  every stack; each board's svc tile shows the shared counterparty. The value
  changes; no structural change.
- `src/mqlab/dashboard.py` (~L126-128) hardcodes `PCMKSVC` and its channel
  names — update to `SVCQM` / `{APP}.SVCQM` / `SVCQM.{APP}`, or explicitly
  retire as a legacy clusterboard touch-point.

### 6. Certs / inventory (`src/mqlab/cli.py`)

- The svc cert-CN dedup (`seen_svc`, ~L551-579) now genuinely collapses to
  **one** svc CN (`SVCQM`) across all stacks — the behavior the code already
  anticipates. One svc keystore/cert is generated instead of four; the
  `mq-inter-qm` role wires that one shared `SVCQM` server keystore.

### 7. Tests

- Add the regression test #446 asks for: `mq_exporter_instances` yields
  **exactly one** svc instance, with `qm == "SVCQM"` and `conn` equal to the
  `svc:` block conn (name and address consistent) — so a per-stack svc target
  cannot silently return.
- Update existing tests that assert per-stack `{short}SVC` names / per-stack
  svc exporter instances / per-stack `exporter_svc_port`.

### 8. Validation & acceptance

- `vrg-container-run -- vrg-validate` — the only validation command.
- Per the repo's cold-rebuild acceptance gate (provisioning-touching change,
  so lint-green is not "done"): full VM cold rebuild with **≥2 stacks up**,
  proving in one pass:
  - `svc-sim` hosts exactly one QM, `SVCQM`, owning `:1414`;
  - each running stack's app messaging round-trips through `SVCQM`
    (`SVC.REQUEST` → reply on `APP.REPLY`);
  - the single svc exporter reads live (no `MQRC 2058`);
  - every per-stack messaging board's svc tile reads live rather than down.

## Scope

One cohesive change spanning topology, the naming source (`stacks.py`),
provisioning (`site-distributed-shared.yml` + each `site-*.yml`), the exporter
(`scrape.py`), the boards, the cert/inventory path (`cli.py`), and tests.
Cross-cutting but single-purpose (it resolves one bug's architecture fork), so
it is a **single task/spec**, not an epic. Because it touches provisioning it
is accepted only after a cold rebuild proves it; implementation is on the
live-lab side per the #446 handoff note.

## Non-goals

- No change to the app-side HA/DR mechanisms or their QMs (`{short}APP`).
- No change to how the app client reaches its app QM (`_app_qm_conn` stays
  per-stack and topology-derived — it was already correct).
- No new svc hosts or multi-port svc topology (that is the rejected
  per-stack alternative).
