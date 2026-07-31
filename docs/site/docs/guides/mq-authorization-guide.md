# IBM MQ authorization — map an authenticated TLS client to a least-privilege identity

- **MQ version:** 9.4 (Multiplatforms, OS-based OAM authorization)
- **Status:** Draft — pending review
- **Last validated in lab:** 2026-07-31
- **Related guides:** [Instrumentation event monitoring](mq-event-monitoring-guide.md)
  — the authority-event class surfaces the `MQRC 2035` failures this guide's
  grants prevent; [TCP configuration](mq-tcp-config-guide.md) — the transport
  this sits above. TLS cipher and certificate setup (the *authentication* layer
  this guide's *authorization* builds on) is a sibling concern, covered
  separately.

---

## 1. Purpose & audience

This guide hardens **channel authorization**: once a client or a partner queue
manager has authenticated over mutual TLS, *what identity does it run as, and
what is that identity allowed to do?* The strong pattern is to derive a
**non-privileged service identity from the authenticated certificate DN** with a
CHLAUTH `SSLPEERMAP` rule, put a **deny-all back-stop** behind it so nothing
unmapped gets in, and grant each identity **only the `setmqaut` authorities its
role needs** — retiring the historical `MCAUSER('mqm')` / `CHLAUTH(DISABLED)`
posture where every channel runs privileged.

It is for anyone moving IBM MQ off blanket-privileged channels onto
least-privilege, certificate-mapped identities — especially in an HA/DR estate,
where the OS accounts behind those identities must be consistent across every
node a queue manager can run on.

## 2. Scope & version floor

In scope, pinned to **MQ 9.4 Multiplatforms** with the default OS-based Object
Authority Manager (OAM):

- CHLAUTH `SSLPEERMAP` identity mapping (`USERSRC(MAP)` + `MCAUSER`) and the
  deny-all `ADDRESSMAP` back-stop, with rule ordering.
- Least-privilege `setmqaut` grants, scoped per role and attached to **groups**.
- **Message-context authority** for a receiving message channel agent (MCA):
  `PUTAUT(DEF)` and the `+setall` requirement on **every** destination the MCA
  opens — including the dead-letter queue.
- The `CONNAUTH` posture when the certificate is the credential.

Out of scope: TLS ciphers and certificate provisioning (the authentication layer
beneath this); LDAP or `UserExternal` authorization models (summarized as an
alternative in Appendix B); z/OS RACF specifics (noted only where they differ);
lab-specific values.

## 3. Recommendation

**Headline — key the identity off the authenticated certificate, never off a
string the client asserts, and never off a blanket privileged account.** CHLAUTH
itself authenticates nothing; it is firewall-style mapping over an identity that
TLS has already proven. So the ranking below is about *which* proven identity a
channel runs as.

Ranked strongest → weakest:

1. **`SSLPEERMAP` → a fixed, non-privileged `MCAUSER` (recommended).**
   `SET CHLAUTH(<channel>) TYPE(SSLPEERMAP) SSLPEER('<partner DN>') USERSRC(MAP)
   MCAUSER('<svc-acct>')`. The identity is keyed off the certificate's
   authenticated Distinguished Name; the client's self-asserted OS username never
   enters the authorization decision. *Compromise:* you must provision and
   maintain one low-privilege service account (and its group) per partner role.
   This cost is the point — it is what makes each identity independently
   grantable.
2. **A fixed channel-definition `MCAUSER`.** Overrides the asserted ID, but it is
   per-*channel*, not per-*partner*, and it is not tied to the authenticated DN —
   any client that reaches the channel runs as it. *Compromise:* coarser
   granularity and no cryptographic binding between identity and certificate.
3. **Blank `MCAUSER` / the asserted client ID (the dangerous default).** The
   channel runs as whatever username the client flows — trivially spoofable.
   *Compromise:* no real access control at all.
4. **`MCAUSER('mqm')` or `CHLAUTH(DISABLED)` (retire).** Every channel runs
   fully privileged and unauthenticated. This is the posture this guide exists to
   replace.

Behind whichever mapping you choose, add the **deny-all back-stop first** so that
any identity not matched by a map is refused (section 4, step 2). Two further
decisions — the dead-letter-queue grant scope, and whether to keep `CONNAUTH` —
are ranked with their evidence in [Appendix B](#appendix-b-alternatives-and-tradeoffs).

## 4. How to configure it

The order matters: the deny-all back-stop is laid **before** the maps, so there
is never a window in which an unmapped identity is admitted, and so
"more-specific rule wins" leaves the maps as the only way in.

1. **Enable CHLAUTH and name the queue manager's dead-letter queue.** CHLAUTH
   must be on for any rule to take effect; the DLQ is a queue-manager attribute
   (there is exactly one per queue manager — see Appendix B).

   ```mqsc
   ALTER QMGR CHLAUTH(ENABLED)
   ALTER QMGR DEADQ('SYSTEM.DEAD.LETTER.QUEUE')
   ```

2. **Lay the deny-all back-stop first.** A wildcard `ADDRESSMAP` with
   `USERSRC(NOACCESS)` refuses every connection that no more-specific rule
   matches. `ACTION(REPLACE)` keeps the statement idempotent.

   ```mqsc
   SET CHLAUTH('*') TYPE(ADDRESSMAP) ADDRESS('*') USERSRC(NOACCESS) ACTION(REPLACE)
   ```

3. **Map each partner's certificate DN to its service identity.** One
   `SSLPEERMAP` per role. `USERSRC(MAP)` makes `MCAUSER` the authoritative
   identity; the asserted username is discarded. Being more specific than the
   back-stop, these are the only admitted paths.

   ```mqsc
   # Application clients (request/reply over a server-connection channel)
   SET CHLAUTH('APP.SVRCONN') TYPE(SSLPEERMAP) SSLPEER('O=Example,OU=apps') USERSRC(MAP) MCAUSER('appsvc') ACTION(REPLACE)

   # Monitoring clients (read-only)
   SET CHLAUTH('MON.SVRCONN') TYPE(SSLPEERMAP) SSLPEER('O=Example,OU=ops') USERSRC(MAP) MCAUSER('monsvc') ACTION(REPLACE)

   # A partner queue manager's receiver channel
   SET CHLAUTH('PARTNER.TO.ME') TYPE(SSLPEERMAP) SSLPEER('O=Example,OU=gateway') USERSRC(MAP) MCAUSER('mcasvc') ACTION(REPLACE)
   ```

   Where subject DNs are not unique across partners, pin the issuer as well with
   `SSLCERTI('<issuer DN>')`.

4. **Provision the OS service accounts on every node the queue manager can run
   on.** Under OS-based OAM each `MCAUSER` must be a real, resolvable OS principal
   with a primary group, or `setmqaut` rejects it (`AMQ7026E`). Grants attach to
   the group. Because CHLAUTH rules and `setmqaut` authorities are
   queue-manager objects — replicated across an RDQM or Native HA group like any
   other configuration — but `/etc/passwd` is **not** replicated, the accounts
   and their groups must exist **identically on every node** a queue manager can
   fail over to. A missing account on a standby node means authorization silently
   breaks after failover. Keep each service-account name **≤ 12 characters** (an
   adopted authorization identity is truncated at 12).

5. **Grant least privilege per role, to the group.** Each identity gets only the
   authorities its role needs, on only the objects it touches. Grant to the group
   (`-g`), never the principal.

   ```mqsc
   # Application identity: connect + inquire; put to its request queue; get its replies
   setmqaut -m <QMGR> -t qmgr                        -g appsvc +connect +inq
   setmqaut -m <QMGR> -t queue -n APP.REQUEST        -g appsvc +put
   setmqaut -m <QMGR> -t queue -n APP.REPLY          -g appsvc +get +inq +browse

   # Monitoring identity: read-only — no put, no get
   setmqaut -m <QMGR> -t qmgr                        -g monsvc +connect +inq
   setmqaut -m <QMGR> -t queue -n APP.REQUEST        -g monsvc +dsp +inq
   setmqaut -m <QMGR> -t queue -n APP.REPLY          -g monsvc +dsp +inq
   setmqaut -m <QMGR> -t topic -n <RESOURCE.TOPIC>   -g monsvc +sub
   ```

   For the monitoring subscription, scope the topic object to the exact
   resource-monitoring point the collector needs (for example a `$SYS/MQ`
   subtree). Do **not** grant `+sub` on `SYSTEM.BASE.TOPIC`: that is the root of
   the topic tree and subscribes to everything — an over-grant against
   least-privilege.

6. **Give a receiving MCA its message-context authority — including on the
   DLQ.** This is the load-bearing subtlety. A receiver channel with
   `PUTAUT(DEF)` opens **every** destination it writes with
   `MQOO_SET_ALL_CONTEXT`, to reinstate the original message context. That open is
   authority-checked against the MCA's identity, so the identity needs `+setall`
   on the queue manager object **and** on each destination it opens — the reply
   queue **and the dead-letter queue**. Miss the DLQ and an undeliverable message
   can be neither delivered nor dead-lettered, and the channel stalls (see
   Appendix D).

   ```mqsc
   # Receiver MCA identity: context authority on QM + every destination it opens
   setmqaut -m <QMGR> -t qmgr                             -g mcasvc +connect +setall
   setmqaut -m <QMGR> -t queue -n APP.REPLY               -g mcasvc +put +setall
   setmqaut -m <QMGR> -t queue -n SYSTEM.DEAD.LETTER.QUEUE -g mcasvc +put +setall
   ```

   Set `PUTAUT(DEF)` and `USEDLQ(YES)` on the receiver channel definition so
   undeliverable messages route to that DLQ.

7. **Refresh security** so the connection-authority and mapping changes take
   effect without a queue-manager restart.

   ```mqsc
   REFRESH SECURITY TYPE(CONNAUTH)
   ```

## 5. Verify it worked

- **Unmapped identities are refused.** A connection whose certificate DN matches
  no `SSLPEERMAP` should be rejected by the back-stop — the channel ends and an
  authority event / error-log entry records the refusal. Confirm a known-good
  partner still connects and runs as the mapped `MCAUSER`, not as `mqm` or the
  asserted username.
- **Each identity holds exactly its grants.** Inspect the authority records for
  each service account's group and confirm they match the role table
  ([Appendix A](#a1-least-privilege-grants-per-role)) — no extra `+put`/`+get` on
  the monitoring identity, no privileged authorities anywhere.
- **The receiver can dead-letter.** Confirm the receiver identity holds `+put`
  **and** `+setall` on both the reply queue and the DLQ. The real test is
  behavioural: an induced undeliverable reply should land in the DLQ (wrapped in
  an `MQDLH`) and the channel should keep running — not stall in
  `STATUS(PAUSED) SUBSTATE(MQPUT)`.
- **No spurious authorization failures.** Watch the authority-event stream (see
  the event-monitoring guide): legitimate flows should produce **no** `MQRC 2035`
  events. A 2035 at a set-all-context open is the signature of a missing
  `+setall` (Appendix D).

## 6. What stays / caveats

- **CHLAUTH authenticates nothing.** It maps and filters an identity that TLS has
  already proven. Certificate mapping is only as strong as the TLS mutual
  authentication beneath it (`SSLCAUTH(ENABLED)` + `SSLPEER`).
- **Only a receiver MCA needs `+setall`.** Context security is **not supported on
  server-connection channels**, so the application and monitoring identities
  (which arrive over SVRCONN) never take a `+setall` grant — only the QM-to-QM
  receiver MCA does. Granting `+setall` to a SVRCONN identity is both useless and
  an over-grant.
- **The DLQ is not special in the authority model.** It is checked exactly like
  any other destination the MCA opens with set-all context. The `+setall`-on-DLQ
  requirement is invisible until an undeliverable message actually needs
  dead-lettering — the classic trap.
- **OS accounts do not replicate; MQ objects do.** The mapped service accounts
  and their groups must be present on every failover node. This is substrate
  consistency the queue manager cannot enforce for you.
- **12-character identities.** An identity adopted for authorization is truncated
  to 12 characters. Keep `MCAUSER` service-account names within that.
- **`MCAUSER('mqm')` and `CHLAUTH(DISABLED)` are retired,** not merely
  discouraged. Any channel left on either undoes the whole model.

---

## Appendix A: Authority reference

### A.1 Least-privilege grants per role

Illustrative grants for three common roles. All grants attach to the identity's
**group**; the OS principal is a member of it.

| Role | Object | Authorities | Why |
|---|---|---|---|
| Application (`appsvc`) | qmgr | `+connect +inq` | connect and inquire only |
| | queue `APP.REQUEST` | `+put` | submit requests |
| | queue `APP.REPLY` | `+get +inq +browse` | collect its replies |
| Monitoring (`monsvc`) | qmgr | `+connect +inq` | connect and inquire only |
| | queue `APP.REQUEST` / `APP.REPLY` | `+dsp +inq` | read metadata (depth, attributes) — no message access |
| | topic `<RESOURCE.TOPIC>` | `+sub` | subscribe to the scoped resource-monitoring topic |
| Receiver MCA (`mcasvc`) | qmgr | `+connect +setall` | connect + context authority (QM half) |
| | queue `APP.REPLY` | `+put +setall` | deliver replies preserving context |
| | queue `SYSTEM.DEAD.LETTER.QUEUE` | `+put +setall` | dead-letter the undeliverable, preserving context |

### A.2 CHLAUTH `USERSRC` values

| `USERSRC` | Effect |
|---|---|
| `MAP` (default) | Run as the rule's `MCAUSER` (mandatory with `MAP`). The identity is asserted by the rule, not the client. |
| `CHANNEL` | Run as the channel-defined / flowed ID. Weak — no mapping. |
| `NOACCESS` | Block. The channel ends immediately. Used for the back-stop. |

### A.3 `PUTAUT` and message-context authority

`PUTAUT` is valid on `RCVR`, `RQSTR`, and `CLUSRCVR` channels (Multiplatforms).
Both settings open the destination with `MQOO_SET_ALL_CONTEXT`, so both require
`+setall`; they differ only in the alternate-user machinery and in which identity
the *put* is checked against.

| Check at inbound `MQPUT` | `PUTAUT(DEF)` | `PUTAUT(CTX)` |
|---|---|---|
| set-all-context open → `+setall` on queue | MCA identity | MCA identity |
| `+setall` also on **qmgr object** | MCA identity | MCA identity |
| alternate-user open → `+altusr` | not opened | MCA identity |
| the put itself → `+put` | MCA identity | the stamped `MQMD.UserIdentifier` |

The rule to remember: **set-all/set-id authority must be granted on both the
queue object and the queue-manager object.** `PUTAUT(DEF)` does *not* avoid
`+setall` — it only avoids the alternate-user check. The requirement is invisible
under a privileged blank `MCAUSER` (the `mqm`-owned listener already holds
`+setall`), which is exactly why hardening to a low-privilege `MCAUSER` surfaces
it.

## Appendix B: Alternatives and tradeoffs

### B.1 Identity mapping — evidence for the section 3 ranking

`SSLPEERMAP` → fixed `MCAUSER` is the top recommendation because identity is keyed
off the **authenticated** certificate DN, not a spoofable asserted string. In the
community "strength of factor" view, CHLAUTH, a channel `MCAUSER`, and an asserted
client ID are all rated *weak — no authentication*; only TLS mutual auth
(possession of the certificate and its secret) is *strong*. Mapping lets the
strong factor (the cert) select the identity, and `USERSRC(NOACCESS)` — not a
"junk" `MCAUSER` — is the correct default-deny primitive.

### B.2 Dead-letter queue scope — QM-wide vs per-counterparty

There is **no per-channel or per-remote-queue-manager DLQ in MQ 9.4.** `DEADQ` is
a queue-manager attribute (one DLQ per queue manager); `USEDLQ(YES|NO)` is only a
per-channel boolean over that single queue — it controls *whether* a channel
dead-letters, never *which* queue.

- **QM-wide DLQ (recommended, and in fact the only native option).** One DLQ, one
  `+put +setall` grant on it. *Compromise:* the grant's blast radius is a queue
  every channel can touch — it cannot be scoped narrower because no per-channel
  DLQ exists. This is not an interim shortcut; it is the architecturally required
  destination under `USEDLQ(YES)`.
- **Per-counterparty routing.** Achievable only **downstream** of the DLQ, with a
  `runmqdlq` rules table forwarding by `DESTQ` / `DESTQM` / `REASON`.
  *Compromise:* a continuously-run batch utility, a maintained rules table,
  re-put/forward semantics with delivery-ordering effects — and the MCA still
  needs `+put +setall` on the QM DLQ regardless. There is no reliable
  "source channel" key to route on (`MQDLH.PutApplName` carries the queue-manager
  name, not the channel), so per-channel segregation is not dependable anyway.

### B.3 `CONNAUTH` posture when the certificate is the credential

Once mutual TLS proves the client and `SSLPEERMAP` selects the identity, the
certificate *is* the credential — so `CONNAUTH` (connection-level user/password)
becomes a **second, independent factor** rather than the primary one.

- **Keep `CONNAUTH` (recommended default).** Retaining a user/password check
  (`CHCKCLNT(REQDADM)` at minimum) is defense-in-depth: an authenticated secret in
  addition to the certificate, and the check that stops a privileged ID
  connecting without a password. *Compromise:* a credential store to operate and
  rotate.
- **Disable `CONNAUTH` (`CONNAUTH(' ')`).** Defensible when the mutually
  authenticated certificate is genuinely the single source of identity truth: it
  removes a password store and simplifies operation. *Compromise:* you give up the
  second factor and the privileged-ID password gate entirely — acceptable only if
  certificate issuance and the deny-all back-stop are truly trusted to carry the
  whole authentication burden. Prefer keeping `CONNAUTH` unless that condition
  clearly holds.

### B.4 OS-based OAM vs external authorization

The default OAM authorizes by **OS group**, which is why every `MCAUSER` must be a
real OS principal on every node. Two escape hatches exist if maintaining OS
accounts across a fleet is undesirable: **LDAP authorization** (`idpwldap` with
LDAP performing authorization, not just authentication — the resolved short name
need not be an OS user but must be unique) and **`SecurityPolicy=UserExternal`**
(MQ 9.2.1+). Both trade the "accounts on every node" burden for an external
directory dependency. LDAP used for *authentication only*, with OAM still doing
authorization, does **not** relax the OS-account requirement.

## Appendix C: Complete configuration examples

**CHLAUTH — back-stop first, then the maps (idempotent):**

```mqsc
ALTER QMGR CHLAUTH(ENABLED)
ALTER QMGR DEADQ('SYSTEM.DEAD.LETTER.QUEUE')

# Deny-all back-stop — laid before any map
SET CHLAUTH('*') TYPE(ADDRESSMAP) ADDRESS('*') USERSRC(NOACCESS) ACTION(REPLACE)

# Per-role SSLPEERMAP identity maps (more specific than the back-stop)
SET CHLAUTH('APP.SVRCONN')   TYPE(SSLPEERMAP) SSLPEER('O=Example,OU=apps')    USERSRC(MAP) MCAUSER('appsvc') ACTION(REPLACE)
SET CHLAUTH('MON.SVRCONN')   TYPE(SSLPEERMAP) SSLPEER('O=Example,OU=ops')     USERSRC(MAP) MCAUSER('monsvc') ACTION(REPLACE)
SET CHLAUTH('PARTNER.TO.ME') TYPE(SSLPEERMAP) SSLPEER('O=Example,OU=gateway') USERSRC(MAP) MCAUSER('mcasvc') ACTION(REPLACE)

REFRESH SECURITY TYPE(CONNAUTH)
```

**`setmqaut` — least privilege per role (grants to groups):**

```mqsc
# Application identity
setmqaut -m <QMGR> -t qmgr                              -g appsvc +connect +inq
setmqaut -m <QMGR> -t queue -n APP.REQUEST              -g appsvc +put
setmqaut -m <QMGR> -t queue -n APP.REPLY                -g appsvc +get +inq +browse

# Monitoring identity (read-only)
setmqaut -m <QMGR> -t qmgr                              -g monsvc +connect +inq
setmqaut -m <QMGR> -t queue -n APP.REQUEST              -g monsvc +dsp +inq
setmqaut -m <QMGR> -t queue -n APP.REPLY                -g monsvc +dsp +inq
setmqaut -m <QMGR> -t topic -n <RESOURCE.TOPIC>         -g monsvc +sub

# Receiver MCA identity (context authority on QM + every destination)
setmqaut -m <QMGR> -t qmgr                              -g mcasvc +connect +setall
setmqaut -m <QMGR> -t queue -n APP.REPLY                -g mcasvc +put +setall
setmqaut -m <QMGR> -t queue -n SYSTEM.DEAD.LETTER.QUEUE -g mcasvc +put +setall
```

## Appendix D: Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Receiver channel wedged in `STATUS(PAUSED) SUBSTATE(MQPUT)`; log shows `AMQ9599E … reason code 2035` then `AMQ9511E` on the DLQ open | MCA identity has `+put` but **not** `+setall` on the dead-letter queue — the set-all-context open is refused, so the message can be neither delivered nor dead-lettered | grant `+setall` on the DLQ (and confirm `+setall` on the qmgr object and the reply queue) |
| Legitimate replies never reach the reply queue; `MQRC 2035` at inbound put | missing `+setall` on the reply queue or the qmgr object under `PUTAUT(DEF)` | grant `+put +setall` on the queue and `+setall` on the qmgr object |
| A known-good partner is refused / channel ends immediately | its certificate DN matches no `SSLPEERMAP`, so the deny-all back-stop catches it | add or correct the `SSLPEERMAP` (check the exact DN order and `SSLCERTI` if subjects collide) |
| `setmqaut` returns `AMQ7026E` (principal or group unknown) | the `MCAUSER` is not a real OS principal with a primary group on this node | create the account and group on **every** node the queue manager can run on |
| Authorization works on the active node but breaks after failover | the service account/group is missing on the standby node (MQ objects replicate, OS accounts do not) | provision the accounts identically across the whole HA/DR group |
| Monitoring identity can read message payloads it should not | `+get`/`+browse` or `+sub` on `SYSTEM.BASE.TOPIC` over-granted | reduce to `+dsp +inq`; scope the topic subscription to the specific resource-monitoring point |

## Appendix E: References

IBM MQ 9.4 documentation:

- *SET CHLAUTH* and *Channel authentication records* — the `SSLPEERMAP` /
  `ADDRESSMAP` rule types, `USERSRC`, and rule precedence.
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=commands-set-chlauth>
- *setmqaut (grant or revoke authority)* — authority keywords and the `-g` group
  form. <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-setmqaut-grant-revoke-authority>
- *PUTAUT (channel attribute)* and *Context authority* — the `DEF`/`CTX` open
  behaviour and the "queue **and** queue-manager object" set-all-context rule.
  Search these topic titles at <https://www.ibm.com/docs/en/ibm-mq/9.4>.
- *Using a dead-letter queue* — `DEADQ` (queue-manager) vs `USEDLQ` (per-channel
  boolean), and `runmqdlq` rules-table handling.
- *Connection authentication (CONNAUTH)* — `CHCKCLNT`, `ADOPTCTX`, and disabling
  the check. Search at <https://www.ibm.com/docs/en/ibm-mq/9.4>.
