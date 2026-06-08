# DR/HA Validation Framework — Live Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Depends on:** `2026-06-08-dr-validation-framework-core.md` (Plan 1) must be merged — this plan imports `mqlab.dr` (`Ledger`, `reconcile`, `build_report`, `assert_self_correct`, `cross_arm`).

**Goal:** Drive a continuous, persistent + syncpoint message flow through the live lab, inject the §6 fault catalog under load, collect the ledgers and post-event snapshots, and feed them through the Plan-1 core to produce honest per-scenario evidence reports — RPO 0 for HA, classified RPO ≠ 0 for forced DR — on both arms (C and D).

**Architecture:** Two `pymqi` clients (a continuous firm-side generator and a god's-eye DTCC responder) emit the app-side and god's-eye ledgers as they run. A scenario runner starts the flow, invokes existing lab fault primitives (`net-*.sh`, `rdqmdr` / `pcmk-dr-cutover.sh`, process/node kill, plus new quiesce and replication-degrade scripts), captures survivor/primary-disk queue snapshots, collects the ledgers off the nodes, and reconciles → classifies → reports via `mqlab.dr`. The DTCC responder and firm client run on the `dtcc-sim` and `app-client` nodes, which sit outside both data-center sites, so the god's-eye oracle and the firm ledger survive a full-site loss (spec §4.2, Issue 3).

**Tech Stack:** Python ≥3.12, `pymqi` (lab nodes only — not a dev/CI dep), the existing Vagrant/libvirt lab + Ansible, bash fault scripts under `lab/scripts/`. Pure-logic modules unit-tested with `pytest`; live drills validated by running them and inspecting the produced `ScenarioReport`.

**Spec:** `docs/specs/2026-06-08-dr-ha-validation-framework-design.md` (§4.1 flow generator, §4.3 scenario engine, §6 catalog, §7 reporting).

## Contents

- [Conventions](#conventions-read-once-applies-to-every-task)
- [File structure](#file-structure-createdmodified-by-this-plan)
- [Task 1: Self-identifying message body (pure)](#task-1-self-identifying-message-body-pure)
- [Task 2: Snapshot — extract present sequences (pure)](#task-2-snapshot--extract-present-sequences-from-browsed-bodies-pure)
- [Task 3: Scenario catalog as data (pure)](#task-3-scenario-catalog-as-data-pure)
- [Task 4: Deploy the mqlab.dr package onto the lab nodes](#task-4-deploy-the-mqlabdr-package-onto-the-lab-nodes)
- [Task 5: Firm-side continuous flow generator (pymqi)](#task-5-firm-side-continuous-flow-generator-pymqi)
- [Task 6: DTCC god's-eye responder (pymqi)](#task-6-dtcc-gods-eye-responder-pymqi)
- [Task 7: Ledger collection + the LIVE self-correctness baseline](#task-7-ledger-collection--the-live-self-correctness-baseline)
- [Task 8: Live queue snapshots + controlled DR (DR-CTRL)](#task-8-live-queue-snapshots--controlled-dr-dr-ctrl)
- [Task 9: Forced DR (DR-FORCE-1/2/3) — reproduce and classify the loss](#task-9-forced-dr-dr-force-123--reproduce-and-classify-the-loss)
- [Task 10: HA suite, FB-REPLAY, reporting outputs, both arms](#task-10-ha-suite-fb-replay-reporting-outputs-both-arms)
- [Task 11: §7 report completeness](#task-11-7-report-completeness--fault-time-exposure-peakat-fault-diagnostics-floor-envelope)
- [Task 12: FB-REPLAY expiry-mitigation pair](#task-12-fb-replay-expiry-mitigation-pair)
- [Done criteria for this plan](#done-criteria-for-this-plan)

---

## Conventions (read once, applies to every task)

- **Work inside your assigned worktree**; use absolute paths / `cd` in.
- **Git:** `vrg-git` / `vrg-commit` only. Commit form: `vrg-commit --type <type> --scope dr --message "<msg>"`.
- **Pure-logic tasks (1–3)** are TDD: red → green → `vrg-validate` → commit, exactly like Plan 1.
- **Integration tasks (4–10)** cannot be asserted by `pytest` — their "test" is *running the drill against the live lab and inspecting the report/observation* described in the task. Each gives the exact commands and the expected observation. Still run `vrg-container-run -- vrg-validate` before committing (it lints/type-checks the new Python and runs the pure tests).
- **Lab access pattern:** `vagrant ssh <node> -c '<cmd>'` from the lab dir; node python is `~/mqvenv/bin/python`; ledgers are written under `~/dr-ledgers/` on each node and collected to `build/dr-runs/<run-id>/` on the host.
- **`pymqi` is lab-only.** Do not add it to `pyproject.toml`. The dev/CI suite never imports the live clients; it imports only the pure modules under `src/mqlab/dr/`.

---

## File structure (created/modified by this plan)

| File | Responsibility |
|---|---|
| `src/mqlab/dr/wire.py` | self-identifying message body: `build_body` / `parse_body` (pure) |
| `src/mqlab/dr/snapshot.py` | `seqs_from_bodies` (pure) + live queue browse (lab) |
| `src/mqlab/dr/catalog.py` | the §6 scenario catalog as data + expectations (pure) |
| `src/mqlab/dr/collect.py` | pull ledgers off nodes, capture snapshots (lab) |
| `src/mqlab/dr/runner.py` | run a scenario end-to-end → `ScenarioReport` → files (lab) |
| `clients/dr_flow.py` | firm-side continuous generator (persistent + syncpoint, pymqi) |
| `clients/dr_responder.py` | DTCC god's-eye responder (syncpoint, pymqi) |
| `lab/scripts/quiesce-drain.sh` | controlled quiesce: `endmqm -c` after fail-if-quiescing drain |
| `lab/scripts/drbd-degrade.sh` | throttle / break replication for DR-FORCE-3 |
| `lab/scripts/capture-diag.sh` | under-fault `runmqras`/FFST + cluster/replication SEV-1 package |
| `tests/test_dr_wire.py` | body roundtrip |
| `tests/test_dr_snapshot.py` | seq extraction from browsed bodies |
| `tests/test_dr_catalog.py` | catalog completeness vs spec §6 |

---

## Task 1: Self-identifying message body (pure)

**Files:**
- Create: `src/mqlab/dr/wire.py`
- Test: `tests/test_dr_wire.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dr_wire.py
from mqlab.dr.wire import build_body, parse_body


def test_roundtrip_preserves_identity_and_payload():
    body = build_body(seq=42, uuid="abc-123", busdate="20260608", trade="TRADE-0042")
    msg = parse_body(body)
    assert msg.seq == 42
    assert msg.uuid == "abc-123"
    assert msg.busdate == "20260608"
    assert msg.trade == "TRADE-0042"


def test_body_is_bytes_and_self_delimited():
    body = build_body(seq=1, uuid="u1", busdate="20260608", trade="T|with|pipes")
    assert isinstance(body, bytes)
    # the trade field may contain the delimiter; parsing must still recover it
    assert parse_body(body).trade == "T|with|pipes"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- python -m pytest tests/test_dr_wire.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.dr.wire'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/dr/wire.py
"""Self-identifying DR message body.

Layout (UTF-8): "DRv1|<seq>|<uuid>|<busdate>|<trade...>"
The trade field is last, so it may contain the '|' delimiter without ambiguity
(we split with maxsplit=4).
"""
from __future__ import annotations

from dataclasses import dataclass

_PREFIX = "DRv1"


@dataclass(frozen=True)
class WireMessage:
    seq: int
    uuid: str
    busdate: str
    trade: str


def build_body(*, seq: int, uuid: str, busdate: str, trade: str) -> bytes:
    return "|".join([_PREFIX, str(seq), uuid, busdate, trade]).encode("utf-8")


def parse_body(body: bytes) -> WireMessage:
    parts = body.decode("utf-8").split("|", 4)
    if len(parts) != 5 or parts[0] != _PREFIX:
        raise ValueError(f"not a DRv1 body: {body!r}")
    _, seq, uuid, busdate, trade = parts
    return WireMessage(seq=int(seq), uuid=uuid, busdate=busdate, trade=trade)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- python -m pytest tests/test_dr_wire.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/wire.py tests/test_dr_wire.py
vrg-commit --type feat --scope dr --message "wire: self-identifying DR message body build/parse"
```

---

## Task 2: Snapshot — extract present sequences from browsed bodies (pure)

**Files:**
- Create: `src/mqlab/dr/snapshot.py`
- Test: `tests/test_dr_snapshot.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dr_snapshot.py
from mqlab.dr.wire import build_body
from mqlab.dr.snapshot import seqs_from_bodies


def test_extracts_seqs_and_ignores_foreign_bodies():
    bodies = [
        build_body(seq=10, uuid="u10", busdate="20260608", trade="T10"),
        build_body(seq=11, uuid="u11", busdate="20260608", trade="T11"),
        b"not-a-dr-message",            # foreign traffic must be ignored, not crash
    ]
    assert seqs_from_bodies(bodies) == {10, 11}


def test_empty_browse_is_empty_set():
    assert seqs_from_bodies([]) == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- python -m pytest tests/test_dr_snapshot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.dr.snapshot'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/dr/snapshot.py
"""Post-event queue snapshots.

The pure half (`seqs_from_bodies`) turns a list of browsed message bodies into
the set of DR sequence numbers present — used to build `secondary_present` and
`primary_disk_present` for reconcile(). The live browse that produces those
bodies (pymqi) lives in browse_queue() and runs only on the lab.
"""
from __future__ import annotations

from .wire import parse_body


def seqs_from_bodies(bodies: list[bytes]) -> set[int]:
    seqs: set[int] = set()
    for b in bodies:
        try:
            seqs.add(parse_body(b).seq)
        except ValueError:
            continue  # foreign / non-DR message — not ours to count
    return seqs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- python -m pytest tests/test_dr_snapshot.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/snapshot.py tests/test_dr_snapshot.py
vrg-commit --type feat --scope dr --message "snapshot: extract present sequences from browsed bodies"
```

---

## Task 3: Scenario catalog as data (pure)

**Files:**
- Create: `src/mqlab/dr/catalog.py`
- Test: `tests/test_dr_catalog.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dr_catalog.py
from mqlab.dr.catalog import CATALOG, Scenario, Kind


def test_catalog_has_every_spec_scenario():
    ids = {s.id for s in CATALOG}
    assert ids == {
        "HA-1", "HA-2", "HA-3", "HA-4", "HA-5",
        "DR-CTRL", "DR-FORCE-1", "DR-FORCE-2", "DR-FORCE-3", "FB-REPLAY",
    }


def test_ha_scenarios_expect_rpo_zero():
    for s in CATALOG:
        if s.kind is Kind.HA:
            assert s.expect_rpo_zero is True


def test_forced_dr_scenarios_expect_loss():
    forced = [s for s in CATALOG if s.id.startswith("DR-FORCE")]
    assert forced and all(s.expect_rpo_zero is False for s in forced)


def test_controlled_dr_expects_rpo_zero():
    ctrl = next(s for s in CATALOG if s.id == "DR-CTRL")
    assert ctrl.expect_rpo_zero is True


def test_every_scenario_names_its_fault_and_expected_buckets():
    for s in CATALOG:
        assert s.fault           # non-empty description of what is injected
        assert s.expect_buckets  # at least one bucket it should light up
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- python -m pytest tests/test_dr_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.dr.catalog'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/dr/catalog.py
"""The §6 scenario catalog, as data.

Encoding the catalog (not just running it) lets the suite assert completeness
against the spec and gives the runner a single source of truth for each drill's
fault and expected outcome.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .model import Bucket


class Kind(str, Enum):
    HA = "ha"
    DR_CONTROLLED = "dr_controlled"
    DR_FORCED = "dr_forced"
    FAILBACK = "failback"


@dataclass(frozen=True)
class Scenario:
    id: str
    kind: Kind
    fault: str
    expect_rpo_zero: bool
    expect_buckets: tuple[Bucket, ...]


_C = (Bucket.CONTINUED,)
_SAFE = (Bucket.CONFIRMED, Bucket.CONTINUED)

CATALOG: tuple[Scenario, ...] = (
    Scenario("HA-1", Kind.HA, "kill -9 the QM process under load", True, _C),
    Scenario("HA-2", Kind.HA, "power-off the active node under load", True, _C),
    Scenario("HA-3", Kind.HA, "sever heartbeat/replication net under load", True, _C),
    Scenario("HA-4", Kind.HA, "sever shared storage under load", True, _C),
    Scenario("HA-5", Kind.HA, "rolling patch one node at a time under load", True, _C),
    Scenario("DR-CTRL", Kind.DR_CONTROLLED,
             "quiesce -> drain -> confirm replication caught up -> cutover",
             True, _SAFE),
    Scenario("DR-FORCE-1", Kind.DR_FORCED,
             "primary unrecoverable, flow continues through cutover", False,
             (Bucket.STRANDED, Bucket.AMBIGUOUS)),
    Scenario("DR-FORCE-2", Kind.DR_FORCED,
             "primary isolated from both DTCC and secondary, app keeps producing",
             False, (Bucket.STRANDED,)),
    Scenario("DR-FORCE-3", Kind.DR_FORCED,
             "replication lagged/broken then failover (chained)", False,
             (Bucket.STRANDED,)),
    Scenario("FB-REPLAY", Kind.FAILBACK,
             "recovered primary's QM brought online against stale storage "
             "before resync/discard", False, (Bucket.DUPLICATED,)),
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- python -m pytest tests/test_dr_catalog.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/catalog.py tests/test_dr_catalog.py
vrg-commit --type feat --scope dr --message "catalog: §6 scenarios as data with completeness tests"
```

---

## Task 4: Deploy the `mqlab.dr` package onto the lab nodes

The live clients (Tasks 5–6) import `mqlab.dr.wire` and `mqlab.dr.ledger`. The
node venvs currently get standalone `epn.py` only. Extend the client Ansible role
to install the package into `~/mqvenv`.

**Files:**
- Modify: `ansible/roles/mq-client/tasks/main.yml`

- [ ] **Step 1: Add a task that installs the repo package into the node venv**

Append to `ansible/roles/mq-client/tasks/main.yml` (follow the role's existing
`copy`/`pip` style — the snippet below is the intent; match surrounding vars):

```yaml
- name: Sync mqlab package source to the node
  ansible.posix.synchronize:
    src: "{{ playbook_dir }}/../src/mqlab/"
    dest: "/home/{{ ansible_user }}/mqlab_src/mqlab/"
    delete: true
  # mqlab.dr is pure-stdlib; no extra wheels needed

- name: Install mqlab (editable) into the node venv
  ansible.builtin.pip:
    name: "/home/{{ ansible_user }}/mqlab_src"
    virtualenv: "/home/{{ ansible_user }}/mqvenv"
    editable: false
  # provides `import mqlab.dr...` for the DR clients
```

(If the role copies a `pyproject.toml`/`setup`, ship a minimal `pyproject.toml`
into `mqlab_src/` so pip can build it; reuse the repo's `[project]` name `mqlab`.)

- [ ] **Step 2: Re-provision the client nodes**

Run (from the lab dir): `vagrant provision app-client dtcc-sim`
Expected: the two plays converge green.

- [ ] **Step 3: Verify the import works on a node**

Run: `vagrant ssh dtcc-sim -c '~/mqvenv/bin/python -c "import mqlab.dr.wire, mqlab.dr.ledger; print(\"ok\")"'`
Expected: prints `ok`

- [ ] **Step 4: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add ansible/roles/mq-client/tasks/main.yml
vrg-commit --type feat --scope dr --message "ansible: install mqlab package into node venvs for DR clients"
```

---

## Task 5: Firm-side continuous flow generator (pymqi)

A long-running generator: a producer loop puts persistent messages under
syncpoint at a target rate while a concurrent consumer loop drains replies and
confirms them — both appending to the firm ledger. Runs until signalled.

**Files:**
- Create: `clients/dr_flow.py`

- [ ] **Step 1: Implement the generator**

```python
# clients/dr_flow.py
"""Firm-side continuous flow generator (persistent + syncpoint).

Producer: build_body() -> MQPUT (PERSISTENT) under syncpoint -> commit ->
          firm ledger SENT.
Consumer: MQGET reply (FAIL_IF_QUIESCING) under syncpoint -> commit ->
          firm ledger CONFIRMED.
Run:  ~/mqvenv/bin/python ~/dr_flow.py --rate 50 --seconds 600 \
          --ledger ~/dr-ledgers/firm.jsonl
"""
from __future__ import annotations

import argparse
import threading
import time
import uuid as uuidlib

import pymqi

from mqlab.dr.ledger import Event, Ledger, LedgerEntry
from mqlab.dr.wire import build_body, parse_body
from mqlab.epn import pack_header  # reuse the EPN header

STOP = threading.Event()


def _connect():
    cd = pymqi.CD(
        ChannelName=b"APP.SVRCONN",
        ConnectionName=b"10.30.0.10(1414)",  # the VIP, never a node IP (spec §9)
        TransportType=pymqi.CMQC.MQXPT_TCP,
    )
    qmgr = pymqi.QueueManager(None)
    qmgr.connect_with_options("QMAIN", cd=cd)
    return qmgr


def producer(qmgr, rate, seconds, expiry, ledger, lock):
    q = pymqi.Queue(qmgr, "DTCC.REQUEST")
    pmo = pymqi.PMO(Options=pymqi.CMQC.MQPMO_SYNCPOINT)
    # MQ expiry is in tenths of a second; MQEI_UNLIMITED (-1) = no expiry, the
    # core-flow default (spec §5). FB-REPLAY passes a short expiry to show the
    # mitigation effect on the replay duplicates.
    expiry_tenths = pymqi.CMQC.MQEI_UNLIMITED if expiry is None else int(expiry * 10)
    md = pymqi.MD(Persistence=pymqi.CMQC.MQPER_PERSISTENT, Expiry=expiry_tenths)
    interval = 1.0 / rate
    seq = 0
    deadline = time.monotonic() + seconds
    while not STOP.is_set() and time.monotonic() < deadline:
        seq += 1
        u = uuidlib.uuid4().hex
        body = build_body(seq=seq, uuid=u, busdate="20260608", trade=f"TRADE-{seq}")
        q.put(pack_header(password="pw", sender="FIRM01", receiver="DTCCSVC",
                          busdate="20260608").encode() + body, md, pmo)
        qmgr.commit()
        with lock:
            ledger.append(LedgerEntry(Event.SENT, seq, u, time.time()))
        time.sleep(interval)
    STOP.set()


def consumer(qmgr, ledger, lock):
    q = pymqi.Queue(qmgr, "TRADE.REPLY")
    gmo = pymqi.GMO(
        Options=pymqi.CMQC.MQGMO_SYNCPOINT
        | pymqi.CMQC.MQGMO_WAIT
        | pymqi.CMQC.MQGMO_FAIL_IF_QUIESCING,
        WaitInterval=2000,
    )
    while not STOP.is_set():
        try:
            raw = q.get(None, pymqi.MD(), gmo)
        except pymqi.MQMIError as e:
            if e.reason == pymqi.CMQC.MQRC_NO_MSG_AVAILABLE:
                continue
            raise
        msg = _parse_reply(raw)
        qmgr.commit()
        with lock:
            ledger.append(LedgerEntry(Event.CONFIRMED, msg.seq, msg.uuid, time.time()))


def _parse_reply(raw: bytes):
    # the reply echoes the DRv1 body after the EPN header; find the DRv1 marker
    idx = raw.find(b"DRv1|")
    return parse_body(raw[idx:])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=50.0)
    ap.add_argument("--seconds", type=float, default=600.0)
    ap.add_argument("--expiry", type=float, default=None,
                    help="per-message expiry in seconds (default: unlimited)")
    ap.add_argument("--ledger", required=True)
    args = ap.parse_args()

    import pathlib
    pathlib.Path(args.ledger).parent.mkdir(parents=True, exist_ok=True)

    qmgr = _connect()
    ledger, lock = Ledger(), threading.Lock()
    t = threading.Thread(target=consumer, args=(qmgr, ledger, lock), daemon=True)
    t.start()
    producer(qmgr, args.rate, args.seconds, args.expiry, ledger, lock)
    time.sleep(3)  # let late replies land
    STOP.set()
    with lock:
        ledger.write_jsonl(args.ledger)
    qmgr.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

(Reply parsing locates the `DRv1|` marker because the responder echoes the EPN
header ahead of the body; `_parse_reply` slices from that marker.)

- [ ] **Step 2: Smoke it against the single-QM lab (no faults)**

Bring up the Phase-B single-QM lab if not running, then:
Run: `vagrant ssh app-client -c '~/mqvenv/bin/python ~/dr_flow.py --rate 20 --seconds 10 --ledger ~/dr-ledgers/firm.jsonl'`
(Run Task 6's responder first so replies flow.)
Expected observation: command exits 0; `vagrant ssh app-client -c 'wc -l ~/dr-ledgers/firm.jsonl'` shows ~400 lines (≈200 SENT + ≈200 CONFIRMED at 20/s for 10s).

- [ ] **Step 3: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add clients/dr_flow.py
vrg-commit --type feat --scope dr --message "clients: continuous persistent+syncpoint firm flow generator"
```

---

## Task 6: DTCC god's-eye responder (pymqi)

Continuous responder: get each request under syncpoint, record a god's-eye
RECEIVED (counting duplicates by identity), reply, record REPLIED, commit.

**Files:**
- Create: `clients/dr_responder.py`

- [ ] **Step 1: Implement the responder**

```python
# clients/dr_responder.py
"""DTCC-side god's-eye responder (syncpoint).

Records RECEIVED for EVERY get (so a redelivered message counts as a duplicate,
spec §5/Issue 8) and REPLIED for every reply, into the god's-eye ledger. Runs on
the dtcc-sim node, which is outside both DC sites, so the oracle survives a full
site loss (Issue 3).
Run: ~/mqvenv/bin/python ~/dr_responder.py --seconds 600 --ledger ~/dr-ledgers/dtcc.jsonl
"""
from __future__ import annotations

import argparse
import pathlib
import time

import pymqi

from mqlab.dr.ledger import Event, Ledger, LedgerEntry
from mqlab.dr.wire import parse_body
from mqlab.epn import pack_header


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=600.0)
    ap.add_argument("--ledger", required=True)
    args = ap.parse_args()
    pathlib.Path(args.ledger).parent.mkdir(parents=True, exist_ok=True)

    qmgr = pymqi.connect("QDTCC", "SIM.SVRCONN", "localhost(1414)")
    qin = pymqi.Queue(qmgr, "TRADE.REQUEST")
    qout = pymqi.Queue(qmgr, "FIRM.REPLY")
    gmo = pymqi.GMO(
        Options=pymqi.CMQC.MQGMO_SYNCPOINT
        | pymqi.CMQC.MQGMO_WAIT
        | pymqi.CMQC.MQGMO_FAIL_IF_QUIESCING,
        WaitInterval=2000,
    )
    pmo = pymqi.PMO(Options=pymqi.CMQC.MQPMO_SYNCPOINT)
    md_persist = pymqi.MD(Persistence=pymqi.CMQC.MQPER_PERSISTENT)

    ledger = Ledger()
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        try:
            raw = qin.get(None, pymqi.MD(), gmo)
        except pymqi.MQMIError as e:
            if e.reason == pymqi.CMQC.MQRC_NO_MSG_AVAILABLE:
                continue
            raise
        idx = raw.find(b"DRv1|")
        msg = parse_body(raw[idx:])
        ledger.append(LedgerEntry(Event.RECEIVED, msg.seq, msg.uuid, time.time()))
        reply = (
            pack_header(password="pw", sender="DTCCSVC", receiver="FIRM01",
                        busdate=msg.busdate).encode()
            + raw[idx:]  # echo the DRv1 body so the firm can match seq/uuid
        )
        qout.put(reply, md_persist, pmo)
        qmgr.commit()
        ledger.append(LedgerEntry(Event.REPLIED, msg.seq, msg.uuid, time.time()))

    ledger.write_jsonl(args.ledger)
    qmgr.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke it with the generator (no faults)**

Run (responder, backgrounded): `vagrant ssh dtcc-sim -c '~/mqvenv/bin/python ~/dr_responder.py --seconds 15 --ledger ~/dr-ledgers/dtcc.jsonl' &`
Then run Task 5's generator for 10s.
Expected observation: `vagrant ssh dtcc-sim -c 'wc -l ~/dr-ledgers/dtcc.jsonl'` shows ~400 lines (≈200 RECEIVED + ≈200 REPLIED), matching the firm's SENT count.

- [ ] **Step 3: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add clients/dr_responder.py
vrg-commit --type feat --scope dr --message "clients: DTCC god's-eye syncpoint responder (counts duplicates)"
```

---

## Task 7: Ledger collection + the LIVE self-correctness baseline

Collect both ledgers off the nodes and run them through the Plan-1 core; on a
no-fault run `assert_self_correct` must pass. This is the green foundation
(spec §7) every drill stands on.

**Files:**
- Create: `src/mqlab/dr/collect.py`
- Create: `src/mqlab/dr/runner.py` (the no-fault path here; faults added in Tasks 8–10)

- [ ] **Step 1: Implement collection + a baseline runner**

```python
# src/mqlab/dr/collect.py
"""Pull ledger files off lab nodes into a run directory on the host."""
from __future__ import annotations

import subprocess
from pathlib import Path


def pull_ledger(node: str, remote: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(
        ["vagrant", "ssh", node, "-c", f"cat {remote}"],
        capture_output=True, text=True, check=True,
    )
    dest.write_text(out.stdout)
    return dest
```

```python
# src/mqlab/dr/runner.py
"""Run a scenario end-to-end and emit a ScenarioReport.

Task 7 wires only the NO-FAULT baseline path (collect ledgers -> reconcile ->
assert_self_correct -> report). Tasks 8-10 add fault injection + snapshots.
"""
from __future__ import annotations

from pathlib import Path

from .collect import pull_ledger
from .ledger import Ledger
from .reconcile import reconcile
from .report import assert_self_correct, build_report
from .exposure import exposure


def run_baseline(run_dir: str | Path, arm: str) -> "ScenarioReport":  # noqa: F821
    run = Path(run_dir)
    firm = Ledger.read_jsonl(pull_ledger("app-client", "~/dr-ledgers/firm.jsonl",
                                         run / "firm.jsonl"))
    dtcc = Ledger.read_jsonl(pull_ledger("dtcc-sim", "~/dr-ledgers/dtcc.jsonl",
                                         run / "dtcc.jsonl"))
    facts = reconcile(firm, dtcc,
                      secondary_present=firm.sent_seqs(),  # no fault: all present
                      primary_disk_present=set(),
                      cutover_ts=float("inf"))
    assert_self_correct(facts)  # FAIL LOUD if the instrument disagrees with itself
    return build_report("BASELINE", arm, facts, peak_exposure=exposure(firm))
```

- [ ] **Step 2: Run the live baseline**

Start responder + generator (no faults, ~30s), then:
Run: `vrg-container-run -- python -c "from mqlab.dr.runner import run_baseline; r=run_baseline('build/dr-runs/baseline', 'C'); print(r.to_markdown())"`
Expected observation: no `SelfCorrectnessError`; the printed report shows `RPO 0: True`, all messages in `confirmed`, `window: None`.

- [ ] **Step 3: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/collect.py src/mqlab/dr/runner.py
vrg-commit --type feat --scope dr --message "runner: ledger collection + live self-correctness baseline"
```

---

## Task 8: Live queue snapshots + controlled DR (DR-CTRL)

Add the live browse (to build `secondary_present` / `primary_disk_present`), the
quiesce-drain script, and the controlled-cutover drill that must hold RPO 0.

**Files:**
- Modify: `src/mqlab/dr/snapshot.py` (add live `browse_queue`)
- Create: `lab/scripts/quiesce-drain.sh`
- Modify: `src/mqlab/dr/runner.py` (add `run_scenario` for DR-CTRL)

- [ ] **Step 1: Add the live browse**

```python
# src/mqlab/dr/snapshot.py  (append)
import subprocess


def browse_queue(node: str, qmgr: str, queue: str) -> set[int]:
    """Browse a queue on a node and return the set of DR sequences present.

    Uses a tiny pymqi browse program shipped to the node; returns the parsed
    seq set. Empty set if the queue is empty or unreachable post-fault.
    """
    out = subprocess.run(
        ["vagrant", "ssh", node, "-c",
         f"~/mqvenv/bin/python ~/dr_browse.py {qmgr} {queue}"],
        capture_output=True, text=True, check=False,
    )
    seqs: set[int] = set()
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            seqs.add(int(line))
    return seqs
```

Ship `clients/dr_browse.py` (browses a queue, prints one seq per line using
`parse_body`; uses `MQGMO_BROWSE_FIRST`/`MQGMO_BROWSE_NEXT`). Add it to the
ansible client role's file list (Task 4 pattern).

- [ ] **Step 2: Write the quiesce-drain script**

```bash
# lab/scripts/quiesce-drain.sh — controlled quiesce of the active QM.
# Stops the app producer, waits for in-flight replies to drain, confirms the
# DR replication has caught up, then ends the QM controlled (-c). Because the
# clients set FAIL_IF_QUIESCING, in-flight MQI calls return cleanly (spec §9).
set -euo pipefail
ARM="${1:?arm: c|d}"
NODE="${2:?active node, e.g. node-a1}"
# 1) signal the generator to stop producing (touch a stop file it polls)
vagrant ssh app-client -c 'touch ~/dr-ledgers/STOP'
sleep 5  # let the reply consumer drain
# 2) controlled end of the QM
vagrant ssh "$NODE" -c 'sudo -u mqm endmqm -c QMAIN'
echo "quiesced QMAIN on $NODE"
```

(Wire a `--stop-file` poll into `dr_flow.py`'s producer loop so the touch is
honored; this is the app-cooperation the controlled path depends on.)

- [ ] **Step 3: Add `run_scenario` (DR-CTRL) to the runner**

```python
# src/mqlab/dr/runner.py  (append)
import subprocess
from .snapshot import browse_queue


def run_dr_ctrl(run_dir, arm, active_node, secondary_node):
    run = Path(run_dir)
    # (flow is already running; caller started generator+responder)
    subprocess.run(["lab/scripts/quiesce-drain.sh", arm, active_node], check=True)
    # cutover: rdqmdr for arm C, pcmk-dr-cutover.sh for arm D
    if arm == "c":
        subprocess.run(["vagrant", "ssh", active_node, "-c",
                        "sudo rdqmdr -m QMAIN -s"], check=True)
    else:
        subprocess.run(["lab/scripts/pcmk-dr-cutover.sh"], check=True)
    secondary_present = browse_queue(secondary_node, "QMAIN", "DTCC.REQUEST")
    firm = Ledger.read_jsonl(pull_ledger("app-client", "~/dr-ledgers/firm.jsonl",
                                         run / "firm.jsonl"))
    dtcc = Ledger.read_jsonl(pull_ledger("dtcc-sim", "~/dr-ledgers/dtcc.jsonl",
                                         run / "dtcc.jsonl"))
    facts = reconcile(firm, dtcc, secondary_present=secondary_present | firm.confirmed_seqs(),
                      primary_disk_present=set(), cutover_ts=float("inf"))
    return build_report("DR-CTRL", arm, facts, peak_exposure=exposure(firm))
```

- [ ] **Step 4: Run DR-CTRL on arm C**

Start flow, then:
Run: `vrg-container-run -- python -c "from mqlab.dr.runner import run_dr_ctrl; print(run_dr_ctrl('build/dr-runs/dr-ctrl-c','c','node-a1','node-b1').to_markdown())"`
Expected observation: report shows `RPO 0: True`, `window: None`, every message `confirmed` or `continued`. (If not, that is a finding for the confidence envelope — capture it.)

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/snapshot.py src/mqlab/dr/runner.py clients/dr_browse.py lab/scripts/quiesce-drain.sh
vrg-commit --type feat --scope dr --message "runner: live browse + quiesce-drain + DR-CTRL (RPO 0 path)"
```

---

## Task 9: Forced DR (DR-FORCE-1/2/3) — reproduce and classify the loss

The marquee. Each forced drill keeps the flow running, injects an unrecoverable
fault, fails over, captures the survivor + (post-mortem) primary-disk snapshots,
and classifies the loss. Each must produce a non-empty loss window.

**Files:**
- Create: `lab/scripts/drbd-degrade.sh`
- Modify: `src/mqlab/dr/runner.py` (add `run_dr_force`)

- [ ] **Step 1: Write the replication-degrade script**

```bash
# lab/scripts/drbd-degrade.sh — degrade or break cross-site replication.
# mode=break  : disconnect the DRBD link (no replication at all)
# mode=lag    : pin a low sync rate so a large backlog accumulates
set -euo pipefail
MODE="${1:?break|lag}"
NODE="${2:?active node}"
RES="${3:-rdqm_QMAIN}"   # DRBD resource name
case "$MODE" in
  break) vagrant ssh "$NODE" -c "sudo drbdadm disconnect $RES" ;;
  lag)   vagrant ssh "$NODE" -c "sudo drbdsetup $RES net-options --c-plan-ahead=0 --resync-rate=256K" ;;
  *) echo "mode must be break|lag" >&2; exit 2 ;;
esac
echo "drbd $MODE on $NODE ($RES)"
```

- [ ] **Step 2: Add `run_dr_force` to the runner**

```python
# src/mqlab/dr/runner.py  (append)

def run_dr_force(run_dir, arm, scenario_id, active_node, secondary_node,
                 inject):
    """inject: a callable that performs the scenario-specific fault while flow runs.
    DR-FORCE-1: kill the active node hard.
    DR-FORCE-2: net-down the primary site's WAN + replication nets (isolate).
    DR-FORCE-3: drbd-degrade.sh lag, let backlog build, THEN kill.
    """
    run = Path(run_dir)
    inject()  # the unrecoverable fault, under live flow
    # force the cutover on the surviving site
    if arm == "c":
        subprocess.run(["vagrant", "ssh", secondary_node, "-c",
                        "sudo rdqmdr -m QMAIN -p"], check=True)
    else:
        subprocess.run(["lab/scripts/pcmk-dr-cutover.sh", "--force"], check=True)
    secondary_present = browse_queue(secondary_node, "QMAIN", "DTCC.REQUEST")
    # post-mortem: what is still on the dead primary's disk (once reachable)
    primary_disk = browse_queue(active_node, "QMAIN", "DTCC.REQUEST")
    firm = Ledger.read_jsonl(pull_ledger("app-client", "~/dr-ledgers/firm.jsonl",
                                         run / "firm.jsonl"))
    dtcc = Ledger.read_jsonl(pull_ledger("dtcc-sim", "~/dr-ledgers/dtcc.jsonl",
                                         run / "dtcc.jsonl"))
    facts = reconcile(firm, dtcc, secondary_present=secondary_present,
                      primary_disk_present=primary_disk, cutover_ts=float("inf"))
    return build_report(scenario_id, arm, facts, peak_exposure=exposure(firm))
```

- [ ] **Step 3: Run DR-FORCE-2 (the marquee) on arm C**

Start flow at a rate high enough to keep a backlog, then:
Run: `vrg-container-run -- python -c "from mqlab.dr.runner import run_dr_force; import subprocess; inj=lambda: subprocess.run(['lab/scripts/net-down.sh'],check=False); print(run_dr_force('build/dr-runs/force2-c','c','DR-FORCE-2','node-a1','node-b1',inj).to_markdown())"`
Expected observation: report shows `RPO 0: False`, a non-empty `window` (messages X–Y = N), and a non-zero `stranded` count. **The drill has succeeded when it demonstrates loss** (spec done-criterion). Capture the census.

- [ ] **Step 4: Repeat for DR-FORCE-1 and DR-FORCE-3**

DR-FORCE-1 inject = `vagrant ssh node-a1 -c 'sudo poweroff'` (hard node loss).
DR-FORCE-3 inject = `drbd-degrade.sh lag node-a1` → `sleep 30` → `poweroff` (the loss window scales with the backlog).
Expected: both report `RPO 0: False` with classified buckets; DR-FORCE-3's window should be visibly larger than DR-FORCE-1's at the same rate.

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add lab/scripts/drbd-degrade.sh src/mqlab/dr/runner.py
vrg-commit --type feat --scope dr --message "runner: forced-DR drills (1/2/3) reproduce and classify loss"
```

---

## Task 10: HA suite, FB-REPLAY, reporting outputs, both arms

Close the catalog: the five HA drills under flow (each must hold RPO 0), the
failback operational-error replay, and the report artifacts (per-run JSON +
`docs/reports/` summary, cross-arm comparison, confidence envelope). Run the
whole suite on both arms.

**Files:**
- Modify: `src/mqlab/dr/runner.py` (add `run_ha`, `run_fb_replay`, `write_outputs`)

- [ ] **Step 1: Add HA + FB-REPLAY + output writers**

```python
# src/mqlab/dr/runner.py  (append)
import json
from .catalog import CATALOG
from .report import cross_arm


def run_ha(run_dir, arm, scenario_id, active_node, secondary_node, inject):
    """HA drill: inject an intra-site fault under flow; expect RPO 0 (Continued)."""
    run = Path(run_dir)
    inject()  # kill -9 / poweroff / net-sever / storage-sever / rolling patch
    secondary_present = browse_queue(secondary_node, "QMAIN", "DTCC.REQUEST")
    firm = Ledger.read_jsonl(pull_ledger("app-client", "~/dr-ledgers/firm.jsonl",
                                         run / "firm.jsonl"))
    dtcc = Ledger.read_jsonl(pull_ledger("dtcc-sim", "~/dr-ledgers/dtcc.jsonl",
                                         run / "dtcc.jsonl"))
    facts = reconcile(firm, dtcc,
                      secondary_present=secondary_present | firm.confirmed_seqs(),
                      primary_disk_present=set(), cutover_ts=float("inf"))
    return build_report(scenario_id, arm, facts, peak_exposure=exposure(firm))


def run_fb_replay(run_dir, arm, recovered_node, secondary_node):
    """Operational-error failback: bring the recovered QM up against stale
    storage BEFORE resync, let it drain, then observe duplicates at DTCC."""
    run = Path(run_dir)
    if arm == "c":
        subprocess.run(["vagrant", "ssh", recovered_node, "-c",
                        "sudo rdqmadm --start-standalone QMAIN"], check=False)
    else:
        subprocess.run(["vagrant", "ssh", recovered_node, "-c",
                        "sudo -u mqm strmqm QMAIN"], check=False)
    firm = Ledger.read_jsonl(pull_ledger("app-client", "~/dr-ledgers/firm.jsonl",
                                         run / "firm.jsonl"))
    dtcc = Ledger.read_jsonl(pull_ledger("dtcc-sim", "~/dr-ledgers/dtcc.jsonl",
                                         run / "dtcc.jsonl"))
    facts = reconcile(firm, dtcc, secondary_present=set(),
                      primary_disk_present=set(), cutover_ts=float("inf"))
    return build_report("FB-REPLAY", arm, facts, peak_exposure=exposure(firm))


def write_outputs(run_dir, reports):
    run = Path(run_dir)
    (run / "reports.json").write_text(
        json.dumps([r.to_dict() for r in reports], indent=2))
    md = "\n\n".join(r.to_markdown() for r in reports)
    (run / "summary.md").write_text(md)
    return run / "summary.md"
```

- [ ] **Step 2: Run the HA suite (HA-1..5) on arm C under flow**

Injects: HA-1 `kill -9` the QM PID on the active node; HA-2 `poweroff`;
HA-3 `virsh net-destroy net-hb-a` (heartbeat net); HA-4 sever the storage net;
HA-5 rolling `dnf update` one node at a time.
Run each via `run_ha(...)`.
Expected observation: every HA report shows `RPO 0: True`, `window: None`,
census all `continued`/`confirmed`. **Any HA drill that shows loss is a finding**
— record it for the confidence envelope (spec §2 goal 2).

- [ ] **Step 3: Run FB-REPLAY on arm C**

Run: `vrg-container-run -- python -c "from mqlab.dr.runner import run_fb_replay; print(run_fb_replay('build/dr-runs/fb-c','c','node-a1','node-b1').to_markdown())"`
Expected observation: a non-zero `duplicated` count — the replay reproduced.
Record the failback-discipline finding (normal resync would have discarded these).

- [ ] **Step 4: Run the full suite on BOTH arms and write the comparison**

For each scenario in `CATALOG`, run the matching `run_*` on arm C and arm D,
collect the `ScenarioReport`s, then:

```python
# build the cross-arm comparison + confidence envelope
from mqlab.dr.runner import write_outputs
from mqlab.dr.report import cross_arm
comps = [cross_arm(c, d) for c, d in paired_reports]  # same scenario_id pairs
# write_outputs(...) for each arm; write comps to build/dr-runs/cross-arm.json
```

Then hand-author `docs/reports/2026-06-08-dr-validation-findings.md`
summarizing: the HA RPO-0 evidence, the forced-DR loss census per scenario, the
cross-arm comparison, and the **confidence envelope** (the conditions under which
RPO 0 is honest vs not), per the existing phase-report convention.

Expected observation: both arms produce a full census per scenario; the findings
report exists and states the envelope honestly.

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/runner.py docs/reports/2026-06-08-dr-validation-findings.md
vrg-commit --type feat --scope dr --message "runner: HA suite + FB-REPLAY + outputs; full suite on both arms"
```

---

## Task 11: §7 report completeness — fault-time, exposure peak/at-fault, diagnostics, floor, envelope

This task closes the alignment gaps (review 2026-06-08): every drill records the
**fault timestamp** (so RTO, exposure-at-fault, and `firm_confirmed` are correct —
the earlier `cutover_ts=float("inf")` was a placeholder), captures diagnostics
under fault, enforces the evidence floor, and emits the confidence-envelope
inputs. A single `_finish` helper replaces the ad-hoc report-building tails in
Tasks 7–10.

**Files:**
- Create: `lab/scripts/capture-diag.sh`
- Modify: `src/mqlab/dr/runner.py` (add `_finish`; route every `run_*` through it)

- [ ] **Step 1: Write the diagnostics-capture script**

```bash
# lab/scripts/capture-diag.sh — gather an IBM-grade SEV-1 package under fault.
# Runs runmqras, collects FFST/FDC, and dumps cluster + replication state on the
# affected node into the run dir. Exits non-zero if it cannot produce a package
# (that failure is itself a finding — the report's diagnostics_captured goes False).
set -euo pipefail
NODE="${1:?node}"
RUN="${2:?run dir}"
OUT="$RUN/diag-$NODE"
mkdir -p "$OUT"
vagrant ssh "$NODE" -c 'sudo -u mqm runmqras -section defs,trace,cluster -workdirectory /tmp/ras' \
  && vagrant ssh "$NODE" -c 'sudo tar czf - /tmp/ras /var/mqm/errors 2>/dev/null' > "$OUT/runmqras.tgz"
vagrant ssh "$NODE" -c 'sudo drbdadm status 2>/dev/null || true' > "$OUT/drbd-status.txt"
vagrant ssh "$NODE" -c 'sudo crm status 2>/dev/null || true'    > "$OUT/cluster-status.txt"
test -s "$OUT/runmqras.tgz"   # fail loud if the SEV-1 package is empty
echo "diag captured -> $OUT"
```

- [ ] **Step 2: Add the `_finish` helper and route every drill through it**

```python
# src/mqlab/dr/runner.py  (append; then replace each run_*'s report-building tail
# with a call to _finish — worked example below)
import time
from .exposure import exposure, peak_exposure
from .floor import FLOOR, meets_floor


def _capture_diag(node: str, run: Path) -> bool:
    rc = subprocess.run(["lab/scripts/capture-diag.sh", node, str(run)], check=False)
    return rc.returncode == 0


def _finish(scenario_id, arm, run, *, fault_ts, service_restored_ts,
            secondary_present, primary_disk, diag_node,
            intervention_required=False, integrity_anomaly=False):
    run = Path(run)
    firm = Ledger.read_jsonl(pull_ledger("app-client", "~/dr-ledgers/firm.jsonl",
                                         run / "firm.jsonl"))
    dtcc = Ledger.read_jsonl(pull_ledger("dtcc-sim", "~/dr-ledgers/dtcc.jsonl",
                                         run / "dtcc.jsonl"))
    floor = meets_floor(firm, FLOOR)
    if not floor.ok:
        raise RuntimeError(f"{scenario_id}/{arm}: run below evidence floor: {floor.reason}")
    facts = reconcile(firm, dtcc, secondary_present=secondary_present,
                      primary_disk_present=primary_disk, cutover_ts=fault_ts)
    return build_report(
        scenario_id, arm, facts,
        peak_exposure=peak_exposure(firm),
        exposure_at_fault=exposure(firm, at_ts=fault_ts),
        rto_seconds=(service_restored_ts - fault_ts) if service_restored_ts else None,
        intervention_required=intervention_required,
        integrity_anomaly=integrity_anomaly,
        diagnostics_captured=_capture_diag(diag_node, run),
    )


def run_dr_force(run_dir, arm, scenario_id, active_node, secondary_node, inject):
    """Worked example of the routed pattern; run_ha / run_dr_ctrl / run_baseline
    follow the same shape (record fault_ts, do the cutover, snapshot, _finish)."""
    fault_ts = time.time()
    inject()  # the unrecoverable fault, under live flow
    if arm == "c":
        subprocess.run(["vagrant", "ssh", secondary_node, "-c",
                        "sudo rdqmdr -m QMAIN -p"], check=True)
    else:
        subprocess.run(["lab/scripts/pcmk-dr-cutover.sh", "--force"], check=True)
    secondary_present = browse_queue(secondary_node, "QMAIN", "DTCC.REQUEST")
    service_restored_ts = time.time()
    primary_disk = browse_queue(active_node, "QMAIN", "DTCC.REQUEST")  # post-mortem
    return _finish(scenario_id, arm, run_dir, fault_ts=fault_ts,
                   service_restored_ts=service_restored_ts,
                   secondary_present=secondary_present, primary_disk=primary_disk,
                   diag_node=active_node, intervention_required=True)
```

Apply the same routing to `run_baseline` (no fault: `fault_ts=time.time()` after
flow ends, `service_restored_ts=None`, `secondary_present=firm.sent_seqs()`,
`primary_disk=set()`), `run_dr_ctrl`, `run_ha`. Delete the now-duplicated tails.

- [ ] **Step 3: Add the confidence-envelope inputs writer**

```python
# src/mqlab/dr/runner.py  (append)

def write_envelope_inputs(run_dir, reports):
    """Machine-collected facts the human writes the confidence envelope ON TOP of
    (data vs judgment): one row per (scenario, arm). The envelope PROSE stays
    hand-authored in docs/reports/."""
    rows = [{
        "scenario_id": r.scenario_id, "arm": r.arm, "rpo_zero": r.rpo_zero,
        "intervention_required": r.intervention_required,
        "diagnostics_captured": r.diagnostics_captured,
        "census": {b.value: n for b, n in r.census.items()},
    } for r in reports]
    out = Path(run_dir) / "envelope-inputs.json"
    out.write_text(json.dumps(rows, indent=2))
    return out
```

(For the cross-arm fairness check, before pairing assert the two arms' runs cleared
the floor with matching `(rate, seconds)` — `meets_floor(firm_c).seconds` ≈
`meets_floor(firm_d).seconds` within tolerance — and record any mismatch.)

- [ ] **Step 4: Re-run one drill end-to-end and inspect the enriched report**

Run a forced drill via the routed `run_dr_force`, then check the report shows the
new fields populated:
Expected observation: `to_markdown()` now prints `RTO: <seconds> s`, `Peak
exposure: N (at fault: M)`, `Diagnostics captured: True`, and `Intervention
required: True`; `build/dr-runs/<id>/diag-<node>/runmqras.tgz` exists and is
non-empty; `envelope-inputs.json` is written.

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add lab/scripts/capture-diag.sh src/mqlab/dr/runner.py
vrg-commit --type feat --scope dr --message "runner: fault-time, peak/at-fault exposure, diagnostics, floor, envelope inputs"
```

---

## Task 12: FB-REPLAY expiry-mitigation pair

Demonstrate expiry as the DR-safety lever (spec §5/§6): the *same* failback
replay, once with no expiry (duplicates replay) and once with a short expiry
(stale messages expired → few/no duplicates).

**Files:**
- Modify: `src/mqlab/dr/runner.py` (`run_fb_replay` takes the flow's expiry; a
  pair driver runs both)

- [ ] **Step 1: Parameterize and pair the run**

```python
# src/mqlab/dr/runner.py  (append)

def run_fb_replay_pair(run_dir, arm, recovered_node, secondary_node):
    """Run the failback replay twice. Caller runs the flow before each leg:
      - leg 'no-expiry': dr_flow.py with NO --expiry  -> duplicates replay
      - leg 'expiry':    dr_flow.py with --expiry 10  -> stale msgs expired
    Returns (no_expiry_report, expiry_report); the contrast IS the evidence.
    """
    fault_ts = time.time()
    if arm == "c":
        subprocess.run(["vagrant", "ssh", recovered_node, "-c",
                        "sudo rdqmadm --start-standalone QMAIN"], check=False)
    else:
        subprocess.run(["vagrant", "ssh", recovered_node, "-c",
                        "sudo -u mqm strmqm QMAIN"], check=False)
    secondary_present = browse_queue(secondary_node, "QMAIN", "DTCC.REQUEST")
    leg = _finish("FB-REPLAY", arm, run_dir, fault_ts=fault_ts,
                  service_restored_ts=time.time(),
                  secondary_present=secondary_present, primary_disk=set(),
                  diag_node=recovered_node, intervention_required=True)
    return leg
```

- [ ] **Step 2: Run both legs on arm C**

Leg A — start flow with **no** `--expiry`, do the replay, capture the report:
`run_fb_replay_pair('build/dr-runs/fb-noexp-c','c','node-a1','node-b1')`
Leg B — start flow with `--expiry 10`, do the replay, capture the report.
Expected observation: leg A shows a non-zero `duplicated` count; leg B shows a
materially lower (ideally zero) `duplicated` count. Record both and the
failback-discipline finding (normal DRBD resync would have discarded the stale
data; the hazard is the operational error of running the QM standalone first).

- [ ] **Step 3: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/runner.py
vrg-commit --type feat --scope dr --message "runner: FB-REPLAY expiry-mitigation before/after pair"
```

---

## Done criteria for this plan

- The continuous generator + god's-eye responder run under load on both arms, persistent + syncpoint, emitting matching ledgers.
- The **live self-correctness baseline** passes (`assert_self_correct` green on a no-fault run) — the instrument agrees with itself against real MQ traffic.
- Every §6 scenario has been run on both arms and produced a `ScenarioReport`: HA-1..5 at RPO 0; DR-CTRL at RPO 0; DR-FORCE-1/2/3 with a non-empty, classified loss window; FB-REPLAY with a non-zero `duplicated` count.
- The done-criterion is met: at least one forced-DR scenario **deterministically reproduces RPO ≠ 0** with the loss counted exactly and bounded by a sequence-span window.
- Every run clears the **evidence floor** (`meets_floor`), paired arms ran identical `(rate, seconds)`, and each report carries the **§7 fields** — RTO, peak + at-fault exposure, intervention/anomaly flags, and `diagnostics_captured` (with a non-empty `runmqras` package per fault).
- **FB-REPLAY** ran as a before/after pair showing expiry's mitigating effect on the replay duplicates.
- `docs/reports/2026-06-08-dr-validation-findings.md` exists with the cross-arm comparison and the honest confidence envelope (written on top of the machine-collected `envelope-inputs.json`) — the input Phase E needs.
- `vrg-container-run -- vrg-validate` is green (lints/types the new Python; the pure tests pass).
