# IBM MQ for C/C++: the C MQI vs. the stabilized C++ classes — status, trade-offs, and resiliency implications — report

- **Issue:** [#1044](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1044) (advisory / prior-art report, ad-hoc)
- **Date:** 2026-08-19
- **Scope:** IBM MQ 9.4.x (Long Term Support). All product claims pinned to IBM MQ 9.4 documentation; sources accessed 2026-08-19.
- **Status:** final — desk research against IBM MQ 9.4 documentation, plus one anonymized field case study.
- **Audience:** application architects and technical leads who own C/C++ applications that connect to IBM MQ.
- **Confidentiality:** contains no client-identifiable material. The §7 case study is anonymized — a generic *trading back-office gateway*.
- **Method:** every load-bearing claim is tagged **DATA** (what an IBM source states) or **JUDGMENT** (our reasoning on top). Sources are listed in §9 with full URLs and reference labels `[TAG]`.

---

## 1. Summary & recommendation

There are two ways to talk to IBM MQ from C or C++ on a distributed platform:

1. **The C MQI** (`cmqc.h`, verbs `MQCONNX` / `MQOPEN` / `MQGET` / `MQPUT` / `MQCB` …) — the native, **fully-maintained, first-class** procedural interface. Every MQ capability lands here first.
2. **The C++ classes** (`imqi.hpp`, the `Imq*` object model) — an object-oriented **wrapper over the MQI** that is **stabilized**: still shipped and supported at 9.4, but frozen at an old function level and not extended to cover capabilities added in and after IBM MQ 7.0.1. `[CPPLIST]` `[DEP]`

**The single most important correction to a common misconception:** the C++ classes are **not "deprecated."** They do not appear on the IBM MQ 9.4.0 deprecated-or-removed list `[DEP]`. "Deprecated," "stabilized," and "removed" are three different lifecycle states (§3). Treating "stabilized" as "deprecated" overstates the urgency; treating it as "just fine, fully current" understates a real capability gap. Both mistakes are common.

**The capability gap that matters for a resiliency programme (DATA, §4–§5):** the following MQ features have **no representation in the C++ classes** and are reachable only via the C MQI (or another maintained binding such as XMS):

- **Programmatic automatic client reconnection** (`MQCNO_RECONNECT` / `MQCNO_RECONNECT_Q_MGR`) and the **reconnection event handler**;
- **Asynchronous / callback-driven consumption** (`MQCB` / `MQCTL`);
- **Message properties and selectors** (message handles, `MQSETMP` / `MQINQMP`);
- **Integrated publish/subscribe** (`MQSUB`).

**Recommendation (JUDGMENT):**

- **If runtime resilience (transparent recovery from a broken connection or a queue-manager failover) is a requirement**, an application built on the C++ classes cannot get it *programmatically*. The only **supported** reconnect path for such an app is the **external** `mqclient.ini` `DefRecon` switch (§5.2) — and that is safe **only after** the application is made reconnection-safe (§5.3). For anything beyond a tactical stopgap, **re-base the MQ layer on the C MQI** (Option A, §6). The existing object-wrapper architecture is preserved; only its foundation changes.
- **If the application is a simple, restart-tolerant client** with no reconnect requirement, the stabilized C++ classes remain supported and there is **no forced migration** — but the residual risk (no runtime failover) should be documented and accepted explicitly, not by default.

The per-option effort/risk trade-off is in §6; the concrete failure modes are made real in the §7 case study.

---

## 2. Background: the two C/C++ interfaces

| | **C MQI** | **C++ classes** |
|---|---|---|
| Header | `cmqc.h` (+ `cmqxc.h`, `cmqstrc.h`, …) | `imqi.hpp` (single header) `[CPPLIST]` |
| Style | Procedural verbs over MQI structures (`MQMD`, `MQGMO`, `MQPMO`, `MQCNO`, `MQOD` …) | Object model; classes **encapsulate** those same MQI structures `[CPPLIST]` |
| Relationship | The interface | A wrapper **built on top of** the MQI `[CPPLIST]` |
| Thread-safety | App-managed | **Objects are not thread-safe**; IBM recommends a separate `ImqQueueManager` per thread `[CPPLIST]` |
| Maintenance | **First-class, fully maintained** | **Stabilized** (frozen function level) — see §3 |

**DATA:** IBM's own description — *"The IBM MQ C++ classes encapsulate the IBM MQ Message Queue Interface (MQI). There is a single C++ header file, `imqi.hpp`, which covers all of these classes."* `[CPPLIST]`

The practical consequence of "wrapper over the MQI": anything the wrapper does, the MQI underneath can also do — but not everything the MQI can do is surfaced by the wrapper. §4 is the list of what is not surfaced.

---

## 3. Lifecycle status: deprecated vs. stabilized vs. removed

IBM uses three distinct terms, and the distinction is the crux of this report:

| Term | Meaning | Applies to the C++ classes? |
|---|---|---|
| **Deprecated** | Discouraged; may be removed in a future release; migrate away. | **No.** `[DEP]` |
| **Stabilized** | Still shipped and **fully supported**, but **frozen** — no new function is added. | **Yes.** (see below) |
| **Removed** | Gone from the product. | **No.** `[DEP]` |

**DATA:** The IBM MQ 9.4.0 *"Deprecated, stabilized, and removed features"* page lists neither the C++ classes nor the C++ API among deprecated or removed features. The only C++-adjacent deprecation is *support for the XL C/C++ for AIX 16 compiler* — a **toolchain** item (IBM is moving to XLC 17), which in fact confirms that IBM continues to actively support **compiling** C++ MQ applications. `[DEP]`

**DATA → "stabilized" is evidenced, not assumed.** The frozen-function-level status is directly observable in the current 9.4 reference:

- The C++ **class inventory** contains no class for publish/subscribe (no subscription class), no class for asynchronous/callback consumption, and no message-property/message-handle class — every one of those is a post-7.0 MQI capability. `[CPPLIST]`
- `ImqQueueManager`'s documented **connect-option set** (`setConnectOptions`) enumerates only the pre-7.0.1 options (binding type, handle sharing, connection tag) and **omits the reconnection options**. `[IMQQM]` (verbatim list in Appendix A.2.)

**JUDGMENT / note on wording.** IBM's explicit "enhancements will not be applied to the C++ classes" statement historically appears in the *Using C++* documentation. We were unable to extract that exact sentence from a live, citable 9.4 URL in preparing this report, so we do **not** quote it verbatim; the "stabilized" characterization above rests instead on the two independently verifiable observations from the 9.4 reference. Treat the label as **well-supported by current-doc evidence**, not as a paraphrased quote.

---

## 4. What the C++ classes cannot reach (capability gap)

**DATA** unless marked. "No" means *no wrapper in the C++ classes*; the capability still exists in the product via the C MQI.

| Capability | C MQI (`cmqc.h`) | C++ classes (`imqi.hpp`) | Reference |
|---|---|---|---|
| Core connect / open / get / put / commit / backout | Yes | Yes (wrapped) | `[CPPLIST]` |
| MQMD / MQGMO / MQPMO control, headers, distribution lists | Yes | Yes (wrapped) | `[CPPLIST]` |
| TLS client channel config (cipher, peer name) | Yes | Yes (`ImqChannel`) | `[CPPLIST]` |
| **Automatic client reconnect — programmatic** (`MQCNO_RECONNECT`) | Yes | **No** (option not in documented set) | `[IMQQM]` `[RECON]` |
| **Reconnection event handler** | Yes (via `MQCB` event handler) | **No** | `[RECON]` `[CPPLIST]` |
| Automatic client reconnect — **external enable** (`DefRecon`) | Yes | **Yes** — enabled *below* the classes, at the client library | `[RECON]` |
| **Asynchronous / callback consume** (`MQCB` / `MQCTL`) | Yes | **No** | `[CPPLIST]` |
| **Message properties & selectors** (`MQSETMP` / `MQINQMP`) | Yes | **No** | `[CPPLIST]` |
| **Integrated publish/subscribe** (`MQSUB`) | Yes | **No** | `[CPPLIST]` |
| Maintenance status | First-class | **Stabilized / frozen** | `[DEP]` |

The reconnect row is the one with resiliency consequences, so it gets its own section.

---

## 5. Client reconnection — the resiliency crux

### 5.1 What automatic client reconnection is (DATA)

*"Automatic client reconnection is inline. The connection is automatically restored at any point in the client application program, and the handles to open objects are all restored."* — versus **manual** reconnection, where the application must re-issue `MQCONN`/`MQCONNX` and reopen objects itself. `[RECON]`

**Support matrix (DATA, `[RECON]` Table 1):** for the messaging APIs **C, C++**, COBOL, unmanaged VB and XMS — *program access to reconnection options* and *reconnection support* are both available from **IBM MQ 7.0.1**. (Note: this is the messaging-API family at the **client-library** level. Whether a *given binding* surfaces the options programmatically is separate — and for the C++ classes it does not, per §4.)

**Requirements (DATA, `[RECON]` Table 2):**

| Component | Requirement | Effect if not met |
|---|---|---|
| Client & server install | ≥ 7.0.1 | `MQRC_OPTIONS_ERROR` |
| Channel | `SHARECNV > 0` | `MQRC_ENVIRONMENT_ERROR` |
| Application environment | **Must be threaded** | `MQRC_ENVIRONMENT_ERROR` |
| MQI | `MQCONNX` with `MQCNO_RECONNECT`/`MQCNO_RECONNECT_Q_MGR`, **or** `DefRecon=YES\|QMGR` in `mqclient.ini` | `MQCC_FAILED` on break |

Default reconnection timeout: `MQReconnectTimeout = 1800` seconds in `mqclient.ini`; after it expires, `MQRC_RECONNECT_FAILED`. `[RECON]`

### 5.2 The two doors — only one is open to a C++-classes app

- **Programmatic door** — `MQCONNX` + `MQCNO_RECONNECT`, plus an optional reconnection **event handler**. **JUDGMENT (verified against `[IMQQM]`):** not available through the stabilized C++ classes — the reconnection options are absent from `ImqQueueManager`'s documented connect-option set, and there is no C++ event-handler wrapper. Reaching this door means calling the **C MQI**.
- **External door** — set `DefRecon=YES` (or `QMGR`) in `mqclient.ini`. **DATA:** *"An existing client application might be able to benefit from reconnection support, without recompilation and linking: for a non-JMS client, set the mqclient.ini environment variable DefRecon."* `[RECON]` This flips reconnection on at the **client-library layer beneath** the C++ classes, so it works for a C++-classes app **with no code change**.

**JUDGMENT:** for an application locked to the C++ classes, the external door is the *only supported* way to enable automatic reconnection. But "enable" ≠ "safe" — see §5.3.

### 5.3 Enabling reconnect is not free — the application must be reconnection-safe (DATA)

`[RECON]` is explicit that turning reconnect on obliges the application to tolerate an inline reconnect:

- **Syncpoint work must be resubmittable.** *"You might have to issue MQI calls within the sync point, and resubmit backed-out transactions."* On a mid-unit-of-work break, the transaction is backed out and the application receives an error; it must detect and retry.
- **`MQRC_CALL_INTERRUPTED` — unknown-outcome hazard.** *"Returned when the connection breaks during the execution of a Commit call and the client reconnects. An `MQPUT` of a persistent message outside the sync point also results in the same reason code."* An out-of-syncpoint persistent put that is interrupted leaves the application unable to tell whether the message was delivered — a duplicate-or-loss decision the application must own.
- **`MQRC_RECONNECT_INCOMPATIBLE` — ordering conflict.** Using `MQPMO_LOGICAL_ORDER`/`MQGMO_LOGICAL_ORDER` (message groups) with reconnect options set returns this reason code. Strict logical ordering and automatic reconnect are **mutually exclusive**.

**JUDGMENT:** these obligations are the real cost. `DefRecon=YES` is a one-line config change; making a transactional consumer/producer *correct* under inline reconnect is application work.

---

## 6. IBM's recommended paths for C/C++ today, and the options

**DATA:** IBM did not ship a successor C++ **object** framework; the `Imq*` classes are the only one, and they are the stabilized ones. "Modern C/C++ + MQ" therefore means one of the following. **The rest of this section is JUDGMENT**, grounded in the DATA above.

- **Option A — Re-base the MQ layer on the C MQI (`cmqc.h`).** The native interface is first-class and fully maintained; it exposes everything the classes cannot — `MQCNO_RECONNECT`, the reconnection event handler, `MQCB` async consume, properties, pub/sub. C++ calls it directly (C linkage). A thin RAII wrapper reproduces the ergonomics of the `Imq*` classes with none of the frozen-function-level limits. **This is the direct answer to "how do we implement reconnect (etc.) in C++ today."**
- **Option B — External `DefRecon` stopgap.** Zero code change to *enable*; requires `SHARECNV>0` + a threaded app, **and** the §5.3 reconnection-safety work before it is trustworthy. Good as a bridge, not a destination.
- **Option C — XMS for C/C++** (Message Service Clients). IBM's maintained, JMS-style API for C/C++; supports automatic reconnection. The closest thing to a "modern higher-level" C/C++ option, at the cost of adopting a new API model.
- **Option D — Status quo (C++ classes, connect-time failover only).** Supported and valid *if* no runtime-reconnect requirement exists. Requires documenting the residual risk (a connection dropped mid-session surfaces as an error to the caller; recovery = tear down and reconnect).

### Decision matrix (JUDGMENT)

| Option | Effort | Runtime reconnect | Async consume / properties / pub-sub | Notes |
|---|---|---|---|---|
| **A — C MQI** | Moderate (rewrite the ~handful of wrapper methods) | **Yes** (programmatic + event handler) | **Yes** | Keeps existing architecture; removes the ceiling |
| **B — `DefRecon`** | ~0 to enable; **real** to make safe | Yes (external) | No | Bridge only; §5.3 obligations apply |
| **C — XMS C/C++** | Higher (new API) | Yes | Partial/Yes (per XMS) | New dependency & model |
| **D — status quo** | None | **No** | No | Only if reconnect is genuinely not required; document the risk |

---

## 7. Case study (anonymized): a trading back-office gateway wrapper

A production C++ wrapper (~one class, request/reply over an MQ **client** connection with TLS) was reviewed to ground the trade-offs above. Identifying detail has been removed; the shape is generic.

**What it does well (JUDGMENT):**

- Clean **RAII** lifecycle — objects close/disconnect on scope exit.
- **Transactional reads** — destructive get under `MQGMO_SYNCPOINT` with explicit `commit()`/`backout()`, and a single-in-flight-message guard.
- **TLS client channel** configured programmatically (cipher spec + peer name).
- **Reason codes surfaced** on every failure via the MQ constant-string tables — good observability.
- Correctly handles a subtle C++-class default: the class's implicit-disconnect behaviour defaults to **commit** (`IMQ_IMPL_DISC_COMMIT` `[IMQQM]`), so a destructor holding an uncommitted get would otherwise *commit* (destructively remove) the message — the wrapper guards this with an explicit `backout()` in its destructor.

**Risks, each mapped to the general findings:**

1. **Shared-input queue breaks ordering (correctness).** The consumer opens with `MQOO_INPUT_SHARED` and the design intends multiple concurrent consumers. IBM's message-ordering guarantee holds only for a **single** getting application; concurrent destructive gets interleave, so strictly-ordered messages are processed out of sequence. If ordering matters, use `MQOO_INPUT_EXCLUSIVE` (single active consumer). Ties to §5.3: the strict-ordering primitive (`MQGMO_LOGICAL_ORDER`) is itself incompatible with reconnect.
2. **No runtime reconnect (resiliency) — the headline.** Failover is **hand-rolled at connect time only** (try primary channel, else reconfigure to a backup and connect). A connection dropped *mid-session* is **not** recovered — it surfaces as a failed `get()`/`put()`. This is exactly the §5 gap: on the stabilized C++ classes, programmatic reconnect is unavailable; the only supported route is external `DefRecon`, and only after the safety work below.
3. **Persistent put outside syncpoint (reconnect hazard).** The send path puts a `MQPER_PERSISTENT` message with **no** `MQPMO_SYNCPOINT`. Under automatic reconnect this is precisely the `MQRC_CALL_INTERRUPTED` unknown-outcome case (§5.3): after an interrupted put, delivery is indeterminate. The current code treats put failure as a plain retryable string — it does **not** implement the duplicate-or-loss decision that reconnect-safety requires.
4. **`setContextReference` with no pass-context option (likely dead code).** The put options set a context reference, but the PMO carries no `MQPMO_PASS_*_CONTEXT` flag and the queue is not opened with `MQOO_*_CONTEXT`. The reference is therefore almost certainly ignored. Low blast radius; worth confirming intent.
5. **Error object inconsistency (diagnostics).** On put failure the code reads the completion/reason code from the *message* object; the failing verb is `queue.put(...)`, so the authoritative reason lands on the *queue* object. The get path correctly reads the queue. Result: a real put failure may log a stale/zero reason code instead of (e.g.) `MQRC_Q_FULL`. Diagnostics-only, but a real inconsistency.

**JUDGMENT — how this case reads for a resiliency programme:** it is a near-perfect "before" specimen — a well-built wrapper pinned to a *stabilized* API, with *connect-time* failover but *no* runtime reconnect, and a persistent-put path that is not reconnect-safe. The "after" is §6 Option A: re-base on the C MQI, add `MQCNO_RECONNECT` + a connection-name list / CCDT, make the syncpoint read resubmittable, and handle `MQRC_CALL_INTERRUPTED` on the put.

---

## 8. Recommendation restated

1. **Decide the requirement first:** does this application need to survive a broken connection / queue-manager failover *without operator or caller intervention*? That single question selects the path.
2. **If yes →** it cannot be met programmatically on the C++ classes. Prefer **Option A (C MQI re-base)** for a durable fix; use **Option B (`DefRecon`)** only as a bridge and only with the §5.3 safety work done.
3. **If no →** the stabilized C++ classes are supported and require no migration; **document the residual no-runtime-failover risk explicitly** (Option D) so it is an accepted decision, not an unnoticed default.
4. **Independently of the interface choice,** fix the ordering exposure (§7.1) if ordering is a requirement, and the diagnostics inconsistency (§7.5).

---

## 9. Sources

All IBM sources are IBM MQ **9.4.x** documentation, accessed **2026-08-19**. Canonical text was retrieved with the repo's `tools/ibm_doc_cache.py` (IBM Docs return HTTP 403 to a bot user-agent) and cached under `build/refs/ibm-docs/ibm-mq/9.4.x/`.

- `[DEP]` — *Deprecated, stabilized, and removed features in IBM MQ 9.4.0* — https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=940-deprecated-stabilized-removed-features-in-mq
- `[RECON]` — *Automatic client reconnection* (IBM MQ 9.4) — https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=restart-automatic-client-reconnection
- `[CPPLIST]` — *IBM MQ C++ classes* (class inventory, MQI encapsulation, thread-safety note) — https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-mq-c-classes
- `[IMQQM]` — *ImqQueueManager C++ class* (connect options, `setConnectOptions`, `IMQ_IMPL_DISC_COMMIT` default) — https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=classes-imqqueuemanager-c-class

**Not quoted verbatim:** IBM's historical "enhancements will not be applied to the C++ classes" statement (in the *Using C++* book, e.g. the WebSphere MQ 7.0 edition). The "stabilized" characterization in §3 is instead grounded in `[DEP]`, `[CPPLIST]`, and `[IMQQM]`.

---

## Appendix A — deep engineering reference

> For the engineers who would perform a migration. **DATA** unless marked.

### A.1 MQI ↔ C++ class mapping (orientation)

The C++ classes map 1:1 onto MQI structures: `ImqMessage` ⇄ `MQMD` (+ data), `ImqGetMessageOptions` ⇄ `MQGMO`, `ImqPutMessageOptions` ⇄ `MQPMO`, `ImqChannel` ⇄ `MQCD`, `ImqQueue` ⇄ `MQOD`/`MQOO_*`, `ImqQueueManager` ⇄ `MQCONN`/`MQCONNX` + `MQCNO`. `[CPPLIST]` A full C++-and-MQI cross-reference is published by IBM (see the "C++ and MQI cross-reference" topic linked from `[CPPLIST]`). Re-basing on the C MQI is therefore a mechanical, structure-by-structure translation for the core paths — the new value is the *options that the classes never exposed*.

### A.2 `ImqQueueManager` connect options — the frozen set (DATA, `[IMQQM]`)

`ImqQueueManager` provides `void setConnectOptions(const MQLONG options = MQCNO_NONE)` and `MQLONG connectOptions() const`. The documented option values are:

```
MQCNO_NONE (initial)
MQCNO_STANDARD_BINDING
MQCNO_FASTPATH_BINDING
MQCNO_HANDLE_SHARE_NONE
MQCNO_HANDLE_SHARE_BLOCK
MQCNO_HANDLE_SHARE_NO_BLOCK
MQCNO_SERIALIZE_CONN_TAG_Q_MGR
MQCNO_SERIALIZE_CONN_TAG_QSG
MQCNO_RESTRICT_CONN_TAG_Q_MGR
MQCNO_RESTRICT_CONN_TAG_QSG
```

`MQCNO_RECONNECT` and `MQCNO_RECONNECT_Q_MGR` are **absent** — every value listed predates the 7.0.1 reconnect feature. `connect()` issues `MQCONNX` when a channel reference is present (client connection). `[IMQQM]`

### A.3 Implicit-disconnect default (DATA, `[IMQQM]`)

The `behavior` attribute controls implicit connect/disconnect. The default is `IMQ_IMPL_DISC_COMMIT` (4L): *"An implicit call to the disconnect method, which can occur during object destruction, implies commit (the default)."* Consequence: RAII destruction of a queue-manager object holding an uncommitted unit of work **commits** it unless the code explicitly backs out first.

### A.4 Reconnection reason codes to handle (DATA, `[RECON]`)

- `MQRC_RECONNECTING` — a break occurred; reconnection is in progress (may fire repeatedly).
- `MQRC_RECONNECTED` — reconnected; handles reestablished.
- `MQRC_RECONNECT_FAILED` — reconnection did not succeed (e.g. after `MQReconnectTimeout`).
- `MQRC_RECONNECT_QMID_MISMATCH` — `MQCNO_RECONNECT_Q_MGR` was set but the target was a different queue manager.
- `MQRC_RECONNECT_Q_MGR_REQD` — an option requiring same-QM reconnect (e.g. `MQMO_MATCH_MSG_TOKEN`) was used.
- `MQRC_RECONNECT_INCOMPATIBLE` — `MQ*MO_LOGICAL_ORDER` used with reconnect options (message groups vs. reconnect — mutually exclusive).
- `MQRC_CALL_INTERRUPTED` — break during `MQCMIT`, or during an out-of-syncpoint persistent `MQPUT`; outcome indeterminate — application must decide.
- Note: `MQCONNX` **itself** is not retried by auto-reconnect; on `MQRC_STANDBY_Q_MGR` (2543) the app reissues the connect after a short delay.

### A.5 Reconnection-safe patterns (JUDGMENT, from `[RECON]` guidance)

- Do transactional gets under syncpoint and **resubmit** backed-out work after a reconnect error.
- Treat an interrupted persistent put (`MQRC_CALL_INTERRUPTED`) as *unknown outcome*: either make the flow idempotent (dedupe on a business key or `MsgId`/`CorrelId`) or put **inside** syncpoint so the outcome is definite.
- Shorten `HBINT` (MQ heartbeat) / `KAINT` (TCP keepalive, z/OS) so broken connections are detected quickly rather than at the default TCP two-hour keepalive `[RECON]`.
- Use a **connection-name list** or a **CCDT** with multiple instances so reconnect has somewhere to go (multi-instance QM, HA/replicated-data QM, or a QM group).

### A.6 Reconnect-safe skeleton on the C MQI (illustrative, JUDGMENT)

```c
/* Enable reconnect at connect time (unavailable through ImqQueueManager). */
MQCNO cno = {MQCNO_DEFAULT};
cno.Options = MQCNO_RECONNECT;      /* or MQCNO_RECONNECT_Q_MGR for same-QM affinity */
cno.Version = MQCNO_VERSION_5;      /* CD/SCO references live here for a client build */
MQCONNX(qmgr, &cno, &hconn, &cc, &rc);

/* Transactional read: resubmit on a reconnect-class error. */
MQGMO gmo = {MQGMO_DEFAULT};
gmo.Options = MQGMO_SYNCPOINT | MQGMO_FAIL_IF_QUIESCING;   /* NOT *_LOGICAL_ORDER under reconnect */
/* ... MQGET; on MQCC_FAILED with a reconnect/connection-broken reason, back out and retry ... */

/* Persistent send: put INSIDE syncpoint so the outcome is definite, */
/* OR keep it out-of-syncpoint and handle MQRC_CALL_INTERRUPTED as unknown-outcome. */
MQPMO pmo = {MQPMO_DEFAULT};
pmo.Options = MQPMO_SYNCPOINT | MQPMO_FAIL_IF_QUIESCING;
/* ... MQPUT ... MQCMIT ... */
```

---

## Appendix B — provenance & confidence

- **Data vs. judgment:** §3–§5 and Appendix A.2–A.4 are **DATA** tied to the labelled sources. §1 recommendation, §6 options/matrix, §7 case-study interpretation, and A.5–A.6 are **JUDGMENT** built on that data.
- **Verification note:** one claim in an earlier draft — "the C++ classes expose no `MQCNO` option at all" — was **corrected** against `[IMQQM]`: `setConnectOptions` exists, but its documented option set is frozen and excludes the reconnect options. The precise, defensible statement is used throughout.
- **Anonymization:** the §7 case study is stripped of all client-identifiable material and framed generically, per project policy.
- **Currency:** pinned to IBM MQ 9.4.x, accessed 2026-08-19. Re-verify against the then-current version before acting if IBM MQ has moved on.
