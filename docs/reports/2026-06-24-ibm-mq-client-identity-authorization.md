# IBM MQ client identity & authorization — research report

> **Issue:** #347 · **Date:** 2026-06-24 · **Status:** Research captured —
> informs a vendor conversation and our own queue-manager configuration.
> **Scope:** Identity resolution and authorization for IBM MQ **client
> (SVRCONN)** and **queue-manager-to-queue-manager** channels, on **distributed
> (UNIX/Linux/Windows)** and **z/OS** platforms. Message payload security
> (MQ Advanced AMS) and cipher selection are out of scope except where they bear
> on identity.

---

## 0. How to read this report

Every claim is labelled **[data]** (what a source actually says, with a citation)
vs **[judgment]** (reasoning on top of those facts). The separation is
deliberate: MQ security folklore is full of confidently-stated "precedence rules"
that turn out to be version-dependent or wrong, and the reader should be able to
tell exactly where documented behavior stops and inference begins. Two
commonly-repeated claims were **explicitly refuted** during this research and are
called out in §10.

**Provenance.** The factual claims below come from a deep-research pass over IBM
documentation and named MQ practitioner sources (Morag Hughson / IBM and MQGem,
T-Rob Wyatt, Mark Taylor), in which 25 candidate claims were adversarially
verified (3 independent skeptics per claim; 2/3 refutes kills a claim). 23 were
confirmed, 2 were refuted. Where a load-bearing point was *not* covered by the
verified pool, it is flagged as such rather than presented as settled fact.

---

## 1. The originating question

A client application connects to a third-party vendor's IBM MQ queue manager over
IP. The vendor will provide a CCDT (Client Channel Definition Table), a keystore,
and a TLS certificate — but has also asked for **the OS user ID that our client
process runs under**. That request was surprising: the CCDT, keystore, and cert
are just files we need read access to. Why would our *username* matter?

**[judgment]** The instinct that "the files don't care about the username" is
correct — our local read access to those files is unrelated to our username. But
the request is not about the files. It is about the identity MQ uses for
**authorization** on the server side, and that exposes a decades-old trust model
that sits uncomfortably next to modern TLS requirements. The rest of this report
explains what the vendor is really asking for, whether it should matter, and how
to configure the same thing securely on our own side.

## 2. The one mental model that makes MQ security legible

There are **two separate identity questions**, and conflating them is the single
biggest source of confusion in MQ security:

| | "What identity does the channel **run as**?" | "What identity is **stamped in the message**?" |
|---|---|---|
| Field | **MCAUSER** | **MQMD.UserIdentifier** |
| Set by | channel definition / CHLAUTH / asserted client ID | the putting application (message context) |
| Used for | connect + put/get **OAM authorization** | audit, and *optionally* put authority via `PUTAUT(CTX)` |
| Governed by | CHLAUTH, `USERSRC` | `PUTAUT`, context authority (`+setid`/`+setall`) |

Everything in this report is one of these two columns. **The vendor's username
request is entirely about column 1** — the MCAUSER the channel runs as. Column 2
(§5) only becomes relevant when *we* build queue-manager-to-queue-manager
channels.

## 3. Default behavior and the security critique

**[data]** By default an MQ client flows the **OS user ID of the client process**
to the queue manager as an **unauthenticated, self-asserted, trivially spoofable
string**, and historically that string drove OAM authorization. T-Rob Wyatt
(MQTC 2013), verbatim:

> "The WMQ client code uses the ID of the client process. But it is trivial for
> the client to present an administrative ID! If the ID presented is trusted, the
> remote partner can administer the queue manager (including remote code
> execution)… The authentication must always occur at the queue manager."

**[data]** The community's own ratings substantiate the critique that trusting an
unauthenticated username while mandating modern TLS is inconsistent. The MQTC 2018
"Holistic Approach" deck rates the mechanisms:

- **CHLAUTH** — *"Not authentication at all, more like firewall rules… No
  validation performed."* Rated **"Weak; no authentication."**
- **Channel MCAUSER** and the **asserted application user ID** — also **"Weak; no
  authentication."**
- **TLS/SSL** — **"Strong; possession of object (certificate) & secret."**
- **Connection Authentication (password)** — also **"Strong; possession of
  secret."**

**[judgment]** So the critique lands: CHLAUTH authenticates *nothing* — it is
firewall-style mapping/filtering. The only **channel-definition** mechanism rated
as strong authentication is **TLS mutual auth** with `SSLCAUTH(ENABLED)` plus an
`SSLPEER` DN restriction. (Honest nuance: Connection Authentication / password is
*also* rated Strong, so TLS is not the only strong factor overall — it is the only
strong *channel-attribute* factor.) The correct framing is subtle: the asserted
string being unauthenticated is not itself the flaw — *trusting it for
authorization* is. The fix is not to authenticate the string; it is to **discard
it and substitute a server-controlled identity** derived from something that was
authenticated (the TLS certificate). See §4.

### 3.1 How the defaults improved (so we don't overstate the critique)

**[data]** MQ V8 (2014) shipped default out-of-the-box CHLAUTH rules that blunt
the worst case:

1. `CHLAUTH(*) TYPE(BLOCKUSER) USERLIST(*MQADMIN)` — bans any inbound channel from
   asserting a **privileged** ID (and catches blank/unresolvable IDs, which on
   Multiplatforms are treated as privileged). The privileged-ID ban dates to
   **V7.1**.
2. `CHLAUTH(SYSTEM.*) TYPE(ADDRESSMAP) ADDRESS(*) USERSRC(NOACCESS)` — disables all
   `SYSTEM.*` channels.
3. `CHLAUTH(SYSTEM.ADMIN.SVRCONN) … USERSRC(CHANNEL)` — re-allows just the admin
   channel (more-specific rule wins).

**[data]** V8 also introduced **Connection Authentication (CONNAUTH)** — a real
username+password check against the local OS (`IDPWOS`) or LDAP (`IDPWLDAP`,
distributed only, **not** z/OS), enforced via `CHCKCLNT`
(`NONE`/`OPTIONAL`/`REQUIRED`/`REQDADM`). A fresh V8 queue manager defaults to
`SYSTEM.DEFAULT.AUTHINFO.IDPWOS` with `CHCKCLNT(REQDADM)` — privileged IDs must
supply a valid password.

**[data]** The `AUTHINFO` object's **`ADOPTCTX`** attribute decides whether the
*password-authenticated* user (`YES`) or the *merely-asserted running* user
(`NO`) is carried forward for authorization. Default was **`NO` in V8, changed to
`YES` in V9.0.4**. CHLAUTH can only alter an adopted context when
`ChlauthEarlyAdopt=Y`.

**[judgment]** Accurate statement to the vendor: the default posture was
indefensible pre-V8 and is merely *"not wide open"* post-V8 — but "not wide open"
still is not "authenticated." The asserted ID is trusted unless actively replaced.
Secure-by-default ciphers, trust-by-default identity.

## 4. How a channel resolves its MCAUSER (the running identity)

**[data]** Resolution mechanisms:

- **Blank MCAUSER** → use the client-flowed/asserted ID (the dangerous default).
- **Fixed MCAUSER on the channel definition** → overrides the asserted ID.
- **CHLAUTH rule that sets MCAUSER** → overrides the channel-definition MCAUSER.

**[data]** CHLAUTH rule **types**: `SSLPEERMAP` (TLS cert DN, optionally pinned to
an issuer via `SSLCERTI`), `USERMAP` (asserted client user via `CLNTUSER`),
`ADDRESSMAP` (IP), `QMGRMAP` (remote QM name), plus `BLOCKUSER` and `BLOCKADDR`.
`USERSRC` values: **`NOACCESS`** (block — channel ends immediately), **`CHANNEL`**
(use the flowed/channel-defined ID), **`MAP`** (assign the MCAUSER; this is the
default, and MCAUSER is mandatory with it).

**[data]** Morag Hughson (IBM, the CHLAUTH author): *"Putting an MCAUSER in a
CHLAUTH record will override what is in the channel definition's MCAUSER"* and
*"CHLAUTH rules which set the MCAUSER over-ride the adopted user."*

### 4.1 The recommended pattern (the answer to the whole problem)

**[data]** Map each partner's **authenticated TLS certificate DN** to a
**dedicated, low-privilege, fixed service-account MCAUSER** via
`TYPE(SSLPEERMAP) USERSRC(MAP)`. The client's self-asserted OS username is
**discarded entirely** — it never enters the authorization decision. Hughson's V8
deck, verbatim:

> "We start off with a rule that blocks everyone… Our Business Partners must all
> connect using SSL, so we will map their access from the certificate DNs…
> Previously you might have done this by having separate channel definitions for
> each BP, now they can come into the same receiver definition."

**[judgment]** This is the crux. If the vendor follows this pattern, *our identity
to their queue manager is our certificate DN, not our OS username* — and the
username request is either habit or a cosmetic per-partner naming convenience, not
a security necessity.

## 5. The override / message-context layer (column 2)

**[data — MCAUSER half] / [judgment — PUTAUT half, see flag]**

- A fixed channel MCAUSER overrides any user identity and is what the channel
  **runs as** for connect/put/get OAM checks. *(Strongly verified.)*
- **`MQMD.UserIdentifier`** is the identity **stamped in the message** — a
  separate thing, set by the putting application as message context.
- On receiver/requester (QM-to-QM) channels, **`PUTAUT(DEF)`** = the put-authority
  check uses the **MCA (channel) user**; **`PUTAUT(CTX)`** = the check
  *additionally* uses the **user ID in the message context**, which requires the
  MCA user to hold **context authority** (`+setall`/`+setid`) on the target queue.

> **Verification gap (medium confidence).** The `PUTAUT(DEF)`/`PUTAUT(CTX)` and
> `+setid`/`+setall` mechanics were **not** among the 23 adversarially-verified
> claims — IBM's PUTAUT documentation pages returned HTTP 403 during the research
> pass (a recurring access pattern in this lab; see the 403-source-retrieval
> notes). This paragraph reflects the general MQ documentation model and the
> author's understanding, but should be verified against live IBM docs before
> being relied on for a production QM-to-QM design. For the **client SVRCONN**
> scenario that prompted this report, this layer largely does not apply — PUTAUT
> is a receiver/requester channel attribute.

## 6. The heart of it: must the resolved ID exist as a real account on the host?

This adjudicates the scalability/collision concern (§7). Two-part answer.

### 6.1 The external client's own username never needs to exist on the server

**[data]** IBM Security Notes #4 (Hughson): *"Using CHLAUTH with a mapping to an
existing user, to avoid the need to create many users in the server of the queue
manager."* Because the asserted username is discarded and replaced by a
server-controlled MCAUSER, an arbitrary corporate username (`k128`, etc.) is
irrelevant to the vendor's host — *provided* they map from the cert DN.

### 6.2 The mapped-to MCAUSER service account itself — platform-dependent

- **[data] UNIX/Linux, default (OS-based) authorization:** YES, it must exist. The
  OAM authorizes by **GROUP**, and `setmqaut -p <user>` applies authority to the
  principal's **primary OS group** — so the MCAUSER must be a real, resolvable OS
  principal with a primary group, or `setmqaut` returns `AMQ7026E`. From **MQ
  8.0** the `SecurityPolicy` attribute can switch to user-based authorization
  (default remains group-based).
- **[data] UNIX/Linux with LDAP authorization** (`idpwldap` + LDAP *authorization*,
  not merely authentication): NO. Mark Taylor (former IBM MQ development lead):
  *"Using LDAP explicitly means that users do not actually need to be defined or
  available on the operating system where MQ is running… The userid on the
  operating system can now be irrelevant."* The `SHORTUSR` *"does not need to
  represent an operating system user ID, but must be a unique string."* **Caveat:**
  this holds only when LDAP does *authorization* too; LDAP-auth-only with OS
  authorization still requires the resolved short name to exist as an OS account.
- **[data] Windows:** grants apply to principals directly.

### 6.3 z/OS — a completely different machine

**[data]** If the far end is a mainframe, there is **no `/etc/passwd`**. Security is
delegated to **RACF/SAF** (the External Security Manager) via class profiles:
`MQCONN` (connection), `MQQUEUE`/`MXQUEUE` (queues), `MQCMDS` (commands),
`MQADMIN`/`MXADMIN` (administration: security switches, RESLEVEL, alternate-user /
context), `MQPROC`, `MQNLIST`, `MXTOPIC`. Access is `PERMIT`ed to **RACF userids
and groups**. Every resolved identity — **including TLS certificate identities** —
must be a valid **RACF userid**: the cert is bound to a userid via
`RACDCERT ID(userid)` or a Certificate Name Filter; if no userid is associated
with a presented cert, the channel falls back to the userid the channel initiator
(CHIN) runs under.

**[judgment]** So on z/OS, "your username has to exist" means *a RACF userid plus
PERMITs must exist*, not an OS account. (Minor verifier nit: the loose claim
"MQADMIN covers all checks" was downgraded — queue/connection/command checks live
in their own classes; MQADMIN holds admin-type profiles.)

### 6.4 The 12-character constraint

**[data]** MQCSP supports user IDs/passwords **longer** than 12 characters *for
authentication*, but if `ADOPTCTX(YES)` adopts that user *for authorization*, the
ID is **truncated to 12 characters**. **[judgment]** A real constraint when naming
service accounts: keep MCAUSER service-account names ≤12 characters or they
collide/fail after truncation. This is also the most likely *practical* reason a
vendor wants the username up front — to provision the exact ≤12-char string.

## 7. The scalability / collision argument

**[judgment]** The concern — "if each client picks its own asserted username and
that drives authorization, then every client's usernames must be unique and
provisioned on the vendor's host, which doesn't scale and invites collisions" — is
**correct for the naive design**, and that naive design is exactly what the
recommended architecture eliminates.

**[data]** Under the recommended pattern (per-partner TLS-cert-DN → fixed
service-account MCAUSER mapping, one low-privilege service account per partner,
OAM authorities granted to that account or its private group), the external
client's own username is thrown away at the door. Only the **small set of mapped
service accounts** must be provisioned — not every client's chosen username. IBM's
own phrasing for the goal is *"to avoid the need to create many users in the
server of the queue manager."*

**[judgment]** Therefore the collision/uniqueness objection dissolves under a
correctly-built queue manager. If a vendor's design genuinely *requires* our
specific username to be globally unique on their host, that is a signal they are
trusting the asserted ID rather than mapping the certificate — the weaker posture.

## 8. Practical guidance

### 8.1 Questions to put to the vendor

1. **"For our inbound connection, are you using `SSLPEERMAP` to map our TLS
   certificate DN to a fixed MCAUSER on your side — or are you trusting the user
   ID our client asserts?"**
   - **SSLPEERMAP → fixed MCAUSER:** then why is our OS username needed at all? Our
     identity to their QM is the **certificate DN**. This question exposes whether
     the request is load-bearing or habit.
   - **Trusting the asserted ID** (or keying a `USERMAP`/grant to it): the username
     *is* load-bearing — it must be **≤12 chars**, exact, case-coordinated, and
     jointly pinned. Worth (politely) noting this is the weak-rated path and asking
     whether cert-DN mapping is an option.
2. **"Does our username need to *exist* as an account on your queue manager host,
   or are you only using it as a CHLAUTH map key / `setmqaut` grant target?"** —
   distinguishes "we provisioned an account for you" from "we keyed a rule to your
   string."
3. **"Is your queue manager z/OS or distributed?"** — determines whether "our
   identity" means a RACF userid or an OS/LDAP principal, and whether LDAP keeps it
   off the host entirely.
4. **"Are you enforcing `CHCKCLNT(REQUIRED)` / Connection Authentication on top of
   TLS?"** — defense in depth.

**[judgment]** Read: since they are already issuing a certificate, a well-built QM
would key authorization off the cert DN, making our OS username irrelevant. Asking
for it suggests either (charitably) they pre-provision a per-partner service
account and want a human-recognizable name, or (less charitably) they key
authorization off the asserted ID out of habit. Question 1 settles which.

### 8.2 Designing our own queue manager for inbound third-party connections

**[data]** Deny-all-then-whitelist:

1. **Dedicated SVRCONN per partner** (or a shared one — the DN mapping
   discriminates them; both are supported).
2. **TLS mutual auth**: `SSLCAUTH(ENABLED)`, strong `SSLCIPH`, and `SSLPEER` /
   `SSLPEERMAP` pinned to the partner's DN (use `SSLCERTI` to pin the issuer when
   subject DNs are not unique).
3. **Back-stop deny rule first:**
   `SET CHLAUTH('*') TYPE(ADDRESSMAP) ADDRESS('*') USERSRC(NOACCESS)`
   (and/or a wildcard `SSLPEERMAP SSLPEER('CN=*') USERSRC(NOACCESS)` fallback).
4. **Per-partner map:**
   `SET CHLAUTH(<chl>) TYPE(SSLPEERMAP) SSLPEER('<partner DN>') USERSRC(MAP) MCAUSER(<svc-acct>)`
   — one low-privilege service account per partner, name **≤12 chars**, each in its
   own private group.
5. **Minimal `setmqaut`** grants to that account's group only (connect + the
   specific queues, nothing else). Grant to the **group**, manage via membership.
6. **Platform note:** on **UNIX/Linux** the service account must be a real OS
   principal with a primary group (or use LDAP authorization /
   `SecurityPolicy=UserExternal` to avoid OS accounts). On **z/OS** it is a **RACF
   userid** bound to the cert via `RACDCERT`, `PERMIT`ed to `MQCONN`/`MQQUEUE`
   profiles — no OS account.
7. **Optional CONNAUTH** on top, for an authenticated secret as well as the cert.

**[judgment]** Lab relevance: this is the exact CHLAUTH/`setmqaut` shape the
resiliency-lab queue managers should adopt for any externally-reachable SVRCONN,
and it composes cleanly with the HA/DR arms — the rules are queue-manager
configuration, replicated like any other object across an RDQM or Native-HA group.

## 9. Bottom line

**[judgment]**

- The username request is about the **MCAUSER the channel runs as**, not the
  files. The original instinct ("the files don't care about my username") is
  correct; the username matters only because MQ's *default* authorization trusts a
  self-asserted, unauthenticated string.
- The security critique (unauthenticated identity alongside mandated strong
  ciphers) is **valid and named in the MQ community's own materials**. MQ 8
  hardened the defaults but did not change the fundamental "trusted unless
  replaced" posture.
- The correct architecture **discards** the asserted username and maps the
  authenticated **TLS certificate DN** to a fixed, low-privilege service account.
  Under that design our OS username is irrelevant to the vendor, and the
  scalability/collision concern disappears.
- z/OS vs distributed materially changes what "your identity must exist" means
  (RACF userid + PERMITs vs OS/LDAP principal + group), so confirming the vendor's
  platform is a load-bearing question.

## 10. Refuted claims (do not repeat)

**[data]** Two commonly-repeated claims were refuted 0-3 during verification:

1. A tidy **linear MCAUSER precedence chain**
   (`channel-def < CHLAUTH < ADOPTCTX(YES)`) **plus** an alleged IBM recommendation
   to "set MCAUSER to a junk value as a default-deny." The real interaction is
   version- and `ChlauthEarlyAdopt`-dependent and is *not* a clean linear override.
   Use `USERSRC(NOACCESS)` for default-deny, **not** a junk MCAUSER string.
2. "**Channels ALWAYS run with administrative authority**" — false.

## 11. Open questions / what to verify next

1. **`PUTAUT(DEF)` vs `PUTAUT(CTX)` and context authority (`+setid`/`+setall`):**
   not established by the verified claim pool (§5); needs direct ibm.com/docs
   verification before being relied on in a QM-to-QM design.
2. **Exact MCAUSER precedence** when `ChlauthEarlyAdopt=Y` vs `N` interacts with
   `ADOPTCTX=YES` vs `NO` and a CHLAUTH-set MCAUSER. The clean linear chain was
   refuted; the precise resolution order across these toggles is open.
3. **`SecurityPolicy=UserExternal` (MQ 9.2.1+):** appears to let a mapped MCAUSER
   avoid being a real OS account even under OS-based authorization — exact
   constraints and comparison to the LDAP-authorization path are unconfirmed.
4. **Vendor specifics:** is the vendor doing `SSLPEERMAP` to a fixed MCAUSER (our
   username irrelevant) or trusting/keying off the asserted ID (our username
   load-bearing)? Is their queue manager z/OS or distributed? Only the vendor can
   confirm — this report enables the questions but cannot answer them.

## 12. Sources

- T-Rob Wyatt — *Base Hardening* (MQTC 2013):
  <https://www.mqtechconference.com/sessions_v2013/MQTC2013_Wyatt_Base_Hardening.pdf>
- *MQ Security: A Holistic Approach* (MQTC 2018):
  <https://www.mqtechconference.com/sessions_v2018/MQTC-2018-MQ-Security-A-Holistic-Approach.pdf>
- M. Hughson — *CHLAUTH in V8* (MQTC 2014):
  <https://www.mqtechconference.com/sessions_v2014/CHLAUTH_in_V8.pdf>
- M. Hughson — *IBM MQ Connection Authentication* (MQTC 2014):
  <https://www.mqtechconference.com/sessions_v2014/IBM_MQ_Connection_Authentication.pdf>
- IBM — *SET CHLAUTH reference* (9.3):
  <https://www.ibm.com/docs/en/ibm-mq/9.3.x?topic=reference-set-chlauth-create-modify-channel-authentication-record>
- IBM — *Mapping a client user ID to an MCAUSER* (9.1):
  <https://www.ibm.com/docs/en/ibm-mq/9.1.x?topic=manager-mapping-client-user-id-mcauser-user-id>
- IBM — *MQ Security Notes #4* (CHLAUTH / MCAUSER wildcards / long userids):
  <https://www.ibm.com/support/pages/mq-security-notes-4-remote-access-administrators-back-stop-chlauth-mcauser-wildcards-setmqaut-long-userids-and-long-passwords>
- IBM — *setmqaut reference* (9.2):
  <https://www.ibm.com/docs/en/ibm-mq/9.2.x?topic=reference-setmqaut-grant-revoke-authority>
- Mark Taylor — *Using Active Directory for authorisation in Unix queue managers*:
  <https://marketaylor.synology.me/?p=541>
- MQGem — *The CHLAUTH back-stop rule*:
  <https://mqgem.wordpress.com/2013/03/21/mq_chlauth_the_back_stop_rule/>
- IBM — *MQ Security (z/OS RACF checklist)*:
  <https://www.ibm.com/support/pages/system/files/inline-files/mq_security.pdf>
