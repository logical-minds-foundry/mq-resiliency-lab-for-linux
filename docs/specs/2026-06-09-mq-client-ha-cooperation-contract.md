# MQ Client HA/DR Cooperation Contract

> **Status:** Derived and validated on the Ubuntu Pacemaker/SAN arm (#64).
> Every requirement below was forced by a live failure in the HA fault
> drills — none is assumed. This document is the reference the future
> multi-language/multi-API client matrix must reproduce: each client, in
> each language, has to satisfy every row to ride an MQ HA/DR event without
> loss.

## Why this exists

IBM MQ can be made extremely robust, but that robustness is **conditional on
the client cooperating with the infrastructure**. A queue manager can fail
over flawlessly and a misconfigured application can still hang forever, lose a
message, or silently duplicate one. Historically this is where HA "works in
the demo, fails in production" — apps that don't set the right options, or set
timeouts too long, lock up during a failover and never reconnect.

The HA drills in [the evidence report](../reports/2026-06-09-ha-drills-under-load-findings.md)
exercised a continuous persistent+syncpoint flow through four distinct site-A
faults. Each client-side defect surfaced as a real failure (a hang, a crash, or
a loss) before it was fixed — so the contract is empirical, not theoretical.

## Contents

- [The contract](#the-contract)
- [The two detection paths a client must survive](#the-two-detection-paths-a-client-must-survive)
- [Requirement detail](#requirement-detail)
- [Infrastructure-side companions](#infrastructure-side-companions)
- [Cross-site DR additions](#cross-site-dr-additions)
- [Reference implementation](#reference-implementation)
- [Sources](#sources)

## The contract

| # | Requirement | Forced by (live failure) |
|---|---|---|
| 1 | Connect with `MQCNO_RECONNECT_Q_MGR` (reconnect to the same QM via the VIP) | baseline — without it the connection dies at the fault |
| 2 | SVRCONN channel `SHARECNV >= 1` | mandatory for reconnect; `SHARECNV(0)` → `MQRC_ENVIRONMENT_ERROR` |
| 3 | Set `FAIL_IF_QUIESCING` on every MQI call (`MQGMO`/`MQPMO`) | a blocking `MQGET`-with-`WAIT` hangs through a controlled `endmqm` quiesce without it — the classic lock-up |
| 4 | Handle the quiescing family `2161` **and** `2202` as transient | client crashed on `MQRC_CONNECTION_QUIESCING` (2202) until it was handled alongside `MQRC_Q_MGR_QUIESCING` (2161) |
| 5 | Treat `2549 MQRC_CALL_INTERRUPTED` as in-doubt: retry the **same business key**, never blind-backout | the commit may already have landed; blind retry-with-new-key risks silent loss. Re-key keeps a double-landing detectable as a duplicate |
| 6 | Yield (sleep) on every retry; never busy-`continue` | a bare retry loop held the GIL and starved the client library's reconnect thread — the producer hung at 90 % CPU and never reconnected |
| 7 | Short channel `HBINT`/`KAINT` + client TCP `KeepAlive` | a blocked `MQPUT` to a *fenced* node stalled ~5 min (default `HBINT(300)`) before the dead peer was detected and reconnect engaged |
| 8 | Know the reconnect timeout (`MQReconnectTimeout`, default 1800 s) | bounds total reconnect attempts; on expiry → `MQRC_RECONNECT_FAILED` |
| 9 | Implement an explicit application-level **reconnect loop** | a controlled `endmqm -w` (no `-r`) disconnects clients **non-reconnectably** — `MQCNO_RECONNECT` covers only *abrupt* breaks, so the app must rebuild its own connection |

**#9 is the headline.** Automatic client reconnection is necessary but not
sufficient. It transparently rides an *abrupt* break (crash, kill, fence —
HA-1, HA-2, and HA-3-with-`suicide` all proved this). But a *controlled*
`endmqm -w` ends client connections non-reconnectably: the call returns
`2009 MQRC_CONNECTION_BROKEN` immediately (not after a reconnect attempt), and
auto-reconnect never engages. A robust client must detect that and rebuild the
connection itself — which then survives *every* stop mode uniformly.

## The two detection paths a client must survive

The same continuous flow must ride two structurally different failover
triggers, because the infrastructure can decide to fail over for either reason:

1. **Quorum-driven** (HA-3): a node loses quorum and Pacemaker stops the QM
   with a controlled `endmqm -w` → the client sees the quiescing family
   (2161/2202) and/or `2009`, and must rebuild (requirement #9).
2. **Storage-driven** (HA-4): the SAN under the owner dies, the hardened
   `mq_fs` monitor times out and the node is fenced → an abrupt break the
   client rides via auto-reconnect, *provided* keepalive (requirement #7)
   detects the fenced peer quickly enough to engage it.

A client that handles only one path passes one drill and fails the other.

## Requirement detail

**Reason-code taxonomy (the core of the contract).** The reference client
splits the codes a reconnectable client still sees into two families that
demand different handling — encoded in `clients/dr_mqi.py`:

- *Retry in place* — the connection is still usable:
  - `2003 MQRC_BACKED_OUT` — the QM cleanly rolled back the uncommitted UOW.
  - `2161 MQRC_Q_MGR_QUIESCING` — a controlled `endmqm` is quiescing the QM.
  - `2549 MQRC_CALL_INTERRUPTED` — the connection broke *during* the commit;
    outcome unknown. Retry the **same** seq/uuid (never blind-backout) so a
    double-landing is a detectable duplicate, never a silent loss. This is the
    at-least-once-plus-idempotent-consumer pattern; the DR framework's
    `duplicated`/`ambiguous` buckets quantify any residue.
- *Rebuild the connection* — it is gone and auto-reconnect did not restore it
  (controlled `endmqm`):
  - `2009 MQRC_CONNECTION_BROKEN`, `2202 MQRC_CONNECTION_QUIESCING`,
    `2059 MQRC_Q_MGR_NOT_AVAILABLE`, `2018 MQRC_HCONN_ERROR`.
  - `MQCONNX` is never auto-retried by the client library, so the rebuild loop
    must re-issue it with backoff until the QM is reachable on the survivor.

**No-loss across a rebuild.** The producer carries the in-flight message's
seq/uuid across a connection rebuild, so a failover at the moment of a put
neither drops the message nor silently re-keys it. The consumer records
`CONFIRMED` only after a clean commit; on an in-doubt commit it records
nothing, so the reconciler honestly buckets the message as ambiguous rather
than claiming a confirmation it cannot be sure of.

## Infrastructure-side companions

Two requirements are satisfiable on the *infrastructure* side and are recorded
here because a client-requirements decision depends on them:

- **Controlled-failover reconnect.** Requirement #9's controlled-`endmqm`
  problem can alternatively be addressed by stopping the QM with `endmqm -r`
  (which tells reconnectable clients to reconnect). In this lab that conflicts
  with Pacemaker's `systemd` resource agent, which needs a clean blocking stop
  (`endmqm -w`) it can relocate on; `-r` confused the relocation. The lab
  therefore relies on the **client** reconnect loop plus fence-on-quorum-loss
  (`no-quorum-policy=suicide` makes the isolated node self-fence abruptly, so
  even the controlled path becomes an abrupt break the client rides). A real
  deployment chooses: robust clients, `endmqm -r` on planned moves, or both.
- **Channel `HBINT(15)`/`KAINT(15)`** on the SVRCONN, plus client
  `mqclient.ini` `TCP:KeepAlive=Yes` and a short OS keepalive, so a blocked
  call detects a fenced peer in ~30 s instead of stalling ~`HBINT` (default
  300 s). Codified in `lab/scripts/pcmk-qm-create.sh` and `ansible/site.yml`.

## Cross-site DR additions

The contract above is HA-shaped — one queue manager, one VIP, a failover that
stays inside a single subnet. Forced cross-site DR (#67) surfaced two more
requirements that single-site auto-reconnect does **not** cover. A client that
passes every HA row above can still silently fail a DR cutover without these.

**DR-1. The connection name must list BOTH sites' VIPs (or use a CCDT that
does).** `MQCNO_RECONNECT_Q_MGR` reconnects to the *same* queue manager — but on
a cross-site cutover that QM comes back at a *different* VIP on a *different*
subnet (site A `10.10.1.200` → site B `10.10.2.200`). Auto-reconnect to a single
VIP cannot follow it. The reference client connects with a comma-separated list,
`"10.10.1.200(1414),10.10.2.200(1414)"`, so it tries A and on A's loss fails over
to B — exactly how the "flow continues through cutover" result was achieved (the
app kept producing on the survivor). Production equivalents: a CCDT listing both
sites, or connection-list configuration.

*Discovered by:* the first DR drill, where the client could only reach site A —
after the kill it had nowhere to reconnect, so it hung (see DR-2) and the flow
could not continue.

**DR-2. Bound the connect attempt — `MQCONNX` blocks on a dead site's VIP.**
When the primary site is gone, a (re)connect to its VIP is a TCP connect to a
host that no longer answers, which blocks for the OS connect timeout (minutes),
**ignoring the application's own deadline** (that deadline bounds the reconnect
*retry loop*, not a single in-progress `MQCONNX`). A DR-aware client needs either
a short connect timeout or — better — the multi-address list from DR-1, so the
stack moves on to the reachable site instead of wedging on the dead one.

*Discovered by:* the empty-ledger drill — a single-VIP client hung in `MQCONNX`
past its deadline and never wrote its ledger. Two fixes followed: the multi-VIP
list (DR-1), and a **crash-safe periodic ledger flush** so evidence survives a
hang regardless.

**Infrastructure companion.** The DR site's cluster must already *know* the QM
before cutover: the QM definition (`addmqinf`) + its systemd unit pre-seeded on
the peer nodes, with matching iSCSI initiator IQNs and the target package
present. The data travels via DRBD; the *identity* must be pre-staged. (RDQM
bundles this; the hand-built arm makes it explicit — see the #56 operability
notes.)

## Reference implementation

- `clients/dr_mqi.py` — the shared contract: reason-code families and the
  `connect`/`connect_retry` helpers.
- `clients/dr_flow.py` — the app-side continuous flow (producer + consumer,
  separate connections, each with its own rebuild loop).
- `clients/dr_responder.py` — the SVC god's-eye responder (single connection,
  outer rebuild loop).

These are the oracle for the multi-language matrix: a candidate client in any
language is correct iff, driven through the same drills, its ledger reconciles
to RPO 0 (or the framework attributes any non-zero result to a specific bucket).

## Sources

Data (what IBM documents) is separated from judgment (reasoning derived in the
lab) inline above. Primary sources:

- [Automatic client reconnection — IBM Documentation (MQ 9.3)](https://www.ibm.com/docs/en/ibm-mq/9.3.x?topic=restart-automatic-client-reconnection)
- [Channel and client reconnection — IBM Documentation (MQ 9.3)](https://www.ibm.com/docs/en/ibm-mq/9.3.x?topic=managers-channel-client-reconnection)
- [IBM MQ MQI client configuration file, mqclient.ini (MQ 9.3)](https://www.ibm.com/docs/en/ibm-mq/9.3.x?topic=multiplatforms-mq-mqi-client-configuration-file-mqclientini)
- Paul Clarke, *MQ Clients*, MQ Technical Conference 2017.
