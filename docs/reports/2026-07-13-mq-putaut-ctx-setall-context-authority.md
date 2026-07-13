# PUTAUT(CTX), `+setall`/`+setid` and the receiver-channel context-authority
demand — research report

> **Issue:** #613 (Stage 2 epic logical-minds-foundry/.github#74) · **Date:**
> 2026-07-13 · **Status:** Research captured — closes the verification gap that
> the #347 brief (`docs/reports/2026-06-24-ibm-mq-client-identity-authorization.md`,
> §5 / §11 open question 1) and the Stage 2 spec (§8) flagged as unverified.
> **Scope:** Put-authority and message-context authorization on **receiver /
> requester (QM-to-QM)** channels, on **distributed (Multiplatforms:
> AIX/Linux/Windows)** IBM MQ **9.4**. z/OS is noted only where it diverges. This
> is the column-2 (message-context) layer the #347 report deliberately deferred.

---

## 0. How to read this report

Every claim is labelled **[data]** (what a cited primary source actually says) or
**[judgment]** (reasoning built on top of those facts). The load-bearing
conclusion of this report — that a non-privileged receiving MCA user needs
`+setall` **regardless of `PUTAUT`** — is a **two-step deduction from two primary
9.4 sources**, not a single verbatim sentence in the docs. It is labelled as such,
corroborated with secondary sources, and paired with an **empirical check** the
lab should run to confirm it on live MQ before it is treated as settled. This is
deliberate: the counterparty demand at the centre of this question ("+setall on
the receiver even for the ID we cite") is exactly the kind of MQ-security folklore
that is usually *stated* without a citation, and the whole point of the exercise
is to establish whether it is true and *why*.

**Provenance.** All primary citations are IBM MQ **9.4** documentation, fetched
with `tools/ibm_doc_cache.py` (IBM Docs 403 the plain WebFetch) and cached under
the gitignored `build/refs/ibm-docs/ibm-mq/9.4.x/`. Each cited page's
`source_url` is listed in §7. Secondary corroboration (an IBM Redbook, two IBM
support/APAR notes) is named inline and used only to support, never to establish,
a claim.

---

## 1. The question

The Stage 2 spec (§5) puts our inbound service-app receiver channel
(`{{ chl_to_app }}`, MCAUSER `mqsvc`) on **`PUTAUT(DEF)`** — "we decided who they
are (`mqsvc`); we do not trust what they stamp in `MQMD.UserIdentifier`." A
counterparty once insisted we had to grant **`+setall`** (context authority) on
the **receiver channel**, *even for the ID we were citing* — which sounded wrong.
Three sub-questions (spec §8):

1. **When does `PUTAUT(CTX)` force `+setid`/`+setall` on the MCA user?**
2. **Does our `PUTAUT(DEF)` posture avoid it entirely for inbound?**
3. **Does *sending* to a counterparty that runs `PUTAUT(CTX)` oblige anything on
   our side?**

## 2. The mechanism, from the primary source

**[data]** `PUTAUT` on a receiver/requester channel "specifies the type of
security processing to be carried out by the MCA … when executing an MQPUT
command to the destination queue." On **Multiplatforms it is valid only for
channel types RCVR, RQSTR, or CLUSRCVR** — it is **not** a sender/CLUSSDR
attribute. `ONLYMCA` and `ALTMCA` are z/OS-only. (Channel-attributes N–R page;
DEFINE CHANNEL reference.)

**[data]** The two values that exist on Multiplatforms, verbatim from the 9.4
channel-attribute reference:

- **`DEF` (process / default authority):** "The default user ID is used. On
  Multiplatforms, the user ID used to check open authority on the queue is that
  of the process or user running the MCA at the receiving end of the message
  channel. … **The queues are opened with this user ID and the open option
  `MQOO_SET_ALL_CONTEXT`.**"
- **`CTX` (context security):** "The user ID from the context information
  associated with the message is used as an alternate user ID. The
  `UserIdentifier` in the message descriptor is moved into the `AlternateUserId`
  field in the object descriptor. **The queue is opened with the open options
  `MQOO_SET_ALL_CONTEXT` and `MQOO_ALTERNATE_USER_AUTHORITY`.** On Multiplatforms,
  the user ID used to check open authority on the queue for `MQOO_SET_ALL_CONTEXT`
  and `MQOO_ALTERNATE_USER_AUTHORITY` is that of the process or user running the
  MCA at the receiving end … The user ID used to check open authority on the queue
  for `MQOO_OUTPUT` is the `UserIdentifier` in the message descriptor."

*(Source: `attributes-channel-mqsc-keywords-n-r`, the 9.4 "PUTAUT (PUT
authority)" entry.)*

**[data]** What those open options *cost* in authority, from the 9.4
authorizations reference: "In order to modify any of the message context options,
you must have the appropriate authorizations … in order to use
`MQOO_SET_IDENTITY_CONTEXT` … you must have `+setid` permission." `setall` = "Set
all context on the specified queue." **Note (verbatim):** "To use `setid` or
`setall` authority, authorizations must be granted on **both the appropriate
queue object and also on the queue manager object**." `setmqaut` groups `+setall`
/ `+setid` / `+passall` / `+passid` as the **context** authorizations, and
`allmqi` includes `setall`, `setid` **and** `altusr`. *(Sources:
`authority-authorizations-context`; `reference-setmqaut-grant-revoke-authority`.)*

**[data]** The receiving MCA reinstates the exact context that travelled with the
message from the sending MCA — that is *why* it opens the destination queue with
set-all-context. IBM's message-context documentation describes receiving MCAs
using `MQPMO_SET_ALL_CONTEXT` "to preserve exactly the message context that
travelled with the message from the sending MCA." *(Message-context / "Controlling
context information" topic, 9.4.)*

### The key deduction

**[judgment — deduction from the two [data] blocks above]** Combine them:

- **`MQOO_SET_ALL_CONTEXT` requires `+setall`** on the queue (and on the QM
  object). *(authorizations-context)*
- **Under BOTH `DEF` and `CTX`, the receiving MCA opens the destination queue with
  `MQOO_SET_ALL_CONTEXT`, and that open is authority-checked against the MCA
  (channel) user.** *(channel-attributes N–R)*

Therefore **the receiving MCA user needs `+setall` on the destination queue and on
the queue manager under `PUTAUT(DEF)` too — it is not a `CTX`-only requirement.**
What `DEF` vs `CTX` actually changes is *whose* identity the **put** (`MQOO_OUTPUT`)
is checked against, and whether `+altusr` is also needed:

| Authority checked at inbound MQPUT | `PUTAUT(DEF)` | `PUTAUT(CTX)` |
|---|---|---|
| `MQOO_SET_ALL_CONTEXT` (reinstate context) → **`+setall`** | **MCA user** (`mqsvc`) | **MCA user** (`mqsvc`) |
| `+setall` also on the **QM object** | MCA user | MCA user |
| `MQOO_ALTERNATE_USER_AUTHORITY` → **`+altusr`** | *(not opened)* | **MCA user** (`mqsvc`) |
| `MQOO_OUTPUT` (the put) → **`+put`** | **MCA user** (`mqsvc`) | **the stamped `MQMD.UserIdentifier`** |

**[judgment]** So the minimal grant to the receiver MCAUSER on the target queue is
**`+put +setall`** for a `DEF` receiver (plus `+setall` on the QM object), and
**`+setall +altusr`** for a `CTX` receiver (plus `+setall` on the QM object) *with
the additionally-required `+put` falling on whatever ID the message carries*.
`+setall` is common to both; it is the price of a **non-privileged** receiving MCA
that must reinstate context. When MCAUSER is blank / privileged (the historical
default: the MCA runs under the `mqm`-owned listener process), the account already
holds `setall`, so the requirement is invisible — which is exactly why hardening to
a low-privilege MCAUSER surfaces it.

**[data — secondary corroboration]** IBM Redbook SG24-8069 *Secure Messaging
Scenarios* pairs "always specify a low-privileged MCAUSER" with the consequent
need to grant that account the authorities the MCA exercises; IBM support note
"SETALL permission is needed for … MQ authorization" and APAR **IT21537** both
document `MQRC_2035` from an `MQOPEN`/`MQPUT` when a non-privileged user opens a
queue with `MQOO_SET_ALL_CONTEXT` **without** `+setall`. These are consistent with
the deduction; they are the observable failure mode it predicts.

## 3. Answering the three sub-questions

**Q1 — When does `PUTAUT(CTX)` force `+setid`/`+setall` on the MCA user?**
**[data + judgment]** `PUTAUT(CTX)` forces **`+setall`** (set-*all*-context, not
merely `+setid`) on the MCA user on *every* inbound message, because the queue is
opened with `MQOO_SET_ALL_CONTEXT` — the same as `DEF`. `CTX` *additionally* forces
**`+altusr`** on the MCA user and moves the **`+put`** check onto the message's
stamped `MQMD.UserIdentifier`. `+setid` alone is never sufficient for a channel
MCA (channels reinstate *all* context, so `setall`, not `setid`, is what applies);
`+setid` is the narrower right an *application* needs for `MQOO_SET_IDENTITY_CONTEXT`.

**Q2 — Does `PUTAUT(DEF)` avoid the `+setall` demand entirely for inbound?**
**[judgment — the load-bearing finding] No.** `PUTAUT(DEF)` avoids the *alternate-user*
machinery — no `+altusr`, and the counterparty's self-asserted `MQMD.UserIdentifier`
is **not** authority-checked for the put (that is genuinely what makes our
"assert-ownership" posture correct). But `DEF` does **not** avoid `+setall`: the
receiving MCA still opens the destination queue with `MQOO_SET_ALL_CONTEXT` to
reinstate context, checked against the MCA user (`mqsvc`). **The counterparty's
demand — "+setall on the receiver even for the ID we cite" — is therefore
essentially correct, and it is *not* evidence that they run `PUTAUT(CTX)`.**
`+setall` is the baseline requirement for a non-privileged receiving MCA under
*either* `PUTAUT` value. (The demand would only be *wrong* if they meant we must
grant `setall` to some identity on **our** side for messages we **send** — see Q3.)

**Q3 — Does *sending* to a `PUTAUT(CTX)` counterparty oblige anything on our
side?** **[judgment, grounded in the [data] that `PUTAUT` is a receiver-only
attribute] No configuration on our QM.** `PUTAUT` governs only the *receiving*
end; our sending MCA transmits the message with its context and is unaffected by
the far end's `PUTAUT`. The **only** consequence of the counterparty running
`PUTAUT(CTX)` is that the **`MQMD.UserIdentifier` we stamp on outbound messages
becomes load-bearing on *their* side** — their MCA will authority-check *that*
cited ID for `+put` on their target queue. That is a **coordination** point (agree
the exact ≤12-char ID we stamp, and make sure it maps to something they have
authorized), not a grant we make on our queue manager. Nothing about sending
requires `+setall`, `+setid`, or `+altusr` on our side.

## 4. Implication for the Stage 2 `pcmk-ubuntu` design

**Does the design need to change? Yes — one addition to the `mqsvc` grant set;
the `PUTAUT(DEF)` decision itself stands.**

1. **`PUTAUT(DEF)` on `{{ chl_to_app }}` is correct and stays.** [judgment] The
   spec's "assert ownership" rationale (§5) holds exactly: under `DEF` the
   counterparty's stamped `MQMD.UserIdentifier` is **not** used for the put-authority
   decision, so a counterparty stamping `mqm` (or any privileged/bogus string) is
   disregarded and the put is authorized as `mqsvc`. The N4 "assert-ownership"
   demo (spec §10) will behave as designed.

2. **Add `+setall` to the `mqsvc` grant surface (§7).** [judgment, from §2] The
   §7 minimal `setmqaut` set for `mqsvc` must include, in addition to `+put` on the
   reply/target queue it feeds (`APP.REPLY`):
   - **`+setall` on that target queue** (`setmqaut -t queue -n APP.REPLY -p mqsvc
     +setall`), and
   - **`+setall` on the queue manager object** (`setmqaut -t qmgr -p mqsvc
     +setall`) — the authorizations-context "both queue **and** QM object"
     requirement.

   Without these, the inbound RCVR `MQPUT` fails `2035 MQRC_NOT_AUTHORIZED` at the
   set-all-context open, the channel puts the reply to the DLQ (or goes
   `RETRY/STOPPED`), and legitimate counterparty replies never reach `APP.REPLY` —
   a failure that has **nothing to do with `PUTAUT` and would not be caught by the
   `PUTAUT(DEF)` reasoning alone.** This is the corner case §8 anticipated ("a
   specific right is genuinely required"). Note this widens `mqsvc` beyond the
   very-minimal `+put`; it is still far short of `mqm`, and `+setall` is scoped to
   the one queue plus the QM object.

3. **This does not fan out to the SVRCONN accounts.** [judgment] `mqapp` and
   `mqmon` arrive over **SVRCONN** (client) channels, where `PUTAUT`/context
   security does not apply ("Context security (CTX) is not supported on
   server-connection channels" — channel-attributes N–R) and the app puts under
   its own identity with normally-generated context. Only the **QM-to-QM receiver**
   MCAUSER (`mqsvc`, and its fan-out equivalents `NHARSVC` / the rdqm receiver)
   needs `+setall`.

### What the N4 assert-ownership validation should assert

The N4 negative/assert-ownership check (spec §10) should be **extended** from "the
stamped ID is ignored" to also pin the `+setall` boundary empirically — settling
the counterparty demand on live MQ rather than on this deduction:

- **[assert — ownership]** With `PUTAUT(DEF)` and `mqsvc` mapped, a counterparty
  message stamping `mqm` (or any unprivileged/bogus string) in
  `MQMD.UserIdentifier` **still lands on `APP.REPLY`** and is authorized as
  `mqsvc` — proving the stamped ID is **not** authority-checked (no alternate-user
  path). A bogus stamped ID that has *no* grants anywhere must succeed; if it
  fails, the channel is not really on `DEF`.
- **[assert — `+setall` is load-bearing]** With `mqsvc` holding `+put +setall`
  (queue) + `+setall` (qmgr), inbound replies land. Then **revoke `mqsvc`'s
  `+setall`** (`setmqaut … -p mqsvc -setall`) and re-drive one reply: the inbound
  RCVR `MQPUT` must fail **`2035`** and the message must divert to the DLQ / the
  channel stops — confirming `+setall` is required for the receiving MCA under
  `DEF`, i.e. the counterparty was right, and confirming it empirically the way
  §8 intends ("exercising the arm is what settles it").
- **[assert — scope]** `mqsvc`'s `+setall` is confined to `APP.REPLY` + the QM
  object; `mqsvc` still cannot reach `APP.SVRCONN`/monitoring queues or any admin
  object (`2035`), so the widened grant has not become a privilege escalation.

## 5. Bottom line

- **[judgment]** `PUTAUT(DEF)` does **not** dodge the `+setall` demand. A
  non-privileged receiving MCA needs **`+setall`** (on the target queue **and** the
  QM object) under **either** `PUTAUT` value, because it reinstates message context
  with `MQOO_SET_ALL_CONTEXT` on every inbound put. `PUTAUT(DEF)` dodges only the
  *alternate-user* layer (`+altusr`, and trusting the stamped `MQMD.UserIdentifier`
  for the put) — which is the part our "assert ownership" posture actually cares
  about.
- **[judgment]** The counterparty's "+setall on the receiver even for the ID we
  cite" is **essentially correct** and is **not** proof they run `PUTAUT(CTX)`; it
  is the baseline non-privileged-receiver requirement. Do not read it as a signal
  to change our inbound `PUTAUT`.
- **[judgment]** **Sending** to a `PUTAUT(CTX)` counterparty obliges **no config**
  on our QM — only that we agree the exact identity we stamp outbound, because it
  becomes their authorization key.
- **[judgment]** Stage 2 change: keep `PUTAUT(DEF)`; **add `+setall` on `APP.REPLY`
  and on the qmgr to the `mqsvc` grant set**; extend N4 to prove both that the
  stamped ID is ignored *and* that `mqsvc`'s `+setall` is load-bearing.

## 6. Confidence & residual uncertainty

**[judgment]** The **`CTX` → `+setall`/`+altusr`** answer is **high confidence** —
it is stated almost verbatim across the PUTAUT and authorizations-context pages.
The **`DEF` → `+setall`** answer (the load-bearing one) is a **medium-high
confidence deduction**: two primary 9.4 statements compose to it and multiple
secondary sources describe its exact failure mode (`2035` on set-all-context
without `+setall`), but IBM does not state "a `PUTAUT(DEF)` receiver MCAUSER needs
`setall`" in one sentence. The §4 empirical N4 check (grant → works; revoke →
`2035`) is what converts this from deduction to demonstrated fact on our arm, and
should run before the finding is treated as closed. What I did **not** attempt to
re-settle here (out of scope, and unchanged from #347): the exact MCAUSER
resolution order across `ChlauthEarlyAdopt` × `ADOPTCTX`, and z/OS RESLEVEL
behaviour (z/OS checks 0/1/2 IDs by RESLEVEL and adds `ONLYMCA`/`ALTMCA`, per the
PUTAUT page — the lab is distributed, so this is noted, not pursued).

## 7. Sources (IBM MQ 9.4, cached via `tools/ibm_doc_cache.py`)

Primary (cited [data] above; cached under `build/refs/ibm-docs/ibm-mq/9.4.x/`):

- Channel attributes for MQSC keywords (N–R) — the **PUTAUT (PUT authority)** entry
  (the crown citation: `DEF` and `CTX` both open with `MQOO_SET_ALL_CONTEXT`):
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=attributes-channel-mqsc-keywords-n-r>
- Channel attributes for MQSC keywords (M) — **MCAUSER** entry:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=attributes-channel-mqsc-keywords-m>
- Authorizations for context (`setall`/`setid` need queue **and** QM-object grant):
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=authority-authorizations-context>
- `setmqaut` (grant or revoke authority) — context authorizations, `allmqi`:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-setmqaut-grant-revoke-authority>
- DEFINE CHANNEL — PUTAUT parameter, MCAUSER interaction, RCVR/RQSTR/CLUSRCVR-only:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-define-channel-define-new-channel>
- Channel attributes (attribute-to-channel-type matrix; PUTAUT grouping):
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-channel-attributes>
- User identities in IBM MQ — `PUTAUT(CTX)` + the 12-char / long-identity caveat:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=authentication-user-identities-in-mq>

Secondary (corroboration only, named at point of use in §2):

- IBM Redbook SG24-8069, *Secure Messaging Scenarios with WebSphere MQ*
  (low-privileged MCAUSER; avoid context put-authority on channels):
  <https://www.redbooks.ibm.com/redbooks/pdfs/sg248069.pdf>
- IBM support — *"SETALL" permission is needed for … MQ authorization*:
  <https://www.ibm.com/support/pages/setall-permission-needed-websphere-datapower-mq-authorization>
- IBM APAR **IT21537** — `MQRC 2035` when a user has less than `SETALL` opening a
  queue with set-all-context:
  <https://www.ibm.com/support/pages/apar/IT21537>

Prior report this one closes the gap for:

- `docs/reports/2026-06-24-ibm-mq-client-identity-authorization.md` (#347), §5 /
  §11 open question 1.
