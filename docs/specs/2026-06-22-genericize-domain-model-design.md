# Genericize the Message-Domain Model — de-brand for public release

**Issue:** #85 (reconciles #73, which folds in here)
**Date:** 2026-06-22
**Status:** Design — awaiting review
**Scope:** Replace every domain- and vendor-specific identifier in the **active
working tree** (code, lab/ansible config, living + dated docs, unimplemented
plans, and open issue text) with a generic request/reply vocabulary, so the
repository reads as a generic HA/DR messaging lab with no traceable relation to
the originating use case. This spec defines the **canonical vocabulary, the exact
rename mapping, the docs de-identification policy, and the execution sequencing**.
It does *not* cover the repository rename (`mq-cluster-tooling` →
`mq-resilience-lab`, #84) — that is a separate human-driven operation. Git
history is explicitly out of scope.

## 1. Goal

The lab models a **generic, reusable HA/DR pattern**: a highly-available,
DR-capable messaging service that an organization operates for an internal
application, which exchanges request/reply messages with an external service
across an inter-business WAN. A specific real-world clearing/payments resilience
scenario got us started, but almost nothing in the design turned out to be
specific to it — and we expect to adapt the lab to numerous external
counterparties over time, each with its own quirks.

The originating entity's name (`DTCC`) and a recognizable real protocol name
(`EPN`) are still woven through identifiers, config, and docs. They overstate the
specificity, hide the generality, and tie a would-be open-source lab to a
particular engagement.

**Deliverable:** after this change, `git clone` followed by a recursive
case-insensitive search for the originating names returns **zero hits in tracked
files**. The lab's behavior is unchanged; only the names change.

This spec reconciles two older, overlapping issues — #73 (anonymize DTCC in
object names) and #85 (genericize the domain model) — whose work surfaces
intersect and which were written before recent distributed-architecture work
*grew* the brand surface (`QMDTCC` alone reached 58 occurrences). #85 is retained
as the single tracking issue; #73 folds into it and is closed as superseded.

## 2. The model — three tiers, named by role

The system has three actors. Naming them by **role** keeps the lab vendor- and
domain-neutral.

1. **Tier 1 — the application (`APP`).** The internal MQ *application* that
   produces request messages. It is an MQ **client**. (Was `FIRM` / `FIRM01` /
   partially `app-client`.)
2. **Tier 2 — our queue manager.** The HA/DR queue manager we operate. It
   *serves* the tier-1 MQ clients and forwards their traffic outward. Named by
   **HA substrate**, which is already generic and stays: `QMPCMK` (Pacemaker),
   `QMNATIVE` (Native HA), `QMRDQM` (RDQM), `QMAIN` (standalone baseline).
3. **Tier 3 — the external service (`SVC`).** The external request/reply
   responder our queue manager exchanges messages with across the WAN — "service"
   in the web-service / REST-endpoint sense (something waiting to respond to a
   request). (Was `DTCC` / `QMDTCC` / "vendor" / partially `SVC`.)

### 2.1 The "service" discipline rule

The English word *service* is overloaded across tiers 2 and 3: our queue manager
is "a service" to its MQ clients, and the external responder is "a service" we
call. To prevent the confusion this overload causes, **the word "service" (and
the token `SVC`) names tier 3 only.** Tier 2 is referred to as "the queue
manager," "the broker," or by its substrate name — never "the service." A
one-line glossary note in the architecture doc states this explicitly.

## 3. Canonical vocabulary

| Concept | Old (inconsistent) | **Canonical** |
|---|---|---|
| Tier-1 application (requester) | `FIRM`, `FIRM01`, `app-client` | **`APP`** / `app-client` |
| Tier-2 queue manager(s) | — | **substrate names kept:** `QMPCMK` / `QMNATIVE` / `QMRDQM` / `QMAIN` |
| Tier-3 external service (responder) | `DTCC`, `QMDTCC`, `QDTCC`, "vendor" (docs) | **`SVC`** / `svc-sim` / `QMSVC` |
| Header pattern | `EPN` (Electronic Payments Network — a real protocol) | **fixed-format header (FFH)** |
| Payload type (queue) | `TRADE` | *folded away* → `SVC.REQUEST` / `APP.REPLY` |
| DRv1 wire payload field | `trade` | **`payload`** (literals `TRADE-<n>` → `MSG-<n>`) |
| Date field | `busdate` | **`session_date`** |
| External-facing network | `net-dtcc` / `virbr-dtcc` | **`net-svc`** / `virbr-svc` |
| TLS/PKI identities | `dtcc-org`, `dtcc-responder`, `client-org`, `in-house`, OU `clearing-service` | **`svc-org`**, **`svc-responder`**, **`app-org`**, **`app`**, OU **`messaging`** |

Multiple naming schemes currently coexist for the same concepts (`DTCC.*`,
`SVC.*`, `TRADE.*` for requests; `FIRM.*`, `APP.*` for our app side; and the
docs-level word "vendor" for the external party). This spec collapses each
concept to its single canonical token and eliminates the stragglers. "vendor" is
docs-only — not in code/lab/ansible identifiers — and folds to "the external
service" / `SVC` in prose.

## 4. Rename surface & exact mapping (code + lab + ansible)

A single atomic find/replace pass across `src/`, `clients/`, `lab/`, `ansible/`,
`content/`.

### 4.1 MQ objects

- External queue manager — **two arms, one canonical name**: the distributed arm
  names it `QMDTCC`; the standalone `content/` arm names it `QDTCC` (a bare QM
  name in `content/dtcc-sim.yaml`, *not* a queue). Both → `QMSVC`.
- Xmit queue to the external QM: in `content/qm-main.yaml` the xmitq is `QDTCC`
  (named after the partner QM) → `QMSVC`.
- Inter-QM channels: `QM*.QMDTCC` / `QMDTCC.QM*` (distributed) and
  `QMAIN.QDTCC` / `QDTCC.QMAIN` (standalone) → `…QMSVC` / `QMSVC…`.
- Request queue: `DTCC.REQUEST`, `TRADE.REQUEST` → `SVC.REQUEST`.
- Reply queue: `TRADE.REPLY`, `FIRM.REPLY` → `APP.REPLY`.
- Sim SVRCONN: `SIM.SVRCONN` → `SVC.SVRCONN`.
- Connection/object names: `DTCC_CONN` → `SVC_CONN`; the template var `dtcc_conn`
  → `svc_conn`; `DTCCSVC` → `SVC`.
- Already canonical, unchanged: `APP.SVRCONN`, `SVC.SVRCONN`, `MON.SVRCONN`.
- `QMAIN` is kept — it names the standalone baseline queue manager, not a brand.

### 4.2 Guests / topology

- Guest: `dtcc-sim` → `svc-sim`; content file `content/dtcc-sim.yaml` →
  `content/svc-sim.yaml`; the `nodes:` entry and all topology references.
- Networks: `net-ext` (inter-business WAN) is already generic — unchanged. But the
  standalone arm rides a second network `net-dtcc` (bridge `virbr-dtcc`,
  10.20.0.0/24): `lab/networks/net-dtcc.xml` → `net-svc.xml`, name `net-svc`,
  bridge `virbr-svc` (subnet unchanged); also update the `net-dtcc` entry in the
  `dashboard.py` message-path list. The `net-*.xml` glob in `net-up.sh` requires
  no code change.

### 4.3 DR framework, fields & ledgers

- Field names — all `dtcc_*` → `svc_*` (`dtcc_received`, `dtcc_replied`,
  `dtcc_receive_counts`, `dtcc_path`, `dtcc_conn`, `dtcc_repl`, `dtcc_counts`) and
  all `firm_*` → `app_*` (`firm_path`, `firm_confirmed`, `firm_states`,
  `firm_ledger`). Enumerate with `grep -rIhoE '\b(dtcc|firm)_[a-z_]+\b'` before
  editing — treat that output as the authoritative field set.
- Ledgers: `dtcc_ledger` → `svc_ledger`; `firm_ledger` → `app_ledger`; ledger
  files `dtcc.jsonl` / `firm.jsonl` → `svc.jsonl` / `app.jsonl` (gitignored
  `build/` ledgers regenerate under the new names).
- DRv1 wire body (`src/mqlab/dr/wire.py`): fields `busdate` → `session_date`,
  `trade` → `payload`; payload literals `TRADE-<n>` → `MSG-<n>`.

### 4.4 Header module & clients

- `src/mqlab/epn.py` → `src/mqlab/header.py`. The `Header` dataclass and ACK
  constants are unchanged; the field `busdate` → `session_date`. All importers
  (`from mqlab.epn import ...`) updated to `from mqlab.header import ...`.
- Clients: the `epn_*` client files no longer exist — `app_requester.py` is
  already canonical. The one remaining straggler is
  `clients/service_responder.py` → `clients/svc_responder.py`, so client
  filenames match the `SVC` token (`app_requester.py` + `svc_responder.py`);
  update its importers.
- Deployment: the `mq-inter-qm` role deploys the responder as the systemd unit
  `mq-service-responder` from `/var/mqm/service_responder.py` — rename to
  `mq-svc-responder` / `/var/mqm/svc_responder.py` (unit template, role tasks,
  `ExecStart`, keyrepo/certlabel → `svc-responder`).
- Tests: `tests/test_epn.py` → `tests/test_header.py`; DR tests referencing
  `dtcc_*` fields updated to `svc_*`.

In docs and code comments, the header is described as "a fixed-format positional
header (FFH)" — blank-padded, left-justified fields — with no reference to EPN or
any real protocol.

### 4.5 TLS / PKI identities

The lab's cert identities encode the org names, so de-branding must reach the PKI
(`ansible/vars/pki-entities.yml`, `ansible/group_vars/all/tls.yml`, and the
`SSLPEER` references in the inter-QM MQSC templates). Both sides are fully
neutralized (decided during planning):

- External side: `dtcc-org` → `svc-org` (CA CN + `organization_name` + `trust:`
  lists), `dtcc-responder` → `svc-responder` (cert CN, keyrepo path, certlabel),
  `tls_peer_dtcc` → `tls_peer_svc` (value `O=dtcc-org` → `O=svc-org`).
- Our side: `client-org` → `app-org`, `in-house`/`inhouse` → `app`, the our-side
  peer var(s) → `tls_peer_app`.
- Domain flavor: the cert OU `clearing-service` → `messaging`.

These are cert *subject DNs*, so a rename regenerates certs on provisioning —
covered by the cold-rebuild gate (§7).

## 5. Docs de-identification policy

**Target gate:** in the tracked working tree, a recursive case-insensitive
search for the unambiguous originating names — `dtcc`, `ficc`, `epn`,
`mqgateway`, `mqgw`, `busdate`, `clearing` — returns **zero hits**. The common-English tokens `firm`,
`trade`, and `vendor` cannot be grepped to literal zero (they appear inside
`confirm`, `platform`, `trade-off`, etc.), so their gate is narrower: zero
remaining *domain references* — whole-word `FIRM` / `TRADE` / `FIRM01`, the
`firm_*` / `trade_*` / `*.TRADE` / `TRADE.*` forms, and any use of "vendor"
meaning the external party (folded to "the external service" / `SVC`). Git
history is *not* touched.

- **Anonymize in place** — all ~51 tracked `.md` files (specs, plans, reports,
  reference, `docs/site/`) get the §3/§4 mapping applied. Dated specs/plans/
  reports included: these are content edits to tracked files, not history
  rewrites. The vast majority of current DTCC mentions are *identifier* mentions
  (`QMDTCC`, `dtcc-sim`, `dtcc_received`), which the mapping already covers.
- **Delete** — documents (or sections) that are *substantially* about the
  originating entity or its in-house tooling rather than the lab:
  `docs/development/2026-06-17-mqgateway-datagram-requirements.md` (an
  employer-specific in-house tool, "MQGateway", plus an out-of-scope inbound flow
  — see §8), deep-dive originating-entity research, the EPN-MQ-implementation-guide
  notes, and the five `dtcc.com` citation URLs. These are not load-bearing; the
  design is not specific to them.
- **Generic sourcing note** — where rationale referenced the originating entity,
  it is reworded to an unnamed generic form: "derived from a real-world
  clearing/payments resilience scenario." No entity is named.
- **Untouched** — non-DTCC reference URLs (IBM MQ docs, DRBD, etc.) and all
  technical content.

## 6. Plans & issue tracker

The rename extends beyond files to forward-looking work, so unimplemented plans
do not reintroduce the old vocabulary:

- **Rewrite unimplemented plan docs** to the canonical vocabulary — notably
  `docs/plans/2026-06-13-plan-2-dtcc-qm-inter-qm-channels.md`,
  `…plan-3-app-requester-end-to-end.md`, `…2026-06-17-vendor-two-site-dr.md`, and
  the RDQM / Native-HA distributed plans that name `QMDTCC` / `QMAIN` / `EPN`.
- **Update open issue titles/bodies** that bake in old names: #147 (`QMDTCC`),
  #148 (`QMAIN`/`EPN`), #237 (vendor/DTCC two-site DR), #267 (converge),
  #145 / #146 / #149, #182.
- **Close #73** as superseded, folded into #85 with a back-link; #85 remains the
  tracking issue for this spec.

## 7. Execution & validation

- **Sequencing.** In-flight work is complete and `develop` is current with no
  pending branches, so this is the next change to the codebase — there is no
  freeze window to wait on and nothing concurrent to serialize against.
- **One atomic branch** `feature/85-genericize-domain-model`, rebased onto
  current `develop` and sanity-checked immediately before execution. The rename
  lands in a single coordinated pass so the tree is never left half-renamed (the
  current inconsistent state is exactly the cost of *not* doing this atomically).
- **Re-provision** the renamed `svc-sim` guest and re-render the `QMSVC` object
  set (`mqlab` re-render; libvirt `net-*` globbing needs no change).
- **Gate.** `vrg-container-run -- vrg-validate` green at 100% branch coverage,
  **and** a cold-rebuild acceptance of the message path per the repo's
  cold-rebuild gate — lint-green alone is not "done" for provisioning-affecting
  changes.
- **Post-conditions.** Working-tree grep for the originating names is empty;
  end-to-end `APP → our QM → SVC → reply` flow passes; the HA/DR drills line up
  (queue names and client `--*-queue` args renamed together).

## 8. Out of scope

- **Repository rename** (`mq-cluster-tooling` → `mq-resilience-lab`, #84) — a
  separate human-driven GitHub + filesystem + Vergil-VM operation with its own
  `.claude` carry-forward gotcha. Lands before public release but is not coupled
  to this spec.
- **The inbound, externally-initiated datagram flow** (the in-house "MQGateway"
  concept, #245) — out-of-band admin traffic (start-of-day / end-of-day), not the
  main request/reply payload. The lab does not model it; its requirements doc is
  deleted (§5), and §2's role model stays request/reply only, with no second
  initiation axis.
- **Git history** — no rewriting, no `filter-branch`. The gate is the working
  tree only.
- **CLI command-interface reorg** — a separate concern; this spec touches object
  and entity names, not the `mqlab` command surface.
