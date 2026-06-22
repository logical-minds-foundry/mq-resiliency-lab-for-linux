# Plan 3 — App requester + end-to-end + retire legacy — Implementation Plan

> **Issue:** #148 (sub-issue of epic #145). **Spec:** `docs/specs/2026-06-13-distributed-mq-architecture-design.md` §5–§6, §8.

**Goal:** Close the distributed flow with a real **app requester** (client-mode to QMPCMK, CONNAME list, correlation contract) so a full app→service→app round-trip runs on the `distributed` setup, and retire the legacy single-QM scaffolding (`standalone`/QMAIN/qm-main/FFH).

**Architecture:** The `app-client` VM becomes the **app** (requester) — it already homes on `net-data-a/-b`, so it reaches both site VIPs. A new `clients/app_requester.py` puts to `SVC.REQUEST` (ReplyToQ `APP.REPLY`, ReplyToQMgr `QMPCMK`) and gets `APP.REPLY` by `CorrelId`, with `CONNAME(10.10.1.200, 10.10.2.200)` + `MQCNO_RECONNECT`. The `distributed` setup gains the `app` group; legacy `standalone`/QMAIN/FFH are removed.

**Tech Stack:** Python 3.12 + pymqi (client mode), Ansible, IBM MQ, pytest (topology/setup integrity).

---

## Scope decision to confirm (the DR boundary)

The original decomposition put **DR site B** (`san_b`, `pcmk_b`) in Plan 3's `distributed` setup. **Recommendation: defer DR to Plan 4**, where it's actually exercised (resilience validation needs DR anyway), and keep Plan 3 focused: **app requester + end-to-end on the site-A `distributed` setup + retire legacy.** Plan 4 then extends `distributed` to both sites and owns the cutover. Cleaner, smaller, and DR lands with the thing that tests it. (Flagged because it shifts a boundary — same as the Plan 2/3 shift.)

## File structure

- **New** `clients/app_requester.py` — client-mode requester (CONNAME list, MQCNO_RECONNECT, correlation contract: GET `APP.REPLY` by `CorrelId` == put `MsgId`).
- **Modify** `lab/topology.yaml` — add `app` group to the `distributed` setup; **retire** the `standalone` setup, `qm` group, `qm-main` node, `client` group → rename to `app` (or repoint `app-client`).
- **Modify** `ansible/site-distributed.yml` — add an `app` play: pymqi venv + deploy `app_requester.py` (+ the client TCP-keepalive tuning from the old `site.yml`, which is real HA-cooperation config).
- **Remove** `ansible/site.yml` (standalone provision) + the FFH client deploy; **delete** `clients/epn_requester.py` / `clients/epn_responder.py`.
- **Reconcile** `clients/dr_*.py` — keep what Plan 4's DR validation needs (`dr_mqi`, `dr_flow`, `dr_responder`); the app requester supersedes the FFH requester for the normal flow.
- **Tests** `tests/test_topology_integrity.py`, `tests/test_setups.py` — `distributed` gains `app`; `standalone`/`qm` removed (update the assertions that reference them).

## Tasks (TDD for code/topology; cold-boot-verified for the flow)

### Task 1 — the app requester client
- [ ] `clients/app_requester.py`: connect `QMPCMK` via `CONNAME(10.10.1.200(1414),10.10.2.200(1414))`, `APP.SVRCONN`, `MQCNO_RECONNECT`; loop: PUT `SVC.REQUEST` (MD.ReplyToQ=`APP.REPLY`, ReplyToQMgr=`QMPCMK`), GET `APP.REPLY` with `MATCH(CORREL_ID)` on the put MsgId; print round-trip latency; fail-loud.
- [ ] `vrg-validate`; `vrg-commit`.

### Task 2 — distributed setup gains `app`; retire legacy (TDD topology)
- [ ] Failing `tests/test_topology_integrity.py` / `tests/test_setups.py`: `distributed.groups` includes `app`; `standalone` and `qm` are **gone**.
- [ ] topology: add `app` group (`[app-client]` or rename), add to `distributed`; remove `standalone` setup, `qm` group, `qm-main` node; reconcile `client`→`app`.
- [ ] Green; `vrg-commit`.

### Task 3 — provisioning: app play + remove standalone
- [ ] `site-distributed.yml`: add `hosts: app` play — pymqi venv, deploy `app_requester.py`, the mqclient TCP-keepalive tuning (lift from `site.yml`).
- [ ] Delete `ansible/site.yml` + `clients/epn_*.py`; drop the FFH refs.
- [ ] `vrg-validate`; `vrg-commit`.

### Task 4 — full validation + cold-boot verification
- [ ] `vrg-validate` green (incl. updated integrity/setups tests).
- [ ] **Cold-boot (operator):** rebuild → nets → obs → `mqlab vm create/up/provision distributed` → `mqlab qm create distributed` → run `app_requester.py` on the app VM → a request round-trips app→QMPCMK→QMSVC→service→QMPCMK→app; inter-QM channels go RUNNING; the MQ Service panel's channel/queue tiles light up (ties off #178's traffic story). No `standalone`/QMAIN/FFH paths remain.

## Acceptance (spec §13.1, §13.6)
Full app→service→app round-trip on the `distributed` setup across two QMs joined only by sender/receiver channels; the tree contains no single-QM-masquerading-as-distributed paths. One-pass on a cold rebuild ([[cold-rebuild-acceptance-gate]]).
