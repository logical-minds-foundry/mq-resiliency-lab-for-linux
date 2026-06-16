# Lab PKI / TLS Certificate Provider — Design

> **Status:** design, first pass — brainstormed 2026-06-16.
> **Date:** 2026-06-16
> **Author:** Phillip Moore (with Claude)
> **Tracking issue:** #201
> **Relationship:** the base-lab **security foundation** that prior specs
> repeatedly deferred to "a future version / separate lab"
> ([`2026-06-13-distributed-mq-architecture-design.md`](2026-06-13-distributed-mq-architecture-design.md)
> defers mutual TLS / `CHLAUTH` / `SSLPEER`), and that the Native HA arm design
> ([`2026-06-16-nativeha-k8s-arm-design.md`](2026-06-16-nativeha-k8s-arm-design.md)
> §4.9, merged via #200) escalated to a **hard, up-front blocking dependency**.
> DTCC also mandates channel TLS (Important Notice GOV1683-24). This is that lab.

---

## 1. Why this exists

Security — specifically TLS on MQ channels and the REST/web endpoints — has been
**deliberately deferred across every prior spec** (security out of scope per the
authoritative design §1; the distributed-architecture design parks mutual TLS,
`CHLAUTH`, and `SSLPEER` for "a separate security lab"). Two forces now make it
the next thing to build:

1. **The Native HA arm hard-requires it.** On the OpenShift substrate, TLS is not
   optional hardening — Route SNI routing and Native HA CRR replication both
   require it (nativeha design §4.9). The arm build is gated behind this layer.
2. **DTCC mandates it.** Channel TLS per GOV1683-24 is a real onboarding
   requirement; the tooling must eventually emit onboarding-ready TLS-secured
   channel config (authoritative design §9.2, §11).

Everything TLS needs a trust root. So the **first** thing to build is the **PKI**:
a certificate-authority provider and the per-entity certificates + key
repositories the lab's MQ entities consume. This spec designs that foundation for
the **base lab (VM arms)**; the Kubernetes/OpenShift cert needs extend from the
same provider later.

## 2. Scope & non-goals

**In scope (this spec):**

- A **certificate-authority "provider"** for the lab, modeling **two
  organizations** (the in-house client org and the DTCC-modeled service org).
- **Per-entity certificate/key issuance** for both organizations' MQ entities.
- **Assembly of the key repositories** (PKCS#12) each entity consumes.
- **Provisioning lifecycle** — idempotent create/ensure, add-entity.

**Out of scope (downstream or deferred):**

- **MQ-side application of the certs** — channel `SSLCIPH`, `CHLAUTH`/`SSLPEER`
  peer mapping, mqweb TLS wiring. This is the "derive it easily" content-plane
  layer that *consumes* the keystores; **its own later spec.**
- **Certificate expiration + renewal/rotation management** — deferred, **but a
  required follow-up** (§8.2).
- **Kubernetes/OpenShift cert needs** (Routes/SNI, CRR endpoints) — later, from
  the same provider.
- **Security posture/hardening** beyond transport-TLS plumbing (auth policy, mTLS
  client-auth semantics, channel exits) — separate, requirements-driven.

## 3. Architecture — the CA provider

An idempotent **Ansible role** built on the **`community.crypto`** collection —
the modern, glass-box realization of an OpenSSL CA, honoring the repo's "Ansible
over shell" principle (native `openssl_privatekey`, `openssl_csr`,
`x509_certificate`, `openssl_pkcs12` modules; re-runnable and drift-correcting; no
cobbled `openssl` shell pipelines).

The provider, run from a committed **entity inventory**, does this per run:

1. **Build the two org root CAs** — `client-org` and `dtcc-org`, each a private
   key + self-signed root certificate (§4).
2. **Issue each entity's cert** — generate the entity key + CSR, sign with **its
   own org's** CA → personal certificate (§5).
3. **Assemble each entity's PKCS#12 key repository** — personal cert + private key
   + the signer (CA) certs that entity must trust (§6, §4).
4. **Emit the public signer certs** for the cross-org exchange (§4).

The unit of output per entity is **one PKCS#12 keystore + its `KEYRPWD`** — the
exact thing an MQ queue manager or client consumes.

## 4. CA topology & trust model

**Two independent organizational CAs, cross-trusted by signer exchange** — there
is **no shared root**, exactly as two separate real enterprises do DTCC
connectivity. This is the high-fidelity model: it lets the lab actually exercise
cross-org `SSLPEER`/`CHLAUTH` DN matching across a real trust boundary instead of
hand-waving it.

```
client-org Root CA                      dtcc-org Root CA
   ├─ QMPCMK            signer certs        ├─ QMDTCC
   ├─ QMRDQM            exchanged  <──>      └─ (dtcc-side entities)
   ├─ app-client
   ├─ mq_prometheus
   └─ mqweb / pymqrest
```

- **Intra-org trust:** every entity trusts its own org's CA.
- **Cross-org trust (the boundary that matters):** the in-house QMs that talk to
  DTCC (`QMPCMK ↔ QMDTCC`, `QMRDQM ↔ QMDTCC`) carry the **other** org's CA signer
  cert in their keystore, and vice versa. That mutual signer exchange is the real
  onboarding handshake, in miniature.

## 5. Entity inventory & naming (base lab)

Driven by a committed inventory; DN scheme **`O=<org>, CN=<entity>`** so downstream
`SSLPEER`/`CHLAUTH` DN matching has real structure to match.

| Org (`O=`) | Entity (`CN=`) | Role / why it needs a cert |
|---|---|---|
| `client-org` | `QMPCMK` | Pacemaker-arm in-house QM (server cert; cross-org channel to DTCC) |
| `client-org` | `QMRDQM` | RDQM-arm in-house QM (server cert; cross-org channel to DTCC) |
| `client-org` | `app-client` | MQI client, mutual TLS over SVRCONN |
| `client-org` | `mq_prometheus` | exporter client, TLS SVRCONN to the QM |
| `client-org` | `mqweb` | admin REST API / web endpoint TLS (per the existing `mqwebuser.xml` sslRef) |
| `client-org` | `pymqrest` | content-plane client; must trust the `mqweb` cert |
| `dtcc-org` | `QMDTCC` | DTCC-sim service QM (server cert; cross-org channel to the in-house QMs) |

*(QMNATIVE / OpenShift Route + CRR endpoint certs are the future extension — same
provider, new inventory entries.)*

## 6. Key repository — PKCS#12, end to end

**Verified (2026-06-16; re-verify against the licensed version).** IBM MQ **9.3
LTS added PKCS#12 support** for queue-manager key repositories: the same `SSLKEYR`
attribute references the PKCS#12 file, with the keystore password supplied via the
QM's **`KEYRPWD`** attribute (PKCS#12 has no `.sth` stash file, unlike CMS). MQ
**9.4 removed** `runmqckm`/`strmqikm`; the new `runmqktool` manages **PKCS#12/JKS,
not CMS**. So PKCS#12 is the strategic direction.

**Consequence — the pipeline is end-to-end Ansible with no GSKit/`runmqakm`
detour.** `community.crypto`'s `openssl_pkcs12` builds the keystore directly; the
QM is configured with `SSLKEYR` → the PKCS#12 path and `KEYRPWD` → its password
(runtime-injected, §7). No CMS key database, no `runmqakm` import step. The
tooling choice (§3) and the keystore format are aligned by MQ's own direction.

**References (verify):**

- IBM MQ supports PKCS#12 keystores — <https://community.ibm.com/community/user/blogs/robert-parker1/2024/08/13/did-you-know-ibm-mq-supports-pkcs12-keystores>
- Securing IBM MQ 9.4 (PKCS#12 / `runmqktool`; CMS tooling removal) — <https://public.dhe.ibm.com/software/integration/wmq/docs/V9.4/PDFs/mq94.secure.pdf>
- Ansible `community.crypto` collection — <https://docs.ansible.com/ansible/latest/collections/community/crypto/>
- DTCC Important Notice GOV1683-24 (channel TLS) — <https://www.dtcc.com/-/media/Files/pdf/2024/4/19/GOV1683-24.pdf>

## 7. Secrets & ephemerality

CA private keys, entity private keys, and `KEYRPWD` values are **secrets** and
follow the repo policy: they live under **`build/` (gitignored), never
committed**, with `KEYRPWD` runtime-injected exactly like the existing `pymqrest`
credentials. No keystore, key, or password artifact enters git.

**Ephemeral, but survives a restart — not a rebuild.** The CA materials are
structured to **persist across a base-VM restart but regenerate on a full
rebuild** — the lab's existing per-arm cached-state convention (rdqm-parity-pivot
§3.5 / #167: *persistent across a restart, disposable across a rebuild*). The
**source of truth is the committed playbook + entity inventory**; the keystores
are reproducible cache — never precious, always regenerable.

## 8. Lifecycle

### 8.1 v1 — provisioning lifecycle (in scope)

Because `community.crypto` is idempotent, re-running "ensures" the state:

- **create / ensure** — build the CAs and all inventory entities' certs +
  keystores; re-runs converge.
- **add-entity** — extend the inventory, re-run; only the new entity is issued.
- Certificates issued with **1–2 year validity** (long enough that nothing expires
  within the engagement window).

### 8.2 Deferred — required follow-up, NOT dropped

**Certificate expiration + renewal/rotation management** is explicitly deferred to
a follow-up effort. It is *owed work*, not dropped — this is one of the classic
places TLS "burns people," so it is logged loudly. The follow-up must deliver:

1. **Reporting** — surface *when* certs approach renewal (expiry visibility /
   monitoring), so it never silently lapses.
2. **Rotation automation** — done correctly, reliably, and **transparently**:
   ideally **no more than a QM restart**, and where supported, **none** — via
   `REFRESH SECURITY TYPE(SSL)`, which re-reads the key repository live. *(Whether
   that covers every scenario — e.g. rotating the QM's own personal cert vs.
   adding a signer — gets verified when this is built, not assumed.)*

**Why deferred:** off the October critical path; nothing issued now (1–2 yr
validity) expires within the engagement window. Revisit once the security
foundation is stable.

## 9. Where it sits

- **Bring-up plane (Ansible).** The provider role generates the certs/keystores,
  distributes each entity's PKCS#12 to its host, and configures `SSLKEYR` /
  `KEYRPWD` on each QM (via `crtmqm`/`ALTER QMGR` or `pymqrest`).
- **CLI surface.** An **`mqlab pki`** command group wraps the playbook (the repo's
  CLI-wraps-playbook pattern): `create-ca`, `issue`, `list`, plus the deferred
  `report`/`renew` (§8.2) when that follow-up lands.
- **Content plane (downstream, separate spec).** Channel `SSLCIPH`,
  `CHLAUTH`/`SSLPEER` peer mapping, and mqweb TLS wiring *consume* these keystores
  — the "derive it easily" layer, not this spec.

## 10. Fidelity to GOV1683-24

Cipher specs, key sizes, and TLS versions should be **onboarding-representative**
(TLS 1.2/1.3 cipher specs MQ supports, sane key sizes) so the lab's certs are a
faithful dry-run of the DTCC-mandated posture. The exact mandated specifics are
pinned when the downstream channel-security spec consumes this foundation; here
the goal is "representative, not toy."

## 11. Risks & open questions

- **`community.crypto` PKCS#12 ↔ MQ interop (verify on first build).** The verified
  docs say MQ 9.3+ accepts PKCS#12 for `SSLKEYR`; confirm a `community.crypto`-built
  PKCS#12 (bag attributes, friendly names, encryption algorithm) loads cleanly in
  the licensed MQ version — a known fiddly spot. Cold-rebuild acceptance gate
  applies.
- **Deferred expiry/rotation (§8.2)** — the biggest owed risk; logged, off the
  critical path, must be revisited before anything lives a year.
- **Two-org trust correctness** — the whole point is to catch trust-config bugs;
  the downstream channel spec must actually exercise cross-org `SSLPEER` matching,
  not just plaintext-with-certs-present.
- **`KEYRPWD` handling** — PKCS#12 lacks a stash file, so the password must reach
  the QM at runtime securely; reuse the existing runtime-injection pattern, keep it
  out of git.

## 12. Definition of done & next steps

- This design captured, reviewed, and committed (PR into `develop`, issue #201).
- **This work is buildable now** — it is *not* gated like the Native HA arm; it is
  the prerequisite the arm waits on. So the terminal step is a real plan:
  **`writing-plans`** turns this into an implementation plan once the spec is
  reviewed.
- Sequenced follow-ups: (a) the downstream **channel-security** spec that consumes
  these keystores (`SSLCIPH`/`CHLAUTH`/`SSLPEER`/mqweb TLS); (b) the deferred
  **expiry + rotation** management (§8.2); (c) the **OpenShift extension** (Route/
  SNI + CRR endpoint certs) for the Native HA arm.
