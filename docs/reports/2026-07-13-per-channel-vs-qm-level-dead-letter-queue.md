# Per-channel vs QM-level dead-letter queue — research report

> **Issue:** #614 (Stage 2 research) · **Date:** 2026-07-13 · **Status:**
> Research captured — settles the Stage 2 DLQ design fork and confirms the
> interim `mqsvc` DLQ grant.
> **Scope:** IBM MQ **9.4** on distributed (Linux) queue managers. The question
> is whether a **dedicated per-channel / per-remote-QM dead-letter queue** is
> achievable, or whether the DLQ is strictly a **queue-manager** attribute.
> z/OS and MQ clustering are out of scope except where a cited page states a
> cross-platform rule.

---

## 0. How to read this report

Every claim is labelled **[data]** (what an IBM 9.4 page actually says, with a
citation) vs **[judgment]** (reasoning on top of those facts). The user
fact-checks research against primary sources, so the separation is deliberate:
where a point is inference rather than documented behavior, it says so.

**Provenance.** All **[data]** claims are quoted from IBM MQ 9.4.x
documentation fetched with `tools/ibm_doc_cache.py` (the plain WebFetch is
HTTP-403'd by IBM Docs) and cached under
`build/refs/ibm-docs/ibm-mq/9.4.x/<slug>/content.txt`; each is cited by slug and
`source_url`. Retrieval date: **2026-07-13**. Full source list in §8. No MQ
behavior below is asserted from memory — where a primary 9.4 page could not be
cached, the claim is flagged.

---

## 1. The originating question (the design fork)

The lab's Stage 2 distributed arm runs QM-to-QM over sender/receiver channel
pairs (`docs/specs/2026-06-13-distributed-mq-architecture-design.md` §5). The
receiving MCA — on the counterparty QM `QMSVC`, running under the `mqsvc`
`MCAUSER` — must be able to dispose of a message it cannot deliver to the target
application queue (e.g. the queue is full or put-inhibited). Today that MCA is
granted **`+put` on the queue-manager-wide `SYSTEM.DEAD.LETTER.QUEUE`** as an
**outage-safe interim** (spec §7.1). The fork: *keep the QM-wide grant, or
replace it with a dedicated per-counterparty / per-channel DLQ so the grant's
blast radius shrinks to a queue only that channel touches?*

That fork only has two arms if MQ actually offers a per-channel DLQ. This
report establishes whether it does.

## 2. The one mental model: WHICH queue vs WHETHER to use it

MQ splits dead-lettering into two independent settings, and conflating them is
the source of the "per-channel DLQ" confusion:

| | "**Which** queue is the DLQ?" | "**Whether** this channel dead-letters at all" |
|---|---|---|
| Setting | **`DEADQ`** | **`USEDLQ`** |
| Object | **queue manager** (one per QM) | **channel** (per channel) |
| Set by | `crtmqm -u <q>` or `ALTER QMGR DEADQ(<q>)` | `DEFINE/ALTER CHANNEL … USEDLQ(YES|NO)` |
| Values | a single local queue name | `YES` (default) / `NO` |

**[judgment]** The whole answer falls out of this table: the only *per-channel*
lever is `USEDLQ`, and it is a **boolean** — it decides *whether* the channel
uses the queue manager's one DLQ, never *which* queue. There is no per-channel
or per-remote-QM `DEADQ`. §3 and §4 substantiate each half with primary text.

## 3. The DLQ name is a queue-manager attribute (strictly QM-level)

**[data]** The dead-letter queue name is a **queue-manager** attribute,
`DEADQ`, listed among the `ALTER QMGR` settings: *"DEADQ(string) The local name
of a dead-letter queue (or undelivered-message queue) on which messages that
cannot be routed to their correct destination are put."*
(`reference-alter-qmgr-alter-queue-manager-settings`,
<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-alter-qmgr-alter-queue-manager-settings>).
The queue-manager attribute list names it once, as `DeadLetterQName`
(`objects-attributes-queue-manager`,
<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=objects-attributes-queue-manager>).

**[data]** You set it at QM creation or later, but it is a **single** QM-level
name: *"specify a dead-letter queue name on the crtmqm command (`crtmqm -u
DEAD.LETTER.QUEUE`, for example), or by using the DEADQ attribute on the ALTER
QMGR command to specify one later."* And *"Each queue manager typically has a
local queue to use as a dead-letter queue"*
(`objects-working-dead-letter-queues`,
<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=objects-working-dead-letter-queues>).

**[data]** The concept page repeats the one-per-QM framing: *"Each queue manager
typically has a dead-letter queue … Every queue manager in a network typically
has a local queue to be used as a dead-letter queue"*
(`components-dead-letter-queues`,
<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=components-dead-letter-queues>).

**[judgment]** There is **no per-channel `DEADQ` attribute and no
per-remote-QM DLQ**. `DEADQ` appears only on the queue manager; the channel
attribute reference lists `USEDLQ` (a yes/no), not any DLQ-name attribute
(`reference-channel-attributes`,
<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-channel-attributes>).
A receiving MCA that dead-letters therefore always writes to the **one** queue
named in its queue manager's `DEADQ`. This is the load-bearing finding.

## 4. The only per-channel lever: `USEDLQ` (a boolean, not a redirect)

**[data]** `USEDLQ` is a **channel** attribute — *"You set the USEDLQ channel
attribute to determine whether the dead-letter queue is used when messages
cannot be delivered"* (`objects-working-dead-letter-queues`, cited above) —
applicable across channel types including receiver channels
(`reference-channel-attributes`, cited above).

**[data]** Its values, verbatim (`attributes-channel-mqsc-keywords-t-z`,
<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=attributes-channel-mqsc-keywords-t-z>):

> "USEDLQ (Use dead-Letter queue) — This attribute determines whether the
> dead-letter queue (or undelivered message queue) is used when messages cannot
> be delivered by channels. Possible values are: **NO** Messages that cannot be
> delivered by a channel are treated as a failure. The channel either discards
> these messages, or the channel ends, in accordance with the setting of
> NPMSPEED. **YES (default)** If the queue manager DEADQ attribute provides the
> name of a dead-letter queue, then it is used, otherwise the behavior is as for
> NO."

**[judgment]** This is the decisive sentence: `USEDLQ(YES)` means *"the queue
manager `DEADQ` … is used."* The channel cannot point at its own queue; it can
only opt **in to** or **out of** the QM's single DLQ. So even the one
per-channel knob confirms the QM-level model rather than offering an
alternative.

**[data]** Opting out is not free. With no DLQ (or `USEDLQ(NO)`), *"if the MCA
is unable to put a message, it is left on the transmission queue and the channel
is stopped. Also, if fast, non-persistent messages … cannot be delivered, and
no dead-letter queue exists on the target system, these messages are discarded"*
(`components-dead-letter-queues`, cited above). **[judgment]** That is exactly
the outage the interim grant exists to prevent — a stopped receiver channel
stalls the whole counterparty flow.

## 5. Can per-counterparty routing be achieved at all? Yes — only downstream

Segregating dead letters *by counterparty* is achievable, but **not at
put-time** and **not as a separate DLQ the MCA writes to**. Two mechanisms
exist, both consuming the single QM DLQ after the fact.

### 5.1 The DLQ handler (`runmqdlq`) + rules table

**[data]** *"To process messages on a dead-letter queue (DLQ), use the default
DLQ handler that is provided by IBM MQ. The handler matches messages on the DLQ
against entries in a rules table that you define"*
(`queues-processing-messages-mq-dead-letter-queue`,
<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=queues-processing-messages-mq-dead-letter-queue>).
It is a **batch utility** invoked with `runmqdlq`, and *"All IBM MQ environments
need a routine to process messages on the DLQ regularly."*

**[data]** The rules table matches on **MQMD/MQDLH fields** and can forward. The
pattern-matching keywords are `APPLIDAT`, `APPLNAME` (`PutApplName`), `APPLTYPE`,
`DESTQ` (destination queue), `DESTQM` (destination queue manager), `FEEDBACK`,
`FORMAT`, `MSGTYPE`, `PERSIST`, `REASON`, `REPLYQ`, `REPLYQM` (reply-to queue
manager), `USERID`; the action keywords are `ACTION(DISCARD|IGNORE|RETRY|FWD)`
with `FWDQ`/`FWDQM` for forwarding (`table-dlq-rules-patterns-actions`,
<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=table-dlq-rules-patterns-actions>).

**[judgment]** So per-counterparty segregation is a **downstream forward**:
match on `DESTQ`/`DESTQM` (each counterparty's target queue differs) or
`REPLYQM` (the origin QM in a request/reply) and `ACTION(FWD)` to a
per-counterparty queue. It does **not** give the receiver MCA its own DLQ — every
dead letter still lands on the QM's one `DEADQ` first, and the MCA still needs
`+put` there.

**[data + judgment] No reliable "source channel" key.** The intuitive route
"match on the channel name" is weak. `MQDLH.PutApplName` — the field behind the
`APPLNAME` keyword — is documented as: *"If the queue manager redirects the
message to the dead-letter queue, PutApplName contains the first 28 characters of
the queue manager name"* (`header-field-details-mqdlh`,
<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=header-field-details-mqdlh>). So
for a QM/MCA-redirected message the field carries the **queue manager name**, not
the receiving channel name; `MQDLH` records `DestQName`/`DestQMgrName` (the
intended destination) and `Reason`, but has **no dedicated source-channel
field**. [judgment] Per-*channel* routing via the handler is therefore not
dependable; per-*destination-queue* or per-*origin-QM* routing is.

### 5.2 A channel exit

**[judgment]** A channel message/receive exit could in principle intercept and
redirect, but exits fire on the channel data stream, not on the MCA's
put-failure decision, and writing one is bespoke C/Java against the exit API.
This is not a DLQ mechanism and is not recommended for the lab; noted only for
completeness. (Not separately cited — asserted as engineering judgment, not
documented behavior.)

## 6. Verdict

**[judgment]**

1. **Is a dedicated per-channel / per-remote-QM DLQ supported? No.** The DLQ
   *name* is strictly a **queue-manager** attribute (`DEADQ`), set once per QM.
   The only per-channel control is `USEDLQ(YES|NO)` — a boolean over the QM's
   single DLQ, not a redirect (§3, §4, all **[data]**).
2. **Can dead letters be split per counterparty? Yes, but only downstream** —
   the `runmqdlq` handler forwards from the one QM DLQ by matching
   `DESTQ`/`DESTQM`/`REPLYQM`/`REASON` (§5.1). Cost: a continuously-run batch
   utility, a maintained rules table, re-put/forward semantics, an effect on
   delivery ordering, no reliable source-channel key, and the receiver MCA still
   needs `+put` on the QM DLQ regardless.
3. **A channel exit** is the only true per-channel interception point and is not
   worth it (§5.2).

## 7. Recommendation for the lab

**[judgment] Keep the QM-level DLQ and the QM-wide `+put` grant. Do not pursue a
dedicated per-channel DLQ — it does not exist as an MQ feature.**

- The `mqsvc` receiver MCA's dead-letter target is **dictated by `QMSVC`'s
  `DEADQ`**; it cannot be pointed at a private queue. The `+put` on
  `SYSTEM.DEAD.LETTER.QUEUE` is therefore **not a temporary stand-in for a future
  dedicated DLQ** — it is the architecturally required grant for a receiver MCA
  to dead-letter at all under `USEDLQ(YES)`. The "interim" **is** the
  destination. **This confirms rather than replaces the Stage 2 grant** (spec
  §7.1); the grant should be reclassified from "interim" to "final, minimal."
- The grant is already minimal: `+put` on exactly **one** queue. It cannot be
  scoped narrower via a per-channel DLQ, because no such queue exists. Renaming
  the QM's single `DEADQ` to a per-counterparty name (e.g. on the dedicated
  `QMSVC`) is cosmetic — it is still the QM-level DLQ; on a QM dedicated to one
  counterparty it is already effectively per-counterparty.
- **Only if** a single QM ever hosts multiple counterparties and forensic
  separation of their dead letters is required, add a `runmqdlq` rules table that
  forwards by `DESTQ`/`DESTQM` to per-counterparty queues (§5.1) — an operational
  add-on, not a change to the grant.

### 7.1 What the N5 undeliverable-message validation should assert

**[judgment]** Frame N5 to prove the grant is **load-bearing**, using the
loss-quantification framing already established in the lab:

1. **Induce undeliverability** on the receiver path — put-inhibit or fill the
   target application queue (`SVC.REQUEST` on `QMSVC`) while messages flow over
   the receiver channel.
2. **Assert the message is dead-lettered on the QM `DEADQ`** with an `MQDLH`
   whose `Reason` is `MQRC_Q_FULL` / `MQRC_PUT_INHIBITED` and whose `DestQName`
   is the intended queue — the documented behavior under `USEDLQ(YES)` + `DEADQ`
   defined (§3, §4).
3. **Assert the channel stays RUNNING** (does not stop). The negative control —
   remove the `+put` grant, or set `USEDLQ(NO)`, or leave `DEADQ` undefined —
   must show the message is **not** dead-lettered and instead the channel
   **stops** with the message stranded on the transmission queue (or a fast
   non-persistent message discarded). This directly demonstrates the outage the
   grant prevents (§4, **[data]**), i.e. it proves the grant is required, not
   decorative.
4. **Assert count conservation**: the dead-lettered message is neither lost nor
   duplicated — it is accounted for on the DLQ (ties into the existing
   loss-quant / RPO framework).

**[judgment]** N5 should **not** attempt to assert per-channel DLQ isolation —
this report establishes there is nothing to assert there.

## 8. Sources

All IBM pages are **IBM MQ 9.4.x**, fetched + cached 2026-07-13 via
`tools/ibm_doc_cache.py` under `build/refs/ibm-docs/ibm-mq/9.4.x/<slug>/`
(gitignored; cite `content.txt` + `source_url`). The tool is committed; the
cached IBM content is not redistributed.

- **Working with dead-letter queues** (`objects-working-dead-letter-queues`) —
  `DEADQ` set via `crtmqm -u` / `ALTER QMGR`; `USEDLQ` determines *whether* the
  DLQ is used:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=objects-working-dead-letter-queues>
- **Dead-letter queues** (concept) (`components-dead-letter-queues`) — one DLQ
  per QM; no-DLQ ⇒ message left on xmitq + channel stopped:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=components-dead-letter-queues>
- **ALTER QMGR (settings)** (`reference-alter-qmgr-alter-queue-manager-settings`)
  — `DEADQ(string)` is a queue-manager attribute:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-alter-qmgr-alter-queue-manager-settings>
- **Attributes for the queue manager** (`objects-attributes-queue-manager`) —
  `DeadLetterQName`:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=objects-attributes-queue-manager>
- **Channel attributes** (`reference-channel-attributes`) — `USEDLQ` is the
  channel-level DLQ attribute (no DLQ-name channel attribute exists):
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-channel-attributes>
- **Channel MQSC keywords T–Z** (`attributes-channel-mqsc-keywords-t-z`) —
  `USEDLQ(NO|YES)` values; `YES` uses the QM `DEADQ`:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=attributes-channel-mqsc-keywords-t-z>
- **Processing messages on a DLQ** (`queues-processing-messages-mq-dead-letter-queue`)
  — `runmqdlq` handler + rules table:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=queues-processing-messages-mq-dead-letter-queue>
- **DLQ rules (patterns and actions)** (`table-dlq-rules-patterns-actions`) —
  pattern/action keywords (`DESTQ`, `DESTQM`, `REPLYQM`, `APPLNAME`, `REASON`,
  `ACTION(FWD)` …):
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=table-dlq-rules-patterns-actions>
- **MQDLH — Dead-letter header** (`header-field-details-mqdlh`) — `DestQName`,
  `DestQMgrName`, `PutApplName` (= QM name when the QM redirects):
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=header-field-details-mqdlh>
- **Using the dead-letter (undelivered message) queue**
  (`errors-using-dead-letter-undelivered-message-queue`) — QM adds `MQDLH` on
  put; DLQ name inquired via `DeadLetterQName`:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=errors-using-dead-letter-undelivered-message-queue>

### 8.1 Access notes / verification gaps

- **[data]** The dedicated `USEDLQ` **keyword** page
  (`…?topic=keywords-usedlq-use-dead-letter-queue`, the 9.2 slug) could not be
  cached at 9.4 — the tool reported *"no oldUrl (content path) found"* (the
  topic moved). The `USEDLQ` values are instead sourced from the **9.4 channel
  MQSC keyword reference** (`attributes-channel-mqsc-keywords-t-z`) and the
  **channel attributes** table — both primary 9.4 pages — so there is no gap in
  the load-bearing claim.
- **[judgment]** The authorization mechanics of the DLQ put (that the receiving
  MCA writes under its `MCAUSER` and so needs `+put`, governed by
  `PUTAUT(DEF|CTX)` on the receiver channel) are consistent with the OAM model
  documented in the #347 client-identity report but were not re-verified against
  a primary page here; the lab's own operational finding (dead-lettering failed
  until `mqsvc` was granted `+put`) is the empirical basis and is what N5
  re-checks (§7.1).
