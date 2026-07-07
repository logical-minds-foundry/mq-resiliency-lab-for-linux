# MQ TLS Coverage — the no-plaintext invariant

> Scope: a lab-wide map of **every MQ communication path** and its TLS state,
> plus the invariant that governs them. This is the security north star made
> auditable: *no plaintext MQ communication anywhere*. It covers the MQ channels
> (application, monitoring, inter-QM) and the HA/DR **replication** links on every
> arm.
>
> Companion to [`nativeha-crr-tls-guide.md`](nativeha-crr-tls-guide.md) (the
> Native HA / CRR keystore specifics) and [`drbd-operations.md`](drbd-operations.md)
> / [`rdqm-ha-cheatsheet.md`](rdqm-ha-cheatsheet.md) (the RDQM substrate). This
> doc is the index over all of them.
>
> Sources: `ansible/group_vars/all/tls.yml`, the per-arm provision playbooks and
> roles (`mq-pcmk-qmgr`, `mq-nativeha`, `rdqm-app-tls`, `mq-inter-qm`,
> `mq-exporter`), `lab/scripts/rdqm-qm-create.sh`, and the IBM MQ 9.4.x docs cited
> in §3.

## 1. The invariant

**Every MQ communication path in the lab is TLS-secured. There is no
optional-plaintext path.**

Two properties make this an invariant rather than an aspiration:

- **Validated, not assumed.** Each path's TLS state is proven by a cold rebuild
  of the arm, not inferred from a product manual. Where a manual and the lab
  disagree (see §3, CRR), the repo documents the *validated* reality — that is
  the whole point of building the lab.
- **No silent downgrade.** A channel that *can* carry `SSLCIPH` and does not is a
  defect, not a default. The coverage matrix below is exhaustive so a future
  plaintext regression is visible rather than accidental.

Our reference build is **IBM MQ 9.4.5 Advanced on RHEL 9.6** (and the Ubuntu
Native HA peer). Every row below is securable — and secured — on that build.

## 2. Coverage matrix

| # | Path | Arms | Mechanism | Cipher / identity | Secured |
|---|------|------|-----------|-------------------|---------|
| 1 | App client → app QM | pcmk, nha-rhel, nha-ubuntu, rdqm-rhel | `APP.SVRCONN` (SVRCONN, mutual) | `ANY_TLS13_OR_HIGHER`, `SSLPEER O=app-org` | ✅ |
| 2 | Ops exporter → app & svc QMs | all QMs | `MON.SVRCONN` (SVRCONN, mutual) | `ANY_TLS13_OR_HIGHER`, `SSLPEER O=app-org` | ✅ |
| 3 | Inter-QM, app side | pcmk, nha-rhel, nha-ubuntu, rdqm-rhel | `<QM>.SVCQM` (SDR) + `SVCQM.<QM>` (RCVR) | `ANY_TLS13_OR_HIGHER`, `SSLPEER O=svc-org` | ✅ |
| 4 | Inter-QM, svc side | shared SVCQM | `SVCQM.<QM>` (SDR) + `<QM>.SVCQM` (RCVR) + `SVC.SVRCONN` | `ANY_TLS13_OR_HIGHER`, `SSLPEER O=app-org` / `O=svc-org` | ✅ |
| 5 | Native HA within-group raft replication | nha-rhel, nha-ubuntu | `NativeHALocalInstance` `CipherSpec` (net-hb :9414) | `ANY_TLS12` → `ECDHE_RSA_AES_256_GCM_SHA384` | ✅ |
| 6 | Native HA **CRR cross-region** replication | nha-rhel (HA/DR) | `NativeHALocalInstance` `CipherSpec` (net-wan :9415) | `ANY_TLS12` → `ECDHE_RSA_AES_256_GCM_SHA384` | ✅ |
| 7 | RDQM **HA** replication links | rdqm-rhel | `crtmqm -re`/`-reh` + `tlshd` (kernel TLS) | KTLS; certs SAN `encrypted.remote` | ✅ [^545] |
| 8 | RDQM **DR** replication link | rdqm-rhel | `crtmqm -re`/`-red` + `tlshd` (kernel TLS) | KTLS; certs SAN `encrypted.remote` | ✅ [^545] |

[^545]: RDQM HA/DR replication TLS is delivered by
    [#545](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/545)
    (secure RDQM replication with `crtmqm -re` + `tlshd`). It closes the last
    optional-plaintext path; before it, rows 7–8 ran plaintext while the app and
    inter-QM channels (rows 1–4) were already TLS.

The channel-TLS attributes (rows 1–4) are authored once as the shared `tls_*`
vars in `ansible/group_vars/all/tls.yml` and applied per arm, so a new arm is a
keystore-wiring delta, never a per-arm channel rewrite:

| Var | Value | Used by |
|-----|-------|---------|
| `tls_cipher` | `ANY_TLS13_OR_HIGHER` | every channel `SSLCIPH` |
| `tls_peer_app` | `O=app-org,OU=messaging` | inter-QM peer = the app QM |
| `tls_peer_svc` | `O=svc-org` | inter-QM peer = the SVC QM |
| `tls_peer_client` | `O=app-org` | app + monitoring SVRCONN clients |

## 3. Version-gated exceptions

**None on MQ 9.4.5.** Every MQ path above is securable on our build.

This section exists because two paths were once believed to be securable only in
MQ 10. Both beliefs were **wrong for 9.4.5**, and the lab is the proof:

- **Native HA CRR cross-region link.** Believed v10-only; in fact secured on
  9.4.5. A single `NativeHALocalInstance CipherSpec=ANY_TLS12` secures both the
  within-group raft link *and* the cross-region link. Verified live (Phase 3,
  #246): the link negotiates `ECDHE_RSA_AES_256_GCM_SHA384` and
  `dspmq -m <QM> -o nativeha -g` reports `CONNGRP(yes) INSYNC(yes)` over TLS. CRR
  itself is a 9.4.2 CD feature.

- **RDQM HA/DR replication links.** Believed v10-only; in fact securable on
  9.4.x. `crtmqm` accepts `-re` (both HA and DR links), `-red` (DR only) and
  `-reh` (HA only); the prerequisite is a configured `tlshd` service and
  certificates whose SAN carries the node hostname plus a group DNS name
  (default `encrypted.remote`). Implemented in #545.

The takeaway: **when a product manual and a validated lab result disagree, this
repo documents the validated result.** These two rows are why the "invariant +
version-gated exceptions" framing was retired in favour of a plain coverage
matrix.

## 4. The two PKI paths

TLS on the two classes of link is rooted in **different** key material — a
frequent source of confusion:

- **MQ channel keystore** (rows 1–4, and the Native HA replication CipherSpec in
  rows 5–6). A per-QM PKCS#12 (`lab-pki` entity `<QM>`, `O=app-org,OU=messaging`)
  referenced by `SSLKEYR` + `CERTLABL`, deployed by the `pki-distribute` role.
  For RDQM the keystore lives under the replicated QM data dir
  (`/var/mqm/qmgrs/<QM>/ssl`) so it follows the QM on HA/DR moves.
- **Kernel-TLS material for RDQM replication** (rows 7–8). RDQM secures its
  replication with **kernel TLS via `tlshd`**, not the MQ keystore. It needs its
  own certificates (SAN `encrypted.remote`) placed where `tlshd` expects them,
  configured *before* `crtmqm`. This is why RDQM replication TLS is a distinct
  piece of work (#545) rather than falling out of the channel-keystore wiring.

## 5. How coverage is verified

Each path is asserted by a cold/warm rebuild of its arm:

- **Channels (1–4):** the channel runs `RUNNING` with no `AMQ9660`/`AMQ9999` in
  the QM error log; the app round-trips a message; the exporter completes its
  `MON.SVRCONN` handshake and metrics land in Prometheus.
- **Native HA raft + CRR (5–6):** `dspmq -o nativeha -g` shows the peer group
  `CONNGRP(yes) INSYNC(yes)`; the negotiated cipher is TLS.
- **RDQM replication (7–8):** the HA/DR replication links establish over TLS
  (`tlshd` handshake succeeds; `rdqmstatus` healthy), QM objects + messages still
  replicate to site B, and DR cutover still works.

## 6. Out of scope

This doc covers **MQ** communication paths. Non-MQ planes are secured and tracked
elsewhere: the `mqweb` administrative REST API is HTTPS (9443) by design, and the
observability HTTP plane (Prometheus/Grafana/Loki scrape endpoints) is a separate
concern. The invariant here is specifically: no plaintext **MQ** traffic.

## Sources

- IBM MQ 9.4.x — Creating HA/DR RDQMs (`-re`/`-red`/`-reh`, `tlshd`, SAN):
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=availability-creating-drha-rdqms>
- IBM MQ 9.4.x — Creating a disaster recovery RDQM:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=recovery-creating-disaster-rdqm>
- Lab: `ansible/group_vars/all/tls.yml`, `ansible/roles/rdqm-app-tls/`,
  `ansible/roles/mq-pcmk-qmgr/`, `ansible/roles/mq-nativeha/tasks/{tls,crr}.yml`,
  `ansible/roles/mq-inter-qm/`, `lab/scripts/rdqm-qm-create.sh`
- Related issues: #410 / #426 (RDQM app-channel TLS), #411 (nativeha
  `APP.SVRCONN`), #246 (Native HA replication + CRR TLS), #545 (RDQM replication
  TLS)
