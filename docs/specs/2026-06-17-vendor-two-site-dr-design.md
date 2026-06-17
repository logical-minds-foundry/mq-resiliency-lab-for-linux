# Two-site vendor (DTCC) DR model — design

- **Issue:** #237
- **Date:** 2026-06-17
- **Status:** Proposed
- **Scope:** Re-model the service/counterparty (DTCC) side as a two-site DR pair
  with 2×2 connectivity to our DR pair, so that surviving a *vendor* DR failover
  and surviving *our own* DR failover are separately exercisable. Vendor HA is
  transparent and out of scope.

## 1. Problem

The service side is modeled today as a **single standalone `QMDTCC`** on one VM
(`dtcc-sim`, `10.60.0.50`), explicitly "no HA/DR". Our side is a 2-site DR pair
(`QMPCMK` / `QMRDQM`). The asymmetry means we cannot validate the most important
operational property: that **our infrastructure survives the vendor performing a
DR failover** — a distinct event from surviving **our own** DR failover.

These are genuinely two different tests. In a real outage we do not get to decide
to use a counterparty's DR site; the counterparty declares DR after they exhaust
primary recovery, and only then do we follow. We need the lab to represent both
events and prove we survive each with no message loss on a clean failover.

## 2. Goals and non-goals

**Goals**

- Model the vendor as a **two-site DR pair**, same QM name, cold standby.
- Establish **2×2 reachability**: our {A,B} ↔ their {A,B}, so any combination of
  live sites works and each DR axis can fail over independently.
- Make our recovery to a vendor DR **automatic and safe** without operator action
  in the happy path, while preserving DR governance (no premature drift to DR).
- Provide a **vendor-DR simulation control** to trigger the event in the lab.
- Validate the full 2×2 with **zero message loss** on clean failovers.

**Non-goals**

- Vendor **HA** — transparent to us; never modeled or instrumented.
- A bespoke operator-side failover tool in the happy path (see §6, superseded).
- Message-integrity fault injection and reconciliation (deferred — §9).

## 3. Assumptions

- Counterparties provide a small set of **static** endpoints (IPs, possibly fixed
  hostnames) for primary and DR, **known up front** and built into the channel
  configuration. This is believed typical of our external third parties; we adapt
  if a concrete counterparty differs. This assumption is what makes the static
  CONNAME-list mechanism (§5) viable.
- "Reachable", from our vantage as a remote connector, means **a TCP listener
  answering on the channel port**. A QM running with its listener stopped is, to
  us, indistinguishable from down — and that is the intended signal.

## 4. Topology & the single-active invariant

Promote the vendor from one node to a two-site DR pair:

```
                      net-ext (inter-business WAN, 10.60.0.0/24)
   OUR SIDE                                           VENDOR SIDE
   QMPCMK / QMRDQM                                    QMDTCC  (one name, two sites)
   ┌──────────────┐  active VIP                       ┌──────────────────────────┐
   │ site A (live) │ 10.60.0.10 ───────┐        ┌──── │ dtcc-sim-a  10.60.0.50    │ LISTENER UP  (primary)
   ├──────────────┤                    │  2×2   │     ├──────────────────────────┤
   │ site B (std)  │ 10.60.0.20 ───────┘  reach └──── │ dtcc-sim-b  10.60.0.51    │ listener DOWN (cold std)
   └──────────────┘                                   └──────────────────────────┘
```

- `dtcc-sim` → **`dtcc-sim-a` (10.60.0.50, existing) + new `dtcc-sim-b`
  (10.60.0.51)**, both on `net-ext`, identical `QMDTCC` config on both.
- **Cold standby:** `dtcc-sim-b`'s machine is up and stable, but its **listener is
  stopped** until a vendor DR is enacted, so it reads as unavailable until then.
- **Single-active invariant — the core safety property:** at most one of A/B is
  reachable at any instant. This is "HA scaled up": the at-most-one-node guarantee
  Pacemaker gives within a cluster, lifted to the site level. The lab *models* it;
  the sim *enforces* it (stop A's listener before starting B's).
- **Arm-agnostic:** the vendor side is independent of our arm, so this applies to
  both `distributed-pcmk-ubuntu` and `distributed-rdqm-rhel`.

## 5. Connectivity, addressing, and the mechanism

### 5.1 Symmetric, static CONNAME lists

Both directions use a static comma-separated CONNAME list of the peer's
`(primary, secondary)` endpoints, configured once at provisioning:

| Direction | Channel | CONNAME | Failover style |
|---|---|---|---|
| them → us (reply) | `QMDTCC.QMPCMK` (SDR on vendor) | our VIP list `10.60.0.10,10.60.0.20` | automatic; their retry follows our VIP move |
| us → them (request) | `QMPCMK.QMDTCC` (SDR on ours) | vendor list `10.60.0.50,10.60.0.51` | automatic; our retry walks to the live vendor site |

The reply path already uses this pattern today (a CONNAME list of our two VIPs),
so the request path simply becomes symmetric. Because the vendor uses one QM name
across both sites with only one live, the vendor's routing back to us is
necessarily **one xmitq → one SDR channel** — a single static config replicated to
both sites.

### 5.2 Why automatic failover is safe here (cold standby)

The naive worry about a CONNAME list is premature drift: failing over to DR during
a transient primary blip before the counterparty has declared DR. **Cold standby
eliminates this**, because the DR listener is dark until the vendor deliberately
activates it:

| Event | primary | secondary | SDR behavior | Safe? |
|---|---|---|---|---|
| Normal | up | down | stays on primary | ✓ |
| Transient primary blip (no DR declared) | down | **down** | retries, reaches neither, buffers on xmitq, reconnects to primary on return | ✓ no premature DR |
| Vendor declares DR (activates secondary) | down | **up** | retry walks primary→secondary, connects to secondary | ✓ auto-recovers |
| Vendor failback (restores primary, drops secondary) | up | down | stays on secondary until it drops, then retry → primary | ✓ auto (once they drop secondary) |

The **deliberate decision still exists** — it lives on the side that owns it (the
vendor's act of activating their DR listener). Our follow is correctly automatic,
because we literally cannot connect to DR until they have declared it. This
reconciles "DR must not be automatic" with "no operator button on our side": the
non-automatic gate is the vendor's listener, not our tooling.

### 5.3 The 2×2 and why "both DR" composes

Exactly one of our sites and one of theirs is live at any instant (single-active on
both axes):

```
                  their A (live)        their B (live)
  our A (live)    ① normal              ③ vendor DR'd
  our B (live)    ② we DR'd             ④ both DR'd
```

- **Our axis (A↔B):** existing Pacemaker/RDQM VIP move via `mqlab dr` — transparent
  to the vendor (their reply list reconnects). Our request channel's CONNAME
  *definition* rides the single floating/replicated QM, so the static vendor list
  persists across our own failover.
- **Their axis (A↔B):** our request channel's CONNAME-list retry walks to the live
  vendor site automatically once the vendor activates it.
- Because both axes are independent and persist, **④ "both DR" composes** with no
  special case.

## 6. Alternatives considered (recorded, not built)

- **Guarded "big-red-button" CONNAME-rewrite tool.** A deliberate operator command
  that pre-flight-checks the invariant (primary unreachable, DR reachable) then
  rewrites the sender CONNAME from primary→DR and cycles the channel. This was the
  initial front-runner. It is **superseded** once we established that endpoints are
  static and known up front (§3): the CONNAME list (§5) achieves the same outcome
  automatically and safely under cold standby, with no operator action and no new
  tool. Retained only as a possible **future manual override** for a gray-failure
  edge case — a primary that answers TCP but does not process — where auto-retry
  will not move because the listener still answers. Not built now.
- **DNS / hostname indirection** (vendor re-points a hostname A→B). **Rejected:**
  DNS and MQ address caching make the switch non-deterministic — a caching
  nightmare. Even where a counterparty offers it, the position is to bypass it for
  the deterministic static-endpoint mechanism. Fixed hostnames that each map 1:1 to
  a stable address are fine and are *not* this option.

## 7. Vendor-DR simulation control

Because the lab owns the modeled vendor QM, the sim drives it via `pymqrest` (the
administrative REST wrapper):

- **`mqlab vendor failover --to {a|b}`** — enact the cold-standby flip:
  `stop_listener_sync` on the current site → confirm down → `start_listener_sync`
  on the target → confirm up. **Stop-before-start enforces at-most-one.**
  Idempotent and state-aware; bidirectional (failover and failback are the same
  verb with `--to` reversed).
- **`mqlab vendor status`** — read-only: which vendor listener is up
  (`display_lsstatus` on both) and which endpoint our sender is on
  (`display_chstatus`); asserts the single-active invariant. Reused by the tests.

The `vendor` namespace is deliberately distinct from `mqlab dr` (our-side
DRBD/VIP failover); help text states plainly that `vendor` commands *simulate* a
counterparty's DR. Final verb spelling confirmed at spec review.

REST is the preferred remote-management mechanism, and the non-trivial logic lives
in strictly integration-tested Python over `pymqrest`, which already wraps the full
MQSC admin surface (`alter_channel`, `stop/start_channel_sync`, `display_chstatus`,
`ping_channel`, `display_qstatus`, `start/stop_listener_sync`, …).

## 8. Validation, testing, acceptance

### 8.1 2×2 matrix

Each transition runs live `app_requester` traffic across it and asserts **zero
message loss** (every request gets its reply), the single-active invariant held
throughout, and the xmitq drained:

| Transition | Trigger | Expected |
|---|---|---|
| ① baseline (ourA/theirA) | — | e2e round-trips |
| ① → ② our DR | `mqlab dr failover` | their reply list auto-follows our VIP; zero loss |
| ① → ③ vendor DR | `mqlab vendor failover --to b` | our CONNAME-list retries onto theirB; zero loss |
| → ④ both DR | both triggers | composes; zero loss |
| failback (both axes) | reverse triggers | returns cleanly |

### 8.2 Tests

- **Unit** — the sim/status logic on a fake `pymqrest`: stop-before-start ordering,
  idempotency, fail-loud, invariant assertion.
- **Integration** — the 2×2 matrix on the cold-rebuilt lab; the real proof.
  Includes the **load-bearing verification** that an SDR honors the comma-separated
  CONNAME list on retry as relied upon (prove empirically *and* cite canonical IBM
  9.4 docs — lower risk since the reply path already uses this pattern).
- Everything within the existing `vrg-validate` gate (mind the known gotchas:
  StrEnum/UP042, ruff magic-comma, 100% branch coverage, `uv run pytest`, no masked
  exit codes).

### 8.3 Provisioning change

`topology.yaml` gains `dtcc-sim-b`; the inter-QM provisioning sets our sender
CONNAME to the static `(vendorPrimary, vendorDR)` list and provisions `QMDTCC`
identically on both sites. All static, applied at provision time.

### 8.4 Acceptance gate

Lab bring-up change → lint-green ≠ done. Accepted only after a **full VM cold
rebuild** brings up the two-site vendor topology and proves auto-failover one-pass,
with the 2×2 matrix passing at zero loss and the single-active invariant holding.
Validate first on the built `distributed-pcmk-ubuntu`, then `distributed-rdqm-rhel`.

## 9. Deferred and anticipated (seam only — not built)

### 9.1 Message-integrity fault injection + reconciliation

Our own DR carries a known **RPO > 0** risk: replication lag means a small,
minimized, but non-zero chance of losing an in-flight message on failover. A vendor
DR carries the same class of risk from our vantage, with less visibility, and can
produce two message-integrity faults worth simulating:

- **Duplicate reply** (their DR replays a reply we already received): **benign**.
  The first reply already closed the correlation / settled the transaction; the
  second is duplicate noise — detect, log as an exception to investigate, proceed.
  No correctness impact; handled by app-layer idempotency.
- **Missing reply** (we sent a request, no reply ever arrives): **the dangerous
  one**. We cannot tell unilaterally whether they processed it and lost the reply,
  or never processed it — irreconcilable without vendor coordination. This is an
  app-dev + ops + vendor **reconciliation process** (one a counterparty such as
  DTCC very likely already has).

The lab will provide the *trigger* (a clean vendor DR) in the first cut. Injecting
these faults and validating detection/reconciliation requires app-dev coordination
and app-side logic the lab does not yet have, so it is a **defined, scoped
fast-follow**. The sim must not hard-code anything that blocks adding fault
injection later.

### 9.2 Vendor-profile seam

Real counterparties differ. Build **Profile 1** (same QM name, cold standby,
auto-follow-our-DR via symmetric CONNAME lists) now; leave a seam — in the spirit
of the repo's arm-backend registry — to add odd real-world vendor configs later
(e.g. a vendor with a transparent floating IP that would make any of our action a
no-op, a vendor that does not auto-follow our DR and forces coordination, distinct
per-site names, a gateway/concentrator QM). Do not hard-code assumptions that would
block additional profiles.

## 10. Verification items (flagged, not assumed)

- An MQ **SDR channel honors a comma-separated CONNAME list and walks it on retry**
  as §5 relies upon — confirmed against canonical IBM 9.4 docs and empirically.
- `stop_listener_sync` / `start_listener_sync` via `pymqrest` enact the
  cold-standby flip cleanly and observably.

## 11. Risks

- **CONNAME-list retry semantics differ from the model** → the whole mechanism
  rests on it. Mitigated by §10 verification and by the reply path already using
  the pattern.
- **Premature DR drift** → mitigated structurally by cold standby (§5.2); the DR
  listener is dark until the vendor declares.
- **Gray failure** (primary answers TCP but does not process) → out of scope for
  the first cut; the deferred manual override (§6) is the future answer.

## 12. Coordination

Touches `lab/topology.yaml`, the inter-QM Ansible role/templates, and adds an
`mqlab vendor` command + Python module. Several DR-adjacent branches are in flight
(`feature/233-plan-c-rdqm-dr`, RDQM parity work). The implementation plan should
land aware of their footprints to avoid messy merges, keeping topology and CLI
edits narrowly scoped.
