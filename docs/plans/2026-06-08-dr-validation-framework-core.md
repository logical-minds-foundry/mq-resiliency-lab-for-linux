# DR/HA Validation Framework — Core Instrument Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pure-Python "brain" of the DR/HA validation framework — the ledger, the six-bucket classifier, the exposure gauge, and the reporting/self-correctness layer — fully unit-tested against synthetic fixtures, with no `pymqi` and no live lab.

**Architecture:** A small package `mqlab.dr` of focused, single-responsibility modules. Raw observations (firm ledger, DTCC god's-eye ledger, post-cutover survivor/primary-disk snapshots) are *reconciled* into per-message `MessageFacts`, which a deterministic `classify()` sorts into one of six buckets. Reporting turns a list of facts into a census, a clock-free loss window, and a `ScenarioReport`. The self-correctness check (god's-eye must equal the app ledger on a no-fault run) fails loud. This is the §10 "load-bearing wall" the spec says to build and prove first; Plan 2 (live execution) feeds it real observations later.

**Tech Stack:** Python ≥3.12 standard library only (dataclasses, enum, json, pathlib, collections). Tests: `pytest`. Package lives under the existing `src/mqlab/` (src layout, already importable as `mqlab`).

**Spec:** `docs/specs/2026-06-08-dr-ha-validation-framework-design.md` (§4.2 ledger, §4.4 exposure, §5 classification model, §7 reporting + self-correctness).

## Contents

- [Conventions](#conventions-read-once-applies-to-every-task)
- [File structure](#file-structure-created-by-this-plan)
- [Task 1: Package skeleton + core model](#task-1-package-skeleton--core-model)
- [Task 2: Ledger — append-only JSONL roundtrip](#task-2-ledger--append-only-jsonl-roundtrip)
- [Task 3: Ledger fold helpers (firm states + DTCC counts)](#task-3-ledger-fold-helpers-firm-states--dtcc-counts)
- [Task 4: Exposure gauge](#task-4-exposure-gauge)
- [Task 5: The classifier (the load-bearing wall)](#task-5-the-classifier-the-load-bearing-wall)
- [Task 6: Reconcile ledgers + snapshots into facts](#task-6-reconcile-ledgers--snapshots-into-facts)
- [Task 7: Census + clock-free loss window](#task-7-census--clock-free-loss-window)
- [Task 8: Self-correctness baseline (fail loud)](#task-8-self-correctness-baseline-fail-loud)
- [Task 9: ScenarioReport + cross-arm comparison](#task-9-scenarioreport--cross-arm-comparison)
- [Task 10: Evidence floor](#task-10-evidence-floor)
- [Task 11: End-to-end pipeline (synthetic scenarios)](#task-11-end-to-end-pipeline-synthetic-scenarios)
- [Done criteria for this plan](#done-criteria-for-this-plan)

---

## Conventions (read once, applies to every task)

- **Work inside your assigned worktree.** All paths below are relative to the repo root; use the worktree's absolute path for Read/Edit/Write and `cd` into it for Bash.
- **Git:** use `vrg-git` and `vrg-commit` (raw `git`/`gh` are blocked). Commit form:
  `vrg-commit --type <type> --scope dr --message "<msg>"`.
- **Inner TDD loop (fast feedback):** run a targeted test in the container:
  `vrg-container-run -- python -m pytest tests/<file>::<test> -v`
- **The gate before every commit:** `vrg-container-run -- vrg-validate` (the *only* validation command; it runs ruff, mypy/ty, pytest, audit). A task is not done until this is green.
- **No new dependencies.** Everything here is stdlib. Do not touch `pyproject.toml`.

---

## File structure (created by this plan)

| File | Responsibility |
|---|---|
| `src/mqlab/dr/__init__.py` | package marker + public re-exports |
| `src/mqlab/dr/model.py` | enums (`Bucket`, `MessageState`) + `MessageFacts` dataclass |
| `src/mqlab/dr/ledger.py` | `Event`, `LedgerEntry`, append-only `Ledger` (JSONL persist) + fold helpers |
| `src/mqlab/dr/exposure.py` | exposure gauge — unresolved count + `peak_exposure` replay |
| `src/mqlab/dr/classifier.py` | `classify()` — the six-bucket precedence (the wall) |
| `src/mqlab/dr/reconcile.py` | raw ledgers + snapshots → `list[MessageFacts]` |
| `src/mqlab/dr/floor.py` | provisional evidence floor + `meets_floor` checker |
| `src/mqlab/dr/report.py` | census, loss window, self-correctness, `ScenarioReport` (incl. §7 fields), cross-arm |
| `tests/test_dr_ledger.py` | ledger roundtrip + folds |
| `tests/test_dr_exposure.py` | exposure counting |
| `tests/test_dr_classifier.py` | one test per bucket + precedence |
| `tests/test_dr_reconcile.py` | facts assembly from ledgers + snapshots |
| `tests/test_dr_floor.py` | evidence-floor checker |
| `tests/test_dr_report.py` | census, window, self-correctness, report shapes (incl. §7 fields) |
| `tests/test_dr_end_to_end.py` | synthetic forced-DR + no-fault, full pipeline |

---

## Task 1: Package skeleton + core model

**Files:**
- Create: `src/mqlab/dr/__init__.py`
- Create: `src/mqlab/dr/model.py`
- Test: `tests/test_dr_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dr_model.py
from mqlab.dr.model import Bucket, MessageState, MessageFacts


def test_bucket_has_the_six_spec_buckets():
    assert {b.value for b in Bucket} == {
        "confirmed", "continued", "stranded",
        "lost_unprocessed", "ambiguous", "duplicated",
    }


def test_message_state_tristate():
    assert {s.value for s in MessageState} == {
        "never_sent", "in_pipeline", "confirmed",
    }


def test_message_facts_is_frozen_and_carries_identity():
    f = MessageFacts(
        seq=7, uuid="u7", firm_confirmed=False, dtcc_received=1,
        dtcc_replied=True, on_secondary=False, on_primary_disk=True,
    )
    assert f.seq == 7 and f.uuid == "u7"
    import dataclasses
    assert dataclasses.is_dataclass(f)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- python -m pytest tests/test_dr_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.dr'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/dr/__init__.py
"""DR/HA validation framework — pure-Python core (ledger, classifier, reporting)."""
```

```python
# src/mqlab/dr/model.py
"""Core enums and the reconciled per-message fact record."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Bucket(str, Enum):
    CONFIRMED = "confirmed"          # reply received at/before cutover
    CONTINUED = "continued"          # replicated + processed on the secondary
    STRANDED = "stranded"            # sent, unreplicated, still on the dead primary
    LOST_UNPROCESSED = "lost_unprocessed"  # sent, never reached DTCC, gone
    AMBIGUOUS = "ambiguous"          # DTCC processed it, reply lost — resend = dup risk
    DUPLICATED = "duplicated"        # DTCC received it more than once


class MessageState(str, Enum):
    NEVER_SENT = "never_sent"
    IN_PIPELINE = "in_pipeline"      # firm: local QM ACKed, no reply yet
    CONFIRMED = "confirmed"          # firm: reply matched


@dataclass(frozen=True)
class MessageFacts:
    """Everything the analyzer needs about one message to assign a bucket.

    In Plan 1 these are built from fixtures; in Plan 2 the live harness derives
    them from ledgers + queue/post-mortem snapshots.
    """

    seq: int
    uuid: str
    firm_confirmed: bool   # firm received its reply at/before cutover
    dtcc_received: int     # god's-eye: number of times DTCC received this message
    dtcc_replied: bool     # god's-eye: DTCC produced a reply
    on_secondary: bool     # present/processable on the secondary after cutover
    on_primary_disk: bool  # physically present on the failed primary (post-mortem)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- python -m pytest tests/test_dr_model.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/__init__.py src/mqlab/dr/model.py tests/test_dr_model.py
vrg-commit --type feat --scope dr --message "core model: Bucket, MessageState, MessageFacts"
```

---

## Task 2: Ledger — append-only JSONL roundtrip

**Files:**
- Create: `src/mqlab/dr/ledger.py`
- Test: `tests/test_dr_ledger.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dr_ledger.py
from mqlab.dr.ledger import Event, LedgerEntry, Ledger


def test_append_and_roundtrip_jsonl(tmp_path):
    lg = Ledger()
    lg.append(LedgerEntry(event=Event.SENT, seq=1, uuid="u1", ts=1.0))
    lg.append(LedgerEntry(event=Event.CONFIRMED, seq=1, uuid="u1", ts=2.0))
    path = tmp_path / "firm.jsonl"
    lg.write_jsonl(path)

    # one JSON object per line, append-only
    lines = path.read_text().splitlines()
    assert len(lines) == 2

    back = Ledger.read_jsonl(path)
    assert back.entries == lg.entries


def test_read_jsonl_missing_file_is_empty(tmp_path):
    back = Ledger.read_jsonl(tmp_path / "nope.jsonl")
    assert back.entries == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- python -m pytest tests/test_dr_ledger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.dr.ledger'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/dr/ledger.py
"""Append-only ledger of message events, persisted as JSONL.

The FIRM ledger records SENT (MQPUT+commit OK) and CONFIRMED (reply matched).
The DTCC god's-eye ledger records RECEIVED (per receive, counting duplicates)
and REPLIED.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path


class Event(str, Enum):
    SENT = "sent"
    CONFIRMED = "confirmed"
    RECEIVED = "received"
    REPLIED = "replied"


@dataclass(frozen=True)
class LedgerEntry:
    event: Event
    seq: int
    uuid: str
    ts: float


class Ledger:
    def __init__(self, entries: list[LedgerEntry] | None = None) -> None:
        self.entries: list[LedgerEntry] = list(entries or [])

    def append(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)

    def write_jsonl(self, path: str | Path) -> None:
        with Path(path).open("w", encoding="utf-8") as fh:
            for e in self.entries:
                row = asdict(e)
                row["event"] = e.event.value
                fh.write(json.dumps(row) + "\n")

    @classmethod
    def read_jsonl(cls, path: str | Path) -> "Ledger":
        p = Path(path)
        if not p.exists():
            return cls()
        entries: list[LedgerEntry] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            entries.append(
                LedgerEntry(
                    event=Event(row["event"]),
                    seq=int(row["seq"]),
                    uuid=str(row["uuid"]),
                    ts=float(row["ts"]),
                )
            )
        return cls(entries)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- python -m pytest tests/test_dr_ledger.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/ledger.py tests/test_dr_ledger.py
vrg-commit --type feat --scope dr --message "ledger: append-only JSONL persistence + roundtrip"
```

---

## Task 3: Ledger fold helpers (firm states + DTCC counts)

**Files:**
- Modify: `src/mqlab/dr/ledger.py` (add methods to `Ledger`)
- Test: `tests/test_dr_ledger.py` (append tests)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dr_ledger.py  (append)
from mqlab.dr.model import MessageState


def test_firm_states_folds_events():
    lg = Ledger([
        LedgerEntry(Event.SENT, 1, "u1", 1.0),
        LedgerEntry(Event.CONFIRMED, 1, "u1", 2.0),
        LedgerEntry(Event.SENT, 2, "u2", 3.0),  # no reply -> in pipeline
    ])
    states = lg.firm_states()
    assert states[1] == MessageState.CONFIRMED
    assert states[2] == MessageState.IN_PIPELINE


def test_dtcc_receive_counts_count_duplicates():
    lg = Ledger([
        LedgerEntry(Event.RECEIVED, 1, "u1", 1.0),
        LedgerEntry(Event.REPLIED, 1, "u1", 1.5),
        LedgerEntry(Event.RECEIVED, 1, "u1", 9.0),  # a second receive of the same msg
        LedgerEntry(Event.RECEIVED, 2, "u2", 2.0),
    ])
    assert lg.dtcc_receive_counts() == {1: 2, 2: 1}
    assert lg.dtcc_replied() == {1}


def test_sent_seqs_and_confirmed_seqs():
    lg = Ledger([
        LedgerEntry(Event.SENT, 1, "u1", 1.0),
        LedgerEntry(Event.CONFIRMED, 1, "u1", 2.0),
        LedgerEntry(Event.SENT, 2, "u2", 3.0),
    ])
    assert lg.sent_seqs() == {1, 2}
    assert lg.confirmed_seqs() == {1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- python -m pytest tests/test_dr_ledger.py -v`
Expected: FAIL — `AttributeError: 'Ledger' object has no attribute 'firm_states'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/dr/ledger.py  (add imports + methods)
# at top, extend imports:
from .model import MessageState

# add these methods to class Ledger:

    def sent_seqs(self) -> set[int]:
        return {e.seq for e in self.entries if e.event is Event.SENT}

    def confirmed_seqs(self, at_ts: float | None = None) -> set[int]:
        return {
            e.seq for e in self.entries
            if e.event is Event.CONFIRMED and (at_ts is None or e.ts <= at_ts)
        }

    def uuid_of(self) -> dict[int, str]:
        return {e.seq: e.uuid for e in self.entries}

    def firm_states(self) -> dict[int, MessageState]:
        sent = self.sent_seqs()
        confirmed = self.confirmed_seqs()
        states: dict[int, MessageState] = {}
        for seq in sent:
            states[seq] = (
                MessageState.CONFIRMED if seq in confirmed
                else MessageState.IN_PIPELINE
            )
        return states

    def dtcc_receive_counts(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for e in self.entries:
            if e.event is Event.RECEIVED:
                counts[e.seq] = counts.get(e.seq, 0) + 1
        return counts

    def dtcc_replied(self) -> set[int]:
        return {e.seq for e in self.entries if e.event is Event.REPLIED}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- python -m pytest tests/test_dr_ledger.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/ledger.py tests/test_dr_ledger.py
vrg-commit --type feat --scope dr --message "ledger: fold helpers for firm states and DTCC receive counts"
```

---

## Task 4: Exposure gauge

**Files:**
- Create: `src/mqlab/dr/exposure.py`
- Test: `tests/test_dr_exposure.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dr_exposure.py
from mqlab.dr.ledger import Event, LedgerEntry, Ledger
from mqlab.dr.exposure import unresolved_seqs, exposure, peak_exposure


def _firm():
    return Ledger([
        LedgerEntry(Event.SENT, 1, "u1", 1.0),
        LedgerEntry(Event.CONFIRMED, 1, "u1", 2.0),
        LedgerEntry(Event.SENT, 2, "u2", 3.0),   # in pipeline at ts>=3
        LedgerEntry(Event.SENT, 3, "u3", 4.0),   # in pipeline at ts>=4
        LedgerEntry(Event.CONFIRMED, 3, "u3", 5.0),
    ])


def test_exposure_now_counts_unresolved():
    # final state: only seq 2 never confirmed
    assert unresolved_seqs(_firm()) == {2}
    assert exposure(_firm()) == 1


def test_exposure_at_instant_uses_only_events_up_to_ts():
    # at ts=4: sent {1,2,3}, confirmed {1} -> unresolved {2,3}
    assert unresolved_seqs(_firm(), at_ts=4.0) == {2, 3}
    assert exposure(_firm(), at_ts=4.0) == 2


def test_peak_exposure_is_max_concurrent_in_flight():
    # replay of _firm(): SENT1->1, CONF1->0, SENT2->1, SENT3->2 (peak), CONF3->1
    assert peak_exposure(_firm()) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- python -m pytest tests/test_dr_exposure.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.dr.exposure'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/dr/exposure.py
"""Exposure gauge — the exact app-layer at-risk count.

exposure(T) = count of messages the firm has SENT (local QM ACKed) but not yet
had reply-CONFIRMED, as of time T. This is the ONLY exposure number we claim;
the spec (§4.4) is explicit that the replication gap is NOT message-attributable
from block-level replication, so we never estimate it.
"""
from __future__ import annotations

from .ledger import Ledger


def unresolved_seqs(firm: Ledger, at_ts: float | None = None) -> set[int]:
    sent = {
        e.seq for e in firm.entries
        if e.event.value == "sent" and (at_ts is None or e.ts <= at_ts)
    }
    confirmed = firm.confirmed_seqs(at_ts=at_ts)
    return sent - confirmed


def exposure(firm: Ledger, at_ts: float | None = None) -> int:
    return len(unresolved_seqs(firm, at_ts=at_ts))


def peak_exposure(firm: Ledger) -> int:
    """Max concurrent in-flight (SENT but not yet CONFIRMED) over the whole run,
    by replaying the timestamped ledger. At equal timestamps a SENT is counted
    before a CONFIRMED (conservative — never under-reports the peak).
    """
    events: list[tuple[float, int]] = []
    for e in firm.entries:
        if e.event.value == "sent":
            events.append((e.ts, +1))
        elif e.event.value == "confirmed":
            events.append((e.ts, -1))
    events.sort(key=lambda x: (x[0], -x[1]))  # +1 before -1 at the same ts
    inflight = peak = 0
    for _, delta in events:
        inflight += delta
        peak = max(peak, inflight)
    return peak
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- python -m pytest tests/test_dr_exposure.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/exposure.py tests/test_dr_exposure.py
vrg-commit --type feat --scope dr --message "exposure: app-layer unresolved-count gauge + peak replay"
```

---

## Task 5: The classifier (the load-bearing wall)

**Files:**
- Create: `src/mqlab/dr/classifier.py`
- Test: `tests/test_dr_classifier.py`

This is the highest-risk module. Test every bucket and the precedence order explicitly.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dr_classifier.py
from mqlab.dr.model import Bucket, MessageFacts
from mqlab.dr.classifier import classify


def _facts(**kw):
    base = dict(
        seq=1, uuid="u", firm_confirmed=False, dtcc_received=0,
        dtcc_replied=False, on_secondary=False, on_primary_disk=False,
    )
    base.update(kw)
    return MessageFacts(**base)


def test_duplicated_when_dtcc_received_twice():
    assert classify(_facts(dtcc_received=2, dtcc_replied=True)) is Bucket.DUPLICATED


def test_confirmed_when_firm_got_reply():
    assert classify(_facts(firm_confirmed=True, dtcc_received=1,
                           dtcc_replied=True, on_secondary=True)) is Bucket.CONFIRMED


def test_continued_when_replicated_to_secondary():
    assert classify(_facts(on_secondary=True, dtcc_received=1,
                           dtcc_replied=True)) is Bucket.CONTINUED


def test_ambiguous_when_dtcc_processed_but_not_replicated_or_confirmed():
    assert classify(_facts(dtcc_received=1, dtcc_replied=True)) is Bucket.AMBIGUOUS


def test_stranded_when_on_dead_primary_only():
    assert classify(_facts(on_primary_disk=True)) is Bucket.STRANDED


def test_lost_when_gone_everywhere():
    assert classify(_facts()) is Bucket.LOST_UNPROCESSED


def test_precedence_duplicated_beats_confirmed():
    # a duplicate is a duplicate even if the firm also got a reply
    assert classify(_facts(firm_confirmed=True, dtcc_received=2)) is Bucket.DUPLICATED


def test_precedence_confirmed_beats_continued():
    assert classify(_facts(firm_confirmed=True, on_secondary=True,
                           dtcc_received=1)) is Bucket.CONFIRMED


def test_precedence_ambiguous_beats_stranded():
    # reached DTCC once AND still on the primary disk -> Ambiguous (resend = dup)
    assert classify(_facts(dtcc_received=1, on_primary_disk=True)) is Bucket.AMBIGUOUS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- python -m pytest tests/test_dr_classifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.dr.classifier'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/dr/classifier.py
"""The six-bucket classifier (spec §5).

First match wins — the precedence encodes the reconciliation severity:
a duplicate is always a duplicate; a confirmed round-trip is done; otherwise
prefer the most-recoverable explanation. The ordering is part of the contract
and is locked by tests in tests/test_dr_classifier.py.
"""
from __future__ import annotations

from .model import Bucket, MessageFacts


def classify(f: MessageFacts) -> Bucket:
    if f.dtcc_received >= 2:
        return Bucket.DUPLICATED
    if f.firm_confirmed:
        return Bucket.CONFIRMED
    if f.on_secondary:
        return Bucket.CONTINUED
    if f.dtcc_received == 1:
        return Bucket.AMBIGUOUS
    if f.on_primary_disk:
        return Bucket.STRANDED
    return Bucket.LOST_UNPROCESSED


def classify_all(facts: list[MessageFacts]) -> list[tuple[MessageFacts, Bucket]]:
    return [(f, classify(f)) for f in facts]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- python -m pytest tests/test_dr_classifier.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/classifier.py tests/test_dr_classifier.py
vrg-commit --type feat --scope dr --message "classifier: six-bucket precedence (the load-bearing wall)"
```

---

## Task 6: Reconcile ledgers + snapshots into facts

**Files:**
- Create: `src/mqlab/dr/reconcile.py`
- Test: `tests/test_dr_reconcile.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dr_reconcile.py
from mqlab.dr.ledger import Event, LedgerEntry, Ledger
from mqlab.dr.reconcile import reconcile


def test_reconcile_builds_one_fact_per_sent_message():
    firm = Ledger([
        LedgerEntry(Event.SENT, 1, "u1", 1.0),
        LedgerEntry(Event.CONFIRMED, 1, "u1", 2.0),   # confirmed pre-cutover
        LedgerEntry(Event.SENT, 2, "u2", 3.0),        # in pipeline at cutover
        LedgerEntry(Event.SENT, 3, "u3", 4.0),        # stranded
    ])
    dtcc = Ledger([
        LedgerEntry(Event.RECEIVED, 1, "u1", 1.5),
        LedgerEntry(Event.REPLIED, 1, "u1", 1.8),
        LedgerEntry(Event.RECEIVED, 2, "u2", 3.5),    # DTCC got it, reply lost
        LedgerEntry(Event.REPLIED, 2, "u2", 3.8),
    ])
    facts = reconcile(
        firm, dtcc,
        secondary_present=set(),     # nothing replicated
        primary_disk_present={3},    # seq 3 still on the dead box
        cutover_ts=2.5,
    )
    by_seq = {f.seq: f for f in facts}
    assert set(by_seq) == {1, 2, 3}

    assert by_seq[1].firm_confirmed is True and by_seq[1].dtcc_received == 1
    assert by_seq[2].firm_confirmed is False and by_seq[2].dtcc_received == 1
    assert by_seq[3].dtcc_received == 0 and by_seq[3].on_primary_disk is True


def test_confirm_after_cutover_is_not_pre_cutover_confirmed():
    firm = Ledger([
        LedgerEntry(Event.SENT, 1, "u1", 1.0),
        LedgerEntry(Event.CONFIRMED, 1, "u1", 9.0),   # reply arrived AFTER cutover
    ])
    facts = reconcile(firm, Ledger(), secondary_present={1},
                      primary_disk_present=set(), cutover_ts=5.0)
    assert facts[0].firm_confirmed is False   # not confirmed at cutover
    assert facts[0].on_secondary is True      # but it did replicate
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- python -m pytest tests/test_dr_reconcile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.dr.reconcile'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/dr/reconcile.py
"""Turn raw observations into per-message MessageFacts.

Inputs the live harness (Plan 2) supplies; in Plan 1 they come from fixtures:
  - firm ledger (SENT / CONFIRMED)
  - dtcc god's-eye ledger (RECEIVED counts / REPLIED)
  - secondary_present: seqs present/processable on the secondary post-cutover
  - primary_disk_present: seqs physically on the failed primary (post-mortem)
  - cutover_ts: the instant of the fault; a reply CONFIRMED after this did not
    arrive "before cutover"
"""
from __future__ import annotations

from .ledger import Ledger
from .model import MessageFacts


def reconcile(
    firm: Ledger,
    dtcc: Ledger,
    *,
    secondary_present: set[int],
    primary_disk_present: set[int],
    cutover_ts: float,
) -> list[MessageFacts]:
    uuid_of = firm.uuid_of()
    confirmed_pre = firm.confirmed_seqs(at_ts=cutover_ts)
    dtcc_counts = dtcc.dtcc_receive_counts()
    dtcc_repl = dtcc.dtcc_replied()

    facts: list[MessageFacts] = []
    for seq in sorted(firm.sent_seqs()):
        facts.append(
            MessageFacts(
                seq=seq,
                uuid=uuid_of.get(seq, ""),
                firm_confirmed=seq in confirmed_pre,
                dtcc_received=dtcc_counts.get(seq, 0),
                dtcc_replied=seq in dtcc_repl,
                on_secondary=seq in secondary_present,
                on_primary_disk=seq in primary_disk_present,
            )
        )
    return facts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- python -m pytest tests/test_dr_reconcile.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/reconcile.py tests/test_dr_reconcile.py
vrg-commit --type feat --scope dr --message "reconcile: assemble MessageFacts from ledgers + snapshots"
```

---

## Task 7: Census + clock-free loss window

**Files:**
- Create: `src/mqlab/dr/report.py`
- Test: `tests/test_dr_report.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dr_report.py
from mqlab.dr.model import Bucket, MessageFacts
from mqlab.dr.report import census, loss_window


def _f(seq, **kw):
    base = dict(
        seq=seq, uuid=f"u{seq}", firm_confirmed=False, dtcc_received=0,
        dtcc_replied=False, on_secondary=False, on_primary_disk=False,
    )
    base.update(kw)
    return MessageFacts(**base)


def _mixed():
    return [
        _f(1, firm_confirmed=True, dtcc_received=1, dtcc_replied=True),   # Confirmed
        _f(2, on_secondary=True, dtcc_received=1, dtcc_replied=True),     # Continued
        _f(3, on_primary_disk=True),                                      # Stranded
        _f(4, dtcc_received=1, dtcc_replied=True),                        # Ambiguous
        _f(5),                                                            # Lost
    ]


def test_census_counts_each_bucket():
    c = census(_mixed())
    assert c[Bucket.CONFIRMED] == 1
    assert c[Bucket.CONTINUED] == 1
    assert c[Bucket.STRANDED] == 1
    assert c[Bucket.AMBIGUOUS] == 1
    assert c[Bucket.LOST_UNPROCESSED] == 1
    assert c[Bucket.DUPLICATED] == 0


def test_loss_window_spans_non_safe_buckets_by_sequence():
    # at-risk = stranded(3), ambiguous(4), lost(5) -> seqs 3..5, count 3
    assert loss_window(_mixed()) == (3, 5, 3)


def test_loss_window_none_when_clean():
    clean = [_f(1, firm_confirmed=True, dtcc_received=1, dtcc_replied=True)]
    assert loss_window(clean) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- python -m pytest tests/test_dr_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.dr.report'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/dr/report.py
"""Reporting: census, loss window, self-correctness, ScenarioReport, cross-arm.

The loss window is measured in SEQUENCE space (exact, clock-free), per spec §7;
wall-clock spans are context only and are not computed here.
"""
from __future__ import annotations

from collections import Counter

from .classifier import classify
from .model import Bucket, MessageFacts

# Buckets that represent a healthy outcome; everything else is "at risk".
SAFE_BUCKETS = frozenset({Bucket.CONFIRMED, Bucket.CONTINUED})


def census(facts: list[MessageFacts]) -> dict[Bucket, int]:
    counts: Counter[Bucket] = Counter()
    for f in facts:
        counts[classify(f)] += 1
    return {b: counts.get(b, 0) for b in Bucket}


def loss_window(facts: list[MessageFacts]) -> tuple[int, int, int] | None:
    at_risk = [f.seq for f in facts if classify(f) not in SAFE_BUCKETS]
    if not at_risk:
        return None
    return (min(at_risk), max(at_risk), len(at_risk))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- python -m pytest tests/test_dr_report.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/report.py tests/test_dr_report.py
vrg-commit --type feat --scope dr --message "report: bucket census + sequence-space loss window"
```

---

## Task 8: Self-correctness baseline (fail loud)

**Files:**
- Modify: `src/mqlab/dr/report.py`
- Test: `tests/test_dr_report.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dr_report.py  (append)
import pytest
from mqlab.dr.report import assert_self_correct, SelfCorrectnessError


def test_self_correct_passes_when_all_confirmed():
    clean = [
        _f(1, firm_confirmed=True, dtcc_received=1, dtcc_replied=True),
        _f(2, firm_confirmed=True, dtcc_received=1, dtcc_replied=True),
    ]
    assert_self_correct(clean)  # must not raise


def test_self_correct_raises_on_any_non_confirmed():
    dirty = [
        _f(1, firm_confirmed=True, dtcc_received=1, dtcc_replied=True),
        _f(2, dtcc_received=1, dtcc_replied=True),  # Ambiguous in a no-fault run!
    ]
    with pytest.raises(SelfCorrectnessError) as exc:
        assert_self_correct(dirty)
    assert "2" in str(exc.value)  # names the offending seq


def test_self_correct_raises_on_duplicate():
    dirty = [_f(1, firm_confirmed=True, dtcc_received=2, dtcc_replied=True)]
    with pytest.raises(SelfCorrectnessError):
        assert_self_correct(dirty)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- python -m pytest tests/test_dr_report.py -v`
Expected: FAIL — `ImportError: cannot import name 'assert_self_correct'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/dr/report.py  (append)


class SelfCorrectnessError(Exception):
    """Raised when a no-fault baseline run is not all-Confirmed — the instrument
    (or the lab) is broken and no drill result can be trusted until it is fixed.
    """


def self_correctness_violations(facts: list[MessageFacts]) -> list[MessageFacts]:
    return [f for f in facts if classify(f) is not Bucket.CONFIRMED]


def assert_self_correct(facts: list[MessageFacts]) -> None:
    bad = self_correctness_violations(facts)
    if bad:
        seqs = ", ".join(str(f.seq) for f in bad)
        raise SelfCorrectnessError(
            f"no-fault baseline has {len(bad)} non-confirmed message(s): seq {seqs}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- python -m pytest tests/test_dr_report.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/report.py tests/test_dr_report.py
vrg-commit --type feat --scope dr --message "report: self-correctness baseline check (fail loud)"
```

---

## Task 9: ScenarioReport + cross-arm comparison

**Files:**
- Modify: `src/mqlab/dr/report.py`
- Test: `tests/test_dr_report.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dr_report.py  (append)
from mqlab.dr.report import ScenarioReport, build_report, cross_arm


def test_build_report_carries_identity_census_window_and_verdict():
    facts = _mixed()
    rep = build_report(scenario_id="DR-FORCE-1", arm="C", facts=facts,
                       peak_exposure=4)
    assert rep.scenario_id == "DR-FORCE-1"
    assert rep.arm == "C"
    assert rep.peak_exposure == 4
    assert rep.window == (3, 5, 3)
    assert rep.rpo_zero is False          # there is at-risk loss
    d = rep.to_dict()
    assert d["scenario_id"] == "DR-FORCE-1"
    assert d["census"]["ambiguous"] == 1
    assert "DR-FORCE-1" in rep.to_markdown()


def test_build_report_rpo_zero_when_clean():
    clean = [_f(1, firm_confirmed=True, dtcc_received=1, dtcc_replied=True)]
    rep = build_report("HA-1", "C", clean, peak_exposure=0)
    assert rep.rpo_zero is True
    assert rep.window is None


def test_cross_arm_pairs_same_scenario():
    facts = _mixed()
    c = build_report("DR-FORCE-2", "C", facts, peak_exposure=4)
    d = build_report("DR-FORCE-2", "D", facts, peak_exposure=4)
    comp = cross_arm(c, d)
    assert comp["scenario_id"] == "DR-FORCE-2"
    assert comp["C"]["census"]["stranded"] == 1
    assert comp["D"]["census"]["stranded"] == 1


def test_report_carries_section7_honesty_fields():
    rep = build_report("DR-FORCE-1", "C", _mixed(), peak_exposure=4,
                       exposure_at_fault=3, rto_seconds=69.0,
                       intervention_required=True, integrity_anomaly=False,
                       diagnostics_captured=True)
    assert rep.exposure_at_fault == 3
    assert rep.rto_seconds == 69.0
    assert rep.intervention_required is True
    assert rep.diagnostics_captured is True
    d = rep.to_dict()
    assert d["rto_seconds"] == 69.0
    assert d["exposure_at_fault"] == 3
    assert d["intervention_required"] is True
    assert d["integrity_anomaly"] is False
    assert d["diagnostics_captured"] is True
    md = rep.to_markdown()
    assert "RTO" in md and "diagnostics" in md.lower()


def test_report_honesty_fields_default_sensibly():
    rep = build_report("HA-1", "C",
                       [_f(1, firm_confirmed=True, dtcc_received=1, dtcc_replied=True)],
                       peak_exposure=0)
    assert rep.rto_seconds is None
    assert rep.exposure_at_fault is None
    assert rep.intervention_required is False
    assert rep.integrity_anomaly is False
    assert rep.diagnostics_captured is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- python -m pytest tests/test_dr_report.py -v`
Expected: FAIL — `ImportError: cannot import name 'ScenarioReport'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/dr/report.py  (append)
from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioReport:
    scenario_id: str
    arm: str
    census: dict[Bucket, int]
    window: tuple[int, int, int] | None
    peak_exposure: int
    # §7 fields — populated by the live runner (Plan 2); default-None/False so
    # pure-core callers and tests need not supply them.
    exposure_at_fault: int | None = None
    rto_seconds: float | None = None
    intervention_required: bool = False
    integrity_anomaly: bool = False
    diagnostics_captured: bool = False

    @property
    def rpo_zero(self) -> bool:
        return self.window is None

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "arm": self.arm,
            "census": {b.value: n for b, n in self.census.items()},
            "window": list(self.window) if self.window else None,
            "peak_exposure": self.peak_exposure,
            "exposure_at_fault": self.exposure_at_fault,
            "rto_seconds": self.rto_seconds,
            "intervention_required": self.intervention_required,
            "integrity_anomaly": self.integrity_anomaly,
            "diagnostics_captured": self.diagnostics_captured,
            "rpo_zero": self.rpo_zero,
        }

    def to_markdown(self) -> str:
        lines = [
            f"### {self.scenario_id} — arm {self.arm}",
            "",
            f"- RPO 0: **{self.rpo_zero}**",
            f"- RTO: {self.rto_seconds} s",
            f"- Peak exposure: {self.peak_exposure}  (at fault: {self.exposure_at_fault})",
            f"- Intervention required: {self.intervention_required}",
            f"- Integrity anomaly: {self.integrity_anomaly}",
            f"- Diagnostics captured: {self.diagnostics_captured}",
        ]
        if self.window:
            lo, hi, n = self.window
            lines.append(f"- Loss window: messages {lo}–{hi} = {n} messages")
        lines.append("")
        lines.append("| Bucket | Count |")
        lines.append("|---|---|")
        for b in Bucket:
            lines.append(f"| {b.value} | {self.census[b]} |")
        return "\n".join(lines)


def build_report(
    scenario_id: str,
    arm: str,
    facts: list[MessageFacts],
    peak_exposure: int,
    *,
    exposure_at_fault: int | None = None,
    rto_seconds: float | None = None,
    intervention_required: bool = False,
    integrity_anomaly: bool = False,
    diagnostics_captured: bool = False,
) -> ScenarioReport:
    return ScenarioReport(
        scenario_id=scenario_id,
        arm=arm,
        census=census(facts),
        window=loss_window(facts),
        peak_exposure=peak_exposure,
        exposure_at_fault=exposure_at_fault,
        rto_seconds=rto_seconds,
        intervention_required=intervention_required,
        integrity_anomaly=integrity_anomaly,
        diagnostics_captured=diagnostics_captured,
    )


def cross_arm(c: ScenarioReport, d: ScenarioReport) -> dict:
    if c.scenario_id != d.scenario_id:
        raise ValueError(
            f"cross_arm needs the same scenario: {c.scenario_id} != {d.scenario_id}"
        )
    return {"scenario_id": c.scenario_id, "C": c.to_dict(), "D": d.to_dict()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- python -m pytest tests/test_dr_report.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/report.py tests/test_dr_report.py
vrg-commit --type feat --scope dr --message "report: ScenarioReport (incl. §7 honesty fields) + cross-arm"
```

---

## Task 10: Evidence floor

**Files:**
- Create: `src/mqlab/dr/floor.py`
- Test: `tests/test_dr_floor.py`

"RPO 0 under load" is only falsifiable against a defined floor. This module
encodes a provisional floor (TBD pending a lab-capacity check, spec §9) and a
pure checker the runner uses to gate each run and to enforce that paired arms ran
the identical `(rate, duration)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dr_floor.py
from mqlab.dr.ledger import Event, LedgerEntry, Ledger
from mqlab.dr.floor import FLOOR, FloorResult, meets_floor


def _firm_with(n, rate):
    # n SENT messages, one every 1/rate s, starting at t=0
    lg = Ledger()
    for i in range(1, n + 1):
        lg.append(LedgerEntry(Event.SENT, i, f"u{i}", (i - 1) / rate))
    return lg


def test_meets_floor_true_when_rate_duration_volume_satisfied():
    firm = _firm_with(n=7000, rate=20.0)   # 7000 msgs over ~350 s at 20/s
    res = meets_floor(firm, FLOOR)
    assert isinstance(res, FloorResult)
    assert res.ok is True
    assert res.total == 7000


def test_meets_floor_false_when_too_few_messages():
    firm = _firm_with(n=100, rate=20.0)
    res = meets_floor(firm, FLOOR)
    assert res.ok is False
    assert "total" in res.reason


def test_floor_defaults_are_the_provisional_values():
    assert FLOOR.min_rate == 20.0
    assert FLOOR.min_seconds == 300.0
    assert FLOOR.min_total == 6000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- python -m pytest tests/test_dr_floor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.dr.floor'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/dr/floor.py
"""The lab evidence floor (spec §2 goal 7).

A run counts as "RPO 0 under load" only if it clears this floor, and paired arms
must run the identical (rate, seconds) for the cross-arm comparison to be fair.
Numbers are PROVISIONAL pending a lab-capacity check (spec §9 Q2).
"""
from __future__ import annotations

from dataclasses import dataclass

from .ledger import Event, Ledger


@dataclass(frozen=True)
class Floor:
    min_rate: float
    min_seconds: float
    min_total: int


FLOOR = Floor(min_rate=20.0, min_seconds=300.0, min_total=6000)


@dataclass(frozen=True)
class FloorResult:
    ok: bool
    rate: float
    seconds: float
    total: int
    reason: str


def meets_floor(firm: Ledger, floor: Floor) -> FloorResult:
    sent = sorted(e.ts for e in firm.entries if e.event is Event.SENT)
    total = len(sent)
    seconds = (sent[-1] - sent[0]) if total >= 2 else 0.0
    rate = (total / seconds) if seconds > 0 else 0.0
    problems = []
    if total < floor.min_total:
        problems.append(f"total {total} < {floor.min_total}")
    if seconds < floor.min_seconds:
        problems.append(f"seconds {seconds:.0f} < {floor.min_seconds:.0f}")
    if rate < floor.min_rate:
        problems.append(f"rate {rate:.1f} < {floor.min_rate:.1f}")
    return FloorResult(ok=not problems, rate=rate, seconds=seconds, total=total,
                       reason="; ".join(problems) or "ok")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- python -m pytest tests/test_dr_floor.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Validate and commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/floor.py tests/test_dr_floor.py
vrg-commit --type feat --scope dr --message "floor: provisional evidence floor + pure meets_floor checker"
```

---

## Task 11: End-to-end pipeline (synthetic scenarios)

**Files:**
- Modify: `src/mqlab/dr/__init__.py` (public re-exports)
- Test: `tests/test_dr_end_to_end.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dr_end_to_end.py
from mqlab.dr import (
    Event, LedgerEntry, Ledger, reconcile, build_report, assert_self_correct,
)
from mqlab.dr.model import Bucket


def _round_trip(firm, dtcc, seq, t):
    """Helper: a clean confirmed round trip for one message at time base t."""
    uuid = f"u{seq}"
    firm.append(LedgerEntry(Event.SENT, seq, uuid, t))
    dtcc.append(LedgerEntry(Event.RECEIVED, seq, uuid, t + 0.1))
    dtcc.append(LedgerEntry(Event.REPLIED, seq, uuid, t + 0.2))
    firm.append(LedgerEntry(Event.CONFIRMED, seq, uuid, t + 0.3))


def test_no_fault_run_is_self_correct():
    firm, dtcc = Ledger(), Ledger()
    for seq in range(1, 51):
        _round_trip(firm, dtcc, seq, float(seq))
    facts = reconcile(firm, dtcc, secondary_present=set(range(1, 51)),
                      primary_disk_present=set(), cutover_ts=1_000.0)
    assert_self_correct(facts)  # the instrument agrees with itself


def test_forced_dr_produces_classified_loss():
    firm, dtcc = Ledger(), Ledger()
    # seqs 1..40 complete cleanly before cutover
    for seq in range(1, 41):
        _round_trip(firm, dtcc, seq, float(seq))
    # seq 41: DTCC processed it, reply lost (Ambiguous)
    firm.append(LedgerEntry(Event.SENT, 41, "u41", 41.0))
    dtcc.append(LedgerEntry(Event.RECEIVED, 41, "u41", 41.1))
    dtcc.append(LedgerEntry(Event.REPLIED, 41, "u41", 41.2))
    # seq 42: stranded on the dead primary, never reached DTCC
    firm.append(LedgerEntry(Event.SENT, 42, "u42", 42.0))

    facts = reconcile(
        firm, dtcc,
        secondary_present=set(range(1, 41)),  # only the completed ones replicated
        primary_disk_present={42},
        cutover_ts=41.5,
    )
    rep = build_report("DR-FORCE-1", "C", facts, peak_exposure=2)
    assert rep.rpo_zero is False
    assert rep.census[Bucket.CONFIRMED] == 40
    assert rep.census[Bucket.AMBIGUOUS] == 1
    assert rep.census[Bucket.STRANDED] == 1
    assert rep.window == (41, 42, 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- python -m pytest tests/test_dr_end_to_end.py -v`
Expected: FAIL — `ImportError: cannot import name 'reconcile' from 'mqlab.dr'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/dr/__init__.py  (replace the docstring-only file)
"""DR/HA validation framework — pure-Python core (ledger, classifier, reporting)."""
from .classifier import classify, classify_all
from .exposure import exposure, peak_exposure, unresolved_seqs
from .floor import FLOOR, FloorResult, meets_floor
from .ledger import Event, Ledger, LedgerEntry
from .model import Bucket, MessageFacts, MessageState
from .reconcile import reconcile
from .report import (
    ScenarioReport,
    SelfCorrectnessError,
    assert_self_correct,
    build_report,
    census,
    cross_arm,
    loss_window,
    self_correctness_violations,
)

__all__ = [
    "Bucket", "MessageState", "MessageFacts",
    "Event", "Ledger", "LedgerEntry",
    "classify", "classify_all", "exposure", "peak_exposure", "unresolved_seqs",
    "reconcile", "FLOOR", "FloorResult", "meets_floor",
    "census", "loss_window", "assert_self_correct", "self_correctness_violations",
    "SelfCorrectnessError", "ScenarioReport", "build_report", "cross_arm",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- python -m pytest tests/test_dr_end_to_end.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Full validation + commit**

```bash
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dr/__init__.py tests/test_dr_end_to_end.py
vrg-commit --type feat --scope dr --message "dr core: public API + end-to-end pipeline tests"
```

---

## Done criteria for this plan

- `vrg-container-run -- vrg-validate` is green.
- The classifier has an explicit test for every bucket and for the precedence edges.
- The self-correctness check passes on a clean run and raises (naming the seq) on a dirty one.
- `mqlab.dr` exposes the full public API Plan 2 will import: `Ledger`/`Event`/`LedgerEntry`, `reconcile`, `classify`, `exposure`, `peak_exposure`, `FLOOR`/`meets_floor`, `build_report` (with the §7 honesty fields), `assert_self_correct`, `cross_arm`.
- No `pymqi`, no lab, no new dependencies — this entire layer is provable on a laptop, which is exactly the point (spec §1, "evidence not proof; the instrument is the product").
