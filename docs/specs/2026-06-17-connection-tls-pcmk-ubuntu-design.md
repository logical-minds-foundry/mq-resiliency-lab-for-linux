# Connection TLS — pcmk-ubuntu Arm (Stage 1) — Design

> **Status:** design, first pass — brainstormed & pushback-reviewed 2026-06-17.
> **Date:** 2026-06-17
> **Author:** Phillip Moore (with Claude)
> **Tracking issue:** #250
> **Relationship:** the downstream "apply TLS to all connections" layer the lab
> PKI design deferred ([`2026-06-16-lab-pki-design.md`](2026-06-16-lab-pki-design.md)
> §9; provider built via #210/#222). **Stage 1 of 2** — Stage 2 (internal
> authorization) is #249. Driver: unblock **NativeHA/CRR** (CRR's replication link
> requires TLS).

---

## 1. Why this exists

The lab PKI provider (#210/#222) *produces* per-entity PKCS#12 keystores (two org
CAs, cross-org signer exchange, partial-DN identities, GSKit-friendly encoding).
Nothing *uses* them yet — every connection in the lab is **plaintext**
(`TRPTYPE(TCP)`, no `SSLCIPH`/`SSLKEYR` anywhere), with `CHLAUTH(DISABLED)` and
`MCAUSER('mqm')` (the old "security out of scope" stance).

This spec applies TLS to **every connection of the `pcmk-ubuntu` arm** — the
stepping stone to NativeHA/CRR, which needs the SSL cert-management tooling and
TLS-secured connections (CRR's inter-region replication link is TLS).

### 1.1 The two-stage split (load-bearing scope decision)

Two distinct security concerns; this spec is **only the first**:

- **Stage 1 — secure + authenticate the *connections* (this spec).** Encrypt every
  TCP connection and **cert-authenticate** the peer (mutual TLS): each end presents
  a cert; each end validates the other's cert DN. The certs *assert* an
  authenticated identity to each queue manager.
- **Stage 2 — *authorize* inside the QM (#249, deferred).** *Acting* on those
  identities: map cert DNs to non-privileged MQ users (CHLAUTH `SSLPEERMAP`),
  secure the queues (OAM authority records), manage user IDs + credentials.

**Stage 1 deliberately leaves internal authority wide open** — `MCAUSER('mqm')`,
queues unrestricted. *Authenticated but unauthorized.* That is a conscious lab
stepping stone (it would be indefensible in production), and it is **adequate to
unblock CRR**, which needs TLS on the wire, not internal authorization.

## 2. Scope & non-goals

**In scope:** mutual TLS + cert authentication on all `pcmk-ubuntu` connections —
QM↔QM channels, SVRCONN clients, mqweb/REST — plus the keystore wiring/distribution
that requires, using the existing lab-pki keystores.

**Non-goals:**
- **Internal authorization** (Stage 2 / #249) — CHLAUTH `SSLPEERMAP`, non-privileged
  identities, queue OAM. Authority stays `MCAUSER('mqm')`; queues stay open.
- **Other arms** — `rdqm-rhel` (revisit later), `pcmk-rhel` (on hold, may be
  abandoned).
- **Certificate expiry/rotation** (deferred per the PKI design §8.2).
- **The Native HA arm itself** — the eventual consumer, separate work.

## 3. The connection inventory (what gets secured)

The `pcmk-ubuntu` arm's distributed link is **four QM↔QM channels** plus SVRCONNs
and the REST endpoint:

| Connection | Where defined | Secured by |
|---|---|---|
| `QMPCMK.QMSVC` (SDR on QMPCMK, RCVR on QMSVC) | `mq-pcmk-qmgr` / `mq-inter-qm` | `SSLCIPH`+`SSLCAUTH`+`SSLPEER` |
| `QMSVC.QMPCMK` (SDR on QMSVC, RCVR on QMPCMK) | `mq-inter-qm` / `mq-pcmk-qmgr` | `SSLCIPH`+`SSLCAUTH`+`SSLPEER` |
| `SVC.SVRCONN` (SVC service responder, client-mode) | `mq-inter-qm` | `SSLCIPH`+`SSLCAUTH`+`SSLPEER` + responder client cert |
| `app-client` → QMPCMK SVRCONN | `mq-client` | TLS SVRCONN + `app-client` cert |
| exporter → QM SVRCONN | `mq-exporter` | TLS SVRCONN + `mq_prometheus` cert |
| `pymqrest` → mqweb (admin REST) | `mqweb` | server cert + client CA-trust flip |

## 4. The recipe (applied uniformly)

- **Each QM:** `ALTER QMGR SSLKEYR('<keystore stem>')` + `KEYRPWD('<value>')` +
  **`CERTLABL('<cert label>')`**, then `REFRESH SECURITY TYPE(SSL)`. **`KEYRPWD` is
  set *once* at QM setup** — the value is sourced from `lab-secret.sh` (never in git)
  and MQ then **persists it (obfuscated) in the QMGR config on the shared LUN**, so it
  follows the QM on failover with no agent to re-supply it (§7). *(Path convention:
  for PKCS#12 the QM expects `<stem>.p12` located by the extensionless `SSLKEYR`
  stem — verify the exact naming against the licensed MQ in the §11 keystore-load
  gate.)*
  - **`CERTLABL` is required, not optional (verified live).** lab-pki labels each
    cert by its CN (e.g. `QMPCMK`), but MQ's default channel cert label is
    `ibmwebspheremq<qmgr>` (lowercase). Without `CERTLABL` set to the actual label,
    the QM finds no matching personal cert and every TLS channel fails handshake with
    **`AMQ9645E` (no SSL certificate for channel)** — even though the keystore loads
    cleanly. Set `CERTLABL` to the entity CN at QM setup.
- **Each channel / SVRCONN:** add `SSLCIPH(<cipher>)`, `SSLCAUTH(REQUIRED)` (require
  a peer cert), `SSLPEER('<expected peer DN>')` (validate it). Applied to **both
  ends** of every channel.
- **Each client** (`app-client`, exporter, the SVC responder, `pymqrest`): present
  its own cert + cipher, and trust the CA(s) it must (its keystore / CA bundle —
  already produced by the provider).
- **Internal authority unchanged** — `MCAUSER('mqm')`, queues open (Stage 2).

## 5. Cipher spec — TLS 1.3 from the start

Default **`ANY_TLS13_OR_HIGHER`** — MQ's alias that negotiates **TLS 1.3+** and
**rejects TLS 1.2 and below**. Start modern: no 1.2 fallback, AEAD-only suites —
the cleanest posture for a greenfield build, and what we carry forward to the
other arms and CRR. (MQ 9.4 and its GSKit support TLS 1.3; our RSA-4096 certs
authenticate fine under TLS 1.3 via RSA-PSS signatures — TLS 1.3 decouples the
cipher suite from the cert's key type.)

**Fidelity note:** those security standards is SVC's mandated channel-security standard. The
exact CipherSpec is **pinned at build time** (verify against those security standards / the
licensed MQ's supported set) — likely a specific TLS 1.3 suite such as
`TLS_AES_256_GCM_SHA384`; `ANY_TLS13_OR_HIGHER` is the representative default until
then.

## 6. Peer identities (`SSLPEER`) — using the partial-DN certs

The provider issued partial-DN identities (shared `O`/`OU`, distinct `CN`) so a
counterparty pins one rule per org. Stage-1 `SSLPEER` values:

| Validating end | Channel/SVRCONN | `SSLPEER` |
|---|---|---|
| QMPCMK | RCVR `QMSVC.QMPCMK`, SDR `QMPCMK.QMSVC` | `O=svc-org` |
| QMSVC | RCVR `QMPCMK.QMSVC`, SDR `QMSVC.QMPCMK` | `O=app-org, OU=messaging` |
| QMSVC | `SVC.SVRCONN` (local SVC responder) | `O=svc-org` (the `svc-responder` cert) |
| QMPCMK | `app-client` / exporter SVRCONN | `O=app-org` (**org-only** — these clients are `OU=apps`/`OU=ops`, not `messaging`) |

The app `SSLPEER` matches on **`O`/`OU`** only — so it holds across arms
(QMPCMK today, QMRDQM/others later) without a rule change, exactly the partial-DN
property the PKI was built for.

## 7. Keystore distribution + HA placement

The provider generates keystores under `build/secrets/pki/` **on the controller**.
They must reach the QM hosts:

- **QMPCMK is a shared-LUN HA QM.** Its keystore goes on the **shared LUN**
  (`/mqshared`, alongside the qmgr/log data), so `SSLKEYR` points at a path that
  **follows the QM on failover** — no per-node copies to keep in sync. Distributed
  once at QM setup (the node that owns the LUN), like the existing inter-QM MQSC.
- **QMSVC** (single container/QM) gets its keystore locally.
- **Clients** get their material on the host they run on: **`app-client`** → its
  host/container; **exporter** → where the `mq_prometheus` exporter runs; **SVC
  responder** → the `svc-sim` host; **`pymqrest`** → wherever it invokes the REST
  API. Each presents it via the **MQI client key repository + cipher** (`pymqi` sets
  its key repository / `MQSCO` and the channel's `SSLCIPH`); the REST clients use the
  **org CA bundle** (trust-only) for the mqweb flip (§9).
- **`KEYRPWD` is set once and persisted, not re-injected.** The value comes from
  `lab-secret.sh` (`pki-keyrpwd-<cn>`, never committed) at setup; `ALTER QMGR` then
  persists it (obfuscated) in the QMGR config **on `/mqshared`**, so an **unattended
  Pacemaker failover** brings the QM up TLS'd with no re-supply (validated in §11).

## 8. CHLAUTH / CONNAUTH stay disabled (Stage 1)

Cert authentication is enforced by the **channel attributes** `SSLCAUTH(REQUIRED)`
+ `SSLPEER`, which operate **independently of `CHLAUTH`**. So Stage 1 keeps both
`CHLAUTH` and `CONNAUTH` **disabled** and still gets encrypted, peer-authenticated
channels. (`CONNAUTH` = user/password auth; `CHLAUTH` `SSLPEERMAP` = Stage 2.)
*Trust-but-verify:* confirm by the functional run that `SSLCAUTH`/`SSLPEER` enforce
as expected with `CHLAUTH` off — there is no ansible-lint gate, so the running
arm is the proof.

## 9. Roles touched + the mqweb coupling

**Arm-agnostic vs arm-specific (the #212/#227 seam).** The **channel-TLS MQSC**
(`SSLCIPH`/`SSLCAUTH`/`SSLPEER` on the channel defs) is **identical across arms** —
author it as a **shared snippet/variable**, not hardcoded inline, so the later
`rdqm-rhel` extension is a "wire its keystore" delta, not a channel-TLS rewrite.
Only the **per-QM keystore wiring** (`SSLKEYR` path, LUN-vs-local) is arm-specific.
No new abstraction — just don't bake the shared part into the pcmk template.

- `mq-pcmk-qmgr` — QMPCMK `SSLKEYR`/`KEYRPWD` + apply the shared channel-TLS MQSC to
  the our-side channels (extend `inter-qm.mqsc.j2`).
- `mq-inter-qm` — QMSVC keystore + the shared channel-TLS MQSC on the their-side
  channels (extend `their-side.mqsc.j2`) + the responder's client cert/trust.
- `mq-client` — `app-client` SVRCONN TLS + client cert.
- `mq-exporter` — exporter SVRCONN TLS + client cert.
- `mqweb` — **the real mechanism** (it rides MQ's *default self-signed* keystore
  today; `mqwebuser.xml` has only `sslRef="mqDefaultSSLConfig"` and the role does no
  cert provisioning): deploy the `mqweb`-entity PKCS#12 where Liberty can read it,
  configure `mqwebuser.xml` with a `<keyStore>` + `<ssl>` (override/replace
  `mqDefaultSSLConfig`) pointing at it with its password, then **`strmqweb`
  restart**. **Coupled change:** the REST clients (`pymqrest`, exporter) currently
  run `verify_tls=False` against the self-signed mqweb and must flip to **trust the
  org CA bundle** *in the same change* mqweb adopts the CA cert, or they break —
  sequence them together.
- **Keystore-distribution step** — new (a role/task that places each entity's
  keystore on its host, shared-LUN-aware for QMPCMK).

## 10. Inventory addition

The SVC service responder is a SVRCONN **client**, so it needs a client cert. Add
a `svc-org` responder entity to `ansible/vars/pki-entities.yml` (e.g.
`CN=svc-responder, O=svc-org`), or reuse `QMSVC`'s cert for the local client —
decide at build (a tiny inventory change, re-run `mqlab pki ensure`).

## 11. Validation

**Functional, by running the arm** (there is no ansible-lint; the running lab is
the gate):

0. **Keystore-load gate (FIRST, blocking — before wiring any channels).** Set
   `SSLKEYR`/`KEYRPWD` on **one** QM, `REFRESH SECURITY TYPE(SSL)`, start a TLS
   listener, and confirm **GSKit loads the keystore** with no parse error
   (`AMQ9657`/`AMQ9633`). First live-MQ test of the lab-pki `compatibility2022`
   PKCS#12 — **this discharges PKI Task 8** and confirms the PKCS#12 `SSLKEYR` path
   convention. **If it fails:** re-encode that keystore with `runmqktool` (the
   documented PKI fallback) *before* proceeding — surfaced on step 0, not after
   building the arm.
1. Bring up the `pcmk-ubuntu` distributed setup with TLS applied.
2. Confirm all four QM↔QM channels go **RUNNING over TLS** (`DIS CHSTATUS` shows
   `SSLPEER`/`SSLCIPH`; a plaintext client is rejected).
3. Confirm the **app trade-flow works end-to-end** (`app-client` → QMPCMK →
   QMSVC → responder → reply) — now encrypted + cert-authenticated.
4. Confirm `pymqrest`/exporter reach mqweb over CA-trusted TLS.
5. **Unattended-failover gate:** trigger a Pacemaker failover and confirm QMPCMK
   comes back up **TLS'd with no manual intervention** — it reads its shared-LUN
   keystore with the persisted `KEYRPWD`. Proves HA-with-TLS, the whole point.

The **cold-rebuild acceptance gate** applies (must come up one-pass on a fresh VM).

## 12. Risks & open questions

- **`SSLCAUTH`/`SSLPEER` with `CHLAUTH` disabled** (§8) — believed correct; confirm
  on the functional run. If MQ requires `CHLAUTH(ENABLED)` for `SSLPEER` to bite,
  fall back to a minimal `CHLAUTH SSLPEERMAP` (pulls a sliver of Stage 2 forward).
- **PKCS#12 ↔ GSKit load** (PKI pushback [2]) — first time the keystores hit a live
  QM; the `compatibility2022` encoding is the legacy form GSKit wants, but this is
  the real proof. `runmqktool` re-encode is the documented fallback.
- **Shared-LUN keystore on failover** — confirm `SSLKEYR` on `/mqshared` is readable
  by the QM after a Pacemaker failover (same mount-follows-QM assumption as the
  qmgr data).
- **mqweb coupling** (§9) — the `verify_tls=False`→CA-trust flip must land with the
  mqweb cert adoption or REST breaks; sequence atomically.
- **Cipher fidelity / TLS 1.3 support** — `ANY_TLS13_OR_HIGHER` (TLS 1.3+) is the
  representative default; pin the those security standards-mandated TLS 1.3 suite at build.
  Confirm the licensed MQ + GSKit actually negotiate TLS 1.3 in the functional run
  (9.4 supports it) — if a component can't, that surfaces immediately as a
  channel-down, not a silent downgrade (the point of requiring 1.3).

### Pushback resolutions (2026-06-17)

A `paad:pushback` review hardened this spec — 6 findings, all resolved:

1. **`KEYRPWD` on HA failover** — set once at setup + **persisted in the QMGR config
   on the shared LUN**, not "runtime-injected each start"; unattended-failover-TLS
   is a validation gate (§4, §7, §11 step 5).
2. **First live-MQ keystore load** — made the **first blocking build step** (§11
   step 0), discharging PKI Task 8, with the `runmqktool` fallback.
3. **mqweb mechanism** — specified the real Liberty config (keystore deploy +
   `mqwebuser.xml` `<keyStore>`/`<ssl>` + `strmqweb` restart + client flip), not
   just "server cert" (§9).
4. **Arm-backend seam** — channel-TLS MQSC authored arm-agnostically; per-arm
   keystore wiring is the only arm-specific part (§9).
5. **`SSLKEYR` PKCS#12 convention** — `<stem>.p12` via extensionless stem, verified
   in the §11 step-0 gate (§4).
6. **Client-keystore distribution** — per-client placement + MQI client key
   repository/cipher spelled out (§7).

## 13. Definition of done & next steps

- This design captured, reviewed, committed (PR into `develop`, issue #250).
- **Buildable now** — the foundation (PKI provider) is merged. Terminal step:
  **`writing-plans`** turns this into the implementation plan once reviewed.
- Sequenced follow-ups: **Stage 2** internal authorization (#249); then **other
  arms** (`rdqm-rhel`); and this unblocks the **NativeHA/CRR** work.
