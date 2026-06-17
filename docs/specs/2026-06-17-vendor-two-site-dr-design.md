# Two-site vendor (DTCC) DR model — design

- **Issue:** #237
- **Date:** 2026-06-17
- **Status:** Proposed (revised after pushback, 2026-06-17)
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
- Provide a **deliberate, operator-run failover utility** that encapsulates the
  multi-step channel reconciliation a vendor DR requires (it is *not* automatic —
  see §5), making the switch a single bulletproof action.
- Provide a **vendor-DR simulation control** to create the event in the lab.
- Validate the full 2×2 with **zero message loss** on clean failovers.

**Non-goals**

- Vendor **HA** — transparent to us; never modeled or instrumented.
- Automatic, hands-off failover for the request path (impossible here — §5.2).
- Message-integrity fault injection and reconciliation (deferred — §9).

## 3. Assumptions

- Counterparties provide a small set of **static** endpoints (IPs, possibly fixed
  hostnames) for primary and DR, **known up front** and built into the channel
  configuration. Believed typical of our external third parties; we adapt if a
  concrete counterparty differs.
- "Reachable", from our vantage as a remote connector, means **a TCP listener
  answering on the channel port**. A QM running with its listener stopped is, to
  us, indistinguishable from down — and that is the intended signal.
- **A vendor DR failover requires us to take deliberate action.** Because the
  vendor's DR site is a *separate QM instance* (not our replicated state), its
  channel sequence numbers must be assumed **stale**, so a `RESET CHANNEL` is
  **always** required on our request channel (§5.2). The vendor activating their
  DR listener is *necessary but not sufficient*; we must run the failover utility.

## 4. Topology & the single-active invariant

Promote the vendor from one node to a two-site DR pair, on a **dedicated vendor
address block** `10.60.0.40–.49` (clear of our nodes; room for future profiles):

```
                      net-ext (inter-business WAN, 10.60.0.0/24)
   OUR SIDE                                           VENDOR SIDE
   QMPCMK / QMRDQM                                    QMDTCC  (one name, two sites)
   ┌──────────────┐  active VIP                       ┌──────────────────────────┐
   │ site A (live) │ 10.60.0.10 ───────┐        ┌──── │ dtcc-sim-a  10.60.0.40    │ LISTENER UP  (primary)
   ├──────────────┤                    │  2×2   │     ├──────────────────────────┤
   │ site B (std)  │ 10.60.0.20 ───────┘  reach └──── │ dtcc-sim-b  10.60.0.41    │ listener DOWN (cold std)
   └──────────────┘                                   └──────────────────────────┘
```

- `dtcc-sim` → **`dtcc-sim-a` (renumbered `10.60.0.50` → `10.60.0.40`) +
  `dtcc-sim-b` (`10.60.0.41`)**, both on `net-ext`, identical `QMDTCC` on both.
- The renumber requires a **reference sweep**: `dtcc_conn` in both distributed
  setups, the their-side channel CONNAME, the `topology.yaml` comments (#147/#182),
  and the lab-bootstrap runbook.
- **Cold standby:** `dtcc-sim-b`'s machine is up and stable, but its **listener is
  stopped** until a vendor DR is enacted, so it reads as unavailable until then.
- **Single-active invariant — the core safety property:** at most one of A/B is
  reachable at any instant. This is "HA scaled up": the at-most-one-node guarantee
  Pacemaker gives within a cluster, lifted to the site level. The lab *models* it;
  the sim *enforces* it (stop A's listener before starting B's).
- **Arm-agnostic:** the vendor side is independent of our arm; applies to both
  the Pacemaker and RDQM combined setups (§8.3).

## 5. Connectivity, addressing, and the mechanism

### 5.1 Addressing

| Direction | Channel | CONNAME | Failover style |
|---|---|---|---|
| them → us (reply) | `QMDTCC.QMPCMK` (SDR on vendor) | static list of our VIPs `10.60.0.10,10.60.0.20` | **transparent / automatic** — our DR replicates channel state, so their retry just reconnects (§5.2) |
| us → them (request) | `QMPCMK.QMDTCC` (SDR on ours) | single endpoint, **managed by the failover utility** | **deliberate** — a vendor DR requires the utility (repoint + reset + restart) |

### 5.2 Why our DR is transparent to them, but their DR is a utility for us

The asymmetry is rooted in **channel sequence numbers**, which MQ persists per
channel at both ends and resynchronizes on channel start (a mismatch yields
`AMQ9526` and the channel refuses to start).

- **Our DR (A↔B) is state-preserving.** `QMPCMK` is a single logical QM whose data
  — including the channel sync state (`SYSTEM.CHANNEL.SYNCQ`) — is replicated
  (DRBD / RDQM). So when we fail over, the DR instance carries matching sequence
  numbers; the vendor's reply channel reconnects to our moved VIP with no reset.
  Transparent and automatic for them. (The accepted RPO>0 risk is the one edge
  where even this could drift — see §9.1.)
- **The vendor's DR (A↔B) is *not* state-preserving from our vantage.** Their DR
  site is a separate QM instance with its own sync state, which we must assume is
  **stale**. So our request channel's handshake to the DR site will hit a sequence
  mismatch, and `RESET CHANNEL` is **always required**. Auto-reconnect cannot clear
  this — hence a utility, not a CONNAME-list that "just reconnects".

This is the channel-level reason the operational narrative is correct: the vendor
makes the deliberate call (activating their DR listener); *we* then make a
deliberate call (running the failover utility, which resets and reconnects).

### 5.3 The failover utility (the deliverable)

A guarded, deterministic, integration-tested Python utility over `pymqrest`,
encapsulating every manual step so the operator runs **one** action:

```
PRE-FLIGHT (guard — refuse unless satisfied or --force)
  c0  read current request CONNAME + DISPLAY CHSTATUS  → current target, state
  c1  if already on target and RUNNING                 → no-op, exit 0
  c2  TCP-probe CURRENT vendor endpoint → MUST be unreachable  (primary down)
  c3  TCP-probe TARGET  vendor endpoint → MUST be reachable    (vendor DR up)
      ── if c2/c3 unmet and not --force → FAIL LOUD, change nothing

SWITCH (deterministic, ordered — _sync variants)
  s1  stop_channel_sync(QMPCMK.QMDTCC, QUIESCE)   # in-flight finishes; persistent
                                                  # messages remain safe on the xmitq
  s2  ensure_channel(QMPCMK.QMDTCC, CONNAME=<target endpoint>)   # repoint
  s3  reset_channel(QMPCMK.QMDTCC)                # MANDATORY — stale vendor seqno (§5.2)
  s4  resolve_channel(...) if in-doubt            # surface/resolve in-doubt batches
  s5  start_channel_sync(QMPCMK.QMDTCC)

VERIFY (fail-loud — never claim success unverified)
  v1  DISPLAY CHSTATUS → RUNNING (not RETRYING / sequence error)
  v2  ping_channel     → round-trip to the DR QM confirmed
  v3  DISPLAY QSTATUS(xmitq) → depth draining toward 0
      ── any failure → report actual state, exit non-zero
```

- **Deliberate, not automatic:** the utility *automates the steps*; the *decision*
  to run it is human (fired when the vendor declares DR).
- **Idempotent & symmetric:** re-runs no-op; **failback** is the same utility with
  the target reversed (when the vendor's primary recovers and they clear us).
- **`--force`** is the explicit, audited override for drills / partial states.
- **Survives our own DR:** the channel definition rides our replicated/floating QM,
  so the chosen vendor target persists when *we* fail over (composes with ④, §5.4).

### 5.4 The 2×2

Exactly one of our sites and one of theirs is live at any instant:

```
                  their A (live)        their B (live)
  our A (live)    ① normal              ③ vendor DR'd
  our B (live)    ② we DR'd             ④ both DR'd
```

- **Our axis (A↔B):** Pacemaker/RDQM VIP move via `mqlab dr` — transparent to the
  vendor (§5.2).
- **Their axis (A↔B):** requires running the failover utility (§5.3) — repoint +
  reset + restart.
- **④ "both DR" composes:** the utility-set vendor target persists across our DR.

## 6. Alternatives considered (recorded, not built)

- **Pure automatic CONNAME-list failover** (sender CONNAME = `(primary, DR)`, rely
  on retry). **Rejected:** it cannot clear the mandatory `RESET CHANNEL` (§5.2); on
  failover the channel would stall on a sequence error (`AMQ9526`) until a reset.
  It also conflates "reachable" with "declared", which cold standby only partially
  mitigates. (This approach was explored at length during brainstorming; the
  sequence-number reality is what ruled it out.)
- **DNS / hostname indirection** (vendor re-points a hostname A→B). **Rejected:**
  DNS and MQ address caching make the switch non-deterministic. Fixed hostnames
  that map 1:1 to a stable address are fine and are *not* this option.

## 7. Vendor-DR simulation control

Two distinct tools, both driving `pymqrest`:

- **Operational — the failover utility (§5.3):** `mqlab vendor failover --to {a|b}`
  acts on **our** request channel (stop → repoint → reset → restart → verify). This
  is the deliverable an operator runs when the vendor declares DR.
- **Lab-only — the vendor-side sim:** `mqlab vendor sim-dr --to {a|b}` enacts the
  modeled vendor's listener flip (`stop_listener_sync` on the current site → confirm
  down → `start_listener_sync` on the target → confirm up). **Stop-before-start
  enforces at-most-one.** Used to create the test scenario; it touches the vendor
  QMs, which the lab owns.
- **Read-only:** `mqlab vendor status` — which vendor listener is up
  (`display_lsstatus` on both) and which endpoint our request channel is on
  (`display_chstatus`); asserts the single-active invariant. Reused by tests.

REST is the preferred remote-management mechanism, and the non-trivial logic lives
in strictly integration-tested Python over `pymqrest` (the administrative REST
wrapper, which exposes the full MQSC surface — `alter`/`ensure_channel`,
`reset_channel`, `resolve_channel`, `stop`/`start_channel_sync`, `display_chstatus`,
`ping_channel`, `display_qstatus`, `start`/`stop_listener_sync`). Final verb
spellings confirmed at spec review.

## 8. Validation, testing, acceptance

### 8.1 Combined setup (the validation target)

No existing setup can run the 2×2 (the distributed setups are *our*-site-A only;
the DR setups have no vendor/app). Add a **combined setup**:

- **`distributed-pcmk-dr`** — groups `[san_a, san_b, pcmk_a, pcmk_b, dtcc, app]`,
  provisioned by a playbook composing the existing DR (`site-pcmk-dr.yml`) and
  distributed (`site-distributed*.yml`) plays; `secrets` include
  `mqweb_admin_password`. The analogous **RDQM** combined setup follows for that arm.
- It provisions `QMDTCC` **identically on both vendor sites**, with site B's
  listener stopped (cold standby).
- **Bring-up weight:** ~10 VMs for the Pacemaker arm; the cold-rebuild gate boots
  all of them — precisely the long bring-up the #211 parallel-bootstrap work
  targets.

### 8.2 2×2 matrix

Each transition runs live `app_requester` traffic across it and asserts **zero
message loss**, the single-active invariant held, and the xmitq drained:

| Transition | Trigger | Expected |
|---|---|---|
| ① baseline (ourA/theirA) | — | e2e round-trips |
| ① → ② our DR | `mqlab dr failover` | vendor reply channel reconnects transparently (no reset); zero loss |
| ① → ③ vendor DR | `mqlab vendor sim-dr --to b` then `mqlab vendor failover --to b` | utility repoints+resets+restarts; zero loss after it completes |
| → ④ both DR | both axes | composes; zero loss |
| failback (both axes) | reverse triggers | returns cleanly |

### 8.3 Tests

- **Unit** — failover-utility logic on a fake `pymqrest`: step ordering
  (stop→repoint→reset→restart), the mandatory reset, in-doubt branch, pre-flight
  guard + `--force`, idempotency, fail-loud verify. Sim logic: stop-before-start.
- **Integration** — the 2×2 on the cold-rebuilt combined setup; the real proof.
  Includes the **load-bearing verification** of the cross-instance same-name
  sequence-reset behavior (the reset is required and works) — confirmed against
  canonical IBM 9.4 docs *and* empirically.
- All within the existing `vrg-validate` gate (StrEnum/UP042, ruff magic-comma,
  100% branch coverage, `uv run pytest`, no masked exit codes).

### 8.4 Acceptance gate

Lab bring-up change → lint-green ≠ done. Accepted only after a **full VM cold
rebuild** of the combined setup proves bring-up one-pass, with the 2×2 matrix
passing at zero loss and the single-active invariant holding. Pacemaker arm first,
then RDQM.

## 9. Deferred and anticipated (seam only — not built)

### 9.1 Message-integrity fault injection + reconciliation

Our own DR carries a known **RPO > 0** risk (replication lag → a small, minimized,
non-zero chance of in-flight loss on failover). A vendor DR carries the same class
of risk from our vantage, with less visibility, and can produce:

- **Duplicate reply** (their DR replays a reply we already received): **benign** —
  the first reply already closed the correlation / settled the transaction; the
  second is duplicate noise to detect, log, and investigate. App-layer idempotency.
- **Missing reply** (request sent, no reply): **the dangerous one** — irreconcilable
  unilaterally; needs an app-dev + ops + vendor **reconciliation process** (one a
  counterparty such as DTCC very likely already has).

First cut provides the *trigger* (a clean vendor DR). Fault injection and
detection/reconciliation need app-dev coordination and app-side logic the lab does
not yet have — a defined, scoped fast-follow. The sim must not hard-code anything
that blocks adding fault injection later.

### 9.2 Channel-exit automation of the reset (research item)

A custom **channel exit** could *someday* detect the sequence mismatch and automate
the reset, moving the request-path failover back toward automatic. Recorded as a
**future possibility to investigate** — feasibility unverified; not on the path now.

### 9.3 Vendor-profile seam

Real counterparties differ. Build **Profile 1** (same QM name, cold standby,
utility-driven failover with mandatory reset) now; leave a seam — in the spirit of
the repo's arm-backend registry — for odd real-world configs later (transparent
vendor floating IP that would make our action a no-op, a vendor that does not
auto-follow our DR, distinct per-site names, a gateway/concentrator QM). Do not
hard-code assumptions that block additional profiles.

## 10. Verification items (flagged, not assumed)

- **Load-bearing:** cross-instance, same-name channel **sequence-reset behavior** —
  that `RESET CHANNEL` is required on failover to the DR instance and reliably
  restores message flow — confirmed against IBM 9.4 docs and empirically.
- **Full manual-step enumeration** the utility must encapsulate: stop, repoint,
  reset, in-doubt resolution (`resolve_channel`), restart, verify — and any case
  that needs vendor-side coordination.
- `stop_listener_sync` / `start_listener_sync` via `pymqrest` enact the cold-standby
  flip cleanly and observably.
- SDR CONNAME addressing semantics (minor now — addressing only, not failover).

## 11. Risks

- **Reset semantics / vendor coordination.** Some sequence-reset cases may require
  vendor-side cooperation; the utility must detect and fail loud rather than mask.
  Retired/bounded by the §10 verification.
- **In-doubt batches** on a hard failover of transactional flows → handled by the
  `resolve_channel` step; verified in the spike.
- **Combined-setup bring-up weight** (~10 VMs, cold-rebuild gate) → ties to and
  benefits from the #211 parallel-bootstrap work.
- **Premature action** → bounded by the pre-flight guard (c2/c3) and cold standby
  (DR listener dark until the vendor declares).

## 12. Coordination

Touches `lab/topology.yaml` (renumber + `dtcc-sim-b` + combined setup), a new
combined-setup provisioning playbook, the inter-QM role/templates, and adds the
`mqlab vendor` commands + Python module. Several DR-adjacent branches are in flight
(`feature/233-plan-c-rdqm-dr`, RDQM parity work). The implementation plan should
land aware of their footprints, keeping topology and CLI edits narrowly scoped.
