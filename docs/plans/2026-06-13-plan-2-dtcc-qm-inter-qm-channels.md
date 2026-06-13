# Plan 2 — DTCC service QM + inter-QM channels — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]`.
>
> **Issue:** #147 (sub-issue of epic #145). **Spec:** `docs/specs/2026-06-13-distributed-mq-architecture-design.md` §3–§5.

**Goal:** Stand up the counterparty queue manager (QMDTCC) and wire the inter-QM SENDER/RECEIVER channel set so a request put on QMPCMK crosses the WAN to QMDTCC, is processed by a bindings responder, and the reply returns to QMPCMK.

**Architecture:** Reuse the existing `mq-qmgr` role to build QMDTCC (standalone) on the repurposed `dtcc-sim` (now on `net-ext`). Apply the inter-QM MQSC object set per spec §5.2 — our side folded into the `mq-pcmk-qmgr` MQSC block at `qm create` (so a cold boot defines it one-pass, persisted to the shared LUN), their side via the `mq-qmgr` flow. A clean bindings responder honours the correlation contract.

**Tech Stack:** Ansible, IBM MQ `runmqsc`/`crtmqm`, Python 3.12 + pymqi (bindings), systemd, pytest (topology/setup integrity only).

---

## Verified facts (this branch)

- `mq-qmgr` role (`ansible/roles/mq-qmgr`) builds a standalone QM from `qmgr_name`: `crtmqm` → systemd unit → mqweb → `CHLAUTH(DISABLED) CONNAUTH(' ')` → `DEFINE LISTENER(L1414) PORT(1414)`. No app queues/channels. The legacy `standalone` setup already builds `QDTCC` here via `site.yml`.
- `mq-pcmk-qmgr` applies QMPCMK's MQSC in the **first-time-only create block** (`when: mq_fs_owned.rc != 0`): briefly `strmqm` → `runmqsc` (LISTENER, `APP.SVRCONN`, `CHLAUTH(DISABLED)`, `QLOCAL(HA.TEST)`) → `endmqm -w`. Persisted to the shared LUN, so it follows the QM on failover. This is where our-side inter-QM objects belong — defined once at create, cold-boot one-pass.
- `dtcc-sim` today: `net-mgmt 10.50.0.50`, `net-dtcc 10.20.0.50`. No net-ext. The `standalone` setup deploys the pymqi venv + EPN/dr clients to `dtcc:client` (`site.yml`).
- Spec §5.2 MQSC (target): QMPCMK = `QREMOTE DTCC.REQUEST`→`SVC.REQUEST@QMDTCC` via xmitq `QMDTCC`, `SENDER QMPCMK.QMDTCC` (CONNAME=DTCC `10.60.0.50`, tuned timers), `RECEIVER QMDTCC.QMPCMK`, `QLOCAL APP.REPLY`. QMDTCC = `RECEIVER QMPCMK.QMDTCC`, `QLOCAL SVC.REQUEST`, xmitq `QMPCMK`, `SENDER QMDTCC.QMPCMK` (CONNAME=`10.60.0.10,10.60.0.20`, tuned timers).

## Structural decision (the one to confirm)

**Plan 2 introduces a `distributed` setup** = groups `[san_a, pcmk_a, dtcc]`, provisioned by a new `site-distributed.yml`, with `qm: {QMPCMK, vip 10.10.1.200, vip_ext 10.60.0.10, dtcc_conn 10.60.0.50}`. Plan 3 then **extends** it (adds `[san_b, pcmk_b]` for DR + the `client`/app group) and retires the legacy `standalone`/QMAIN/EPN. Rationale: Plan 2's "done when" (a request crosses to QMDTCC and the reply returns) needs a runnable composition of QMPCMK + QMDTCC + channels; the cleanest is a real setup rather than ad-hoc playbook runs. `site-distributed.yml` `import_playbook: site-pcmk.yml` (reuse the whole SAN/cluster/QM bring-up) then adds the DTCC + responder plays. The legacy `standalone` stays intact until Plan 3, so `net-dtcc` is kept on `dtcc-sim` for now.

## File structure

- **Modify** `lab/topology.yaml` — `net-ext: 10.60.0.50` on `dtcc-sim`; new `distributed` setup; `dtcc_conn` on its qm config.
- **Modify** `src/mqlab/setups.py` — `QmConfig.dtcc_conn` (optional; only the distributed setup sets it).
- **Modify** `src/mqlab/cli.py` — pass `-e dtcc_conn=…` to `qm create` when present.
- **Modify** `ansible/roles/mq-pcmk-qmgr/tasks/main.yml` — append the our-side inter-QM MQSC to the create-block `runmqsc`, gated on `dtcc_conn is defined`.
- **New** `ansible/roles/mq-inter-qm/` — applies the **their-side** MQSC to QMDTCC (templated `their-side.mqsc.j2`) + deploys the responder.
- **New** `clients/service_responder.py` — bindings responder honouring the correlation contract (`reply CorrelId = request MsgId`, `MQPMO_NEW_MSG_ID`).
- **New** `ansible/site-distributed.yml` — `import_playbook: site-pcmk.yml` + DTCC plays (mq-install, mq-qmgr QMDTCC, mq-inter-qm, responder service).
- **Tests** `tests/test_setups.py`, `tests/test_topology_integrity.py`, `tests/test_cli_qm.py` — `dtcc_conn` threading + `distributed` setup integrity.

## Tasks (TDD where code; cold-boot-verified for infra)

### Task 1 — topology: dtcc-sim on net-ext + the `distributed` setup
- [ ] Failing `tests/test_topology_integrity.py::test_dtcc_sim_on_net_ext` + `test_distributed_setup_composed`.
- [ ] Add `net-ext: 10.60.0.50` to `dtcc-sim` nics; add the `distributed` setup (groups `[san_a, pcmk_a, dtcc]`, provision `ansible/site-distributed.yml`, qm `{QMPCMK, vip, vip_ext, dtcc_conn 10.60.0.50}`).
- [ ] Green; `vrg-commit`.

### Task 2 — thread `dtcc_conn` through QmConfig → qm create
- [ ] Failing `tests/test_setups.py` (QmConfig.dtcc_conn optional, None for pcmk_san_ha, set for distributed) + `tests/test_cli_qm.py` (passes `-e dtcc_conn=` only when set).
- [ ] `QmConfig.dtcc_conn: str | None`; parse `cfg['qm'].get('dtcc_conn')`; cli appends the `-e` when not None.
- [ ] Green; `vrg-commit`.

### Task 3 — our-side inter-QM MQSC in the QM create block
- [ ] In `mq-pcmk-qmgr` create block, after the existing `runmqsc`, append (gated `when: dtcc_conn is defined`): `QLOCAL(APP.REPLY)`, `QREMOTE(DTCC.REQUEST) RNAME(SVC.REQUEST) RQMNAME(QMDTCC) XMITQ(QMDTCC)`, `QLOCAL(QMDTCC) USAGE(XMITQ)` + trigger, `CHANNEL(QMPCMK.QMDTCC) CHLTYPE(SDR) CONNAME('{{ dtcc_conn }}(1414)') XMITQ(QMDTCC)` + tuned timers, `CHANNEL(QMDTCC.QMPCMK) CHLTYPE(RCVR)`.
- [ ] `vrg-validate` (lint); `vrg-commit`. Behaviour proven at cold boot.

### Task 4 — the bindings responder
- [ ] `clients/service_responder.py`: connect QMDTCC (bindings), `MQGET SVC.REQUEST` (wait), process, `MQPUT` reply to msg `ReplyToQ`/`ReplyToQMgr` with `CorrelId = MsgId`, `MQPMO_NEW_MSG_ID`; loop; fail-loud on MQI errors.
- [ ] `vrg-validate`; `vrg-commit`.

### Task 5 — mq-inter-qm role (their side) + responder service
- [ ] `ansible/roles/mq-inter-qm`: render `their-side.mqsc.j2` (RECEIVER `QMPCMK.QMDTCC`, `QLOCAL SVC.REQUEST`, xmitq `QMPCMK`+trigger, `SENDER QMDTCC.QMPCMK` CONNAME `10.60.0.10,10.60.0.20` + timers) → `runmqsc QMDTCC`; deploy `service_responder.py` + a `systemd` unit (bindings, `mqm`), enabled+started.
- [ ] `vrg-validate`; `vrg-commit`.

### Task 6 — site-distributed.yml
- [ ] `import_playbook: site-pcmk.yml`; add `hosts: dtcc` plays: `mq-install`, `mq-qmgr` (`qmgr_name: QMDTCC`), `mq-inter-qm`, responder service; pymqi venv (reuse the `site.yml` pattern).
- [ ] `vrg-validate`; `vrg-commit`.

### Task 7 — full validation + cold-boot verification
- [ ] `vrg-validate` green.
- [ ] **Cold-boot (operator):** rebuild → nets → obs → `mqlab vm create/up/provision distributed` → `mqlab qm create distributed` → bring up the responder → put a test request on QMPCMK `DTCC.REQUEST`, confirm it lands on `SVC.REQUEST@QMDTCC`, the responder replies, and the reply arrives on `APP.REPLY@QMPCMK`. Channels `QMPCMK.QMDTCC`/`QMDTCC.QMPCMK` RUNNING; xmitqs drain.

## Acceptance (spec §13.1)
A request `MQPUT` to `DTCC.REQUEST` on QMPCMK is processed by the service on QMDTCC and the reply returns to `APP.REPLY` — across two distinct QMs joined only by sender/receiver channels. Verified one-pass on a cold rebuild ([[cold-rebuild-acceptance-gate]]).
