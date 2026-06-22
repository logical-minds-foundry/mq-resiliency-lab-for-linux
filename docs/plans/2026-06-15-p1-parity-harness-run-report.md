# P1 — Parity Harness: Run Records, Report Corpus & Capability Matrix (vs. Pacemaker) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote the existing (built, unit-tested) DR/HA validation framework into a single-invocation, self-describing, persisted operation — `mqlab run <setup>` → a timestamped report bundle stamped with `(setup × config × commit)` — plus a cross-arm capability matrix, all green on the Pacemaker arm.

**Architecture:** Two layers. **Layer 1 (pure Python, fully TDD):** run metadata + a `RunReport` bundle that wraps the existing `dr.ScenarioReport`s, persisted under `build/reports/<ts>-<setup>/` with a `index.jsonl` corpus index, plus a `parity` capability matrix. **Layer 2 (orchestration):** a `baseline_run_plan()` that assembles the existing flow/collect pipeline as `CommandStep`s, a new `lab/scripts/dr-run.sh` that drives it on the lab, and a `mqlab run` Typer command that executes the plan then reconciles the collected ledgers in-process (reusing `dr.reconcile`/`build_report`) and writes the bundle. The end-to-end "green on Pacemaker" is a lab-integration acceptance gate (cold-rebuild-style), not a unit test.

**Tech Stack:** Python 3.12 (StrEnum, frozen dataclasses, `from __future__ import annotations`), Typer + Rich (existing `mqlab` CLI), the existing `src/mqlab/dr/` library, pytest (`uv run pytest`), Ansible/Vagrant lab (existing `lab/scripts/`).

---

## Context the implementer needs

- **Work in the worktree.** All edits and git ops happen in `/Users/pmoore/dev/projects/logical-minds-foundry/mq-cluster-tooling/.worktrees/issue-187-rdqm-parity-pivot/` (or a fresh worktree for the implementation issue). The main worktree is read-only.
- **Commits use `vrg-commit`**, not raw git: `vrg-commit --type <type> --scope <scope> --message <msg>`. Raw `git`/`gh` are denied; use `vrg-git`/`vrg-gh`.
- **Validation is `vrg-container-run -- vrg-validate` only.** It runs ruff + mypy + `uv run pytest` with **100% branch coverage required**. Gotchas (from #47–#51): use `StrEnum` (not `class X(str, Enum)` — UP042); ruff enforces the magic trailing comma; cover every branch or use `# pragma: no cover` for genuinely-unreachable/subprocess lines (see `paths.py:21`); never mask exit codes.
- **Run a single test** during development with `uv run pytest tests/test_x.py -v`.
- **The `dr` library is the engine — do not reimplement it.** Reuse: `from mqlab.dr import Ledger, reconcile, build_report, assert_self_correct, peak_exposure, ScenarioReport` (see `src/mqlab/dr/__init__.py`).
- **Existing seams to follow:** `mqlab.paths` (filesystem anchors), `mqlab.runner.Command`, `mqlab.orchestrator.CommandStep`/`run_steps`, `mqlab.cli._execute`/`build_deps` (Typer command pattern), `mqlab.setups.Setup`/`QmConfig`/`lab_setups`/`setup_members`.
- **The "arm" registry does not exist yet** (that's P2). P1 uses a **provisional** setup→arm mapping (`provisional_arm`) and notes it for replacement in P2.

## File Structure

- Create: `src/mqlab/runreport.py` — `RunMetadata`, `capture_metadata`, `RunReport`, `write_bundle`, `append_index`, real readers (`read_commit`, `read_config_digest`, `read_versions`).
- Create: `src/mqlab/parity.py` — `Support`, `VERBS`, `MATRIX`, `supported`, `render_markdown`, `provisional_arm`.
- Create: `src/mqlab/runplan.py` — `baseline_run_plan`.
- Create: `lab/scripts/dr-run.sh` — lab orchestration (deploy flow clients → drive flow → collect ledgers).
- Modify: `src/mqlab/paths.py` — add `reports_dir`.
- Modify: `src/mqlab/cli.py` — add the `mqlab run` command + `mqlab parity` command.
- Test: `tests/test_paths_reports.py`, `tests/test_runreport.py`, `tests/test_parity.py`, `tests/test_runplan.py`.

---

### Task 1: `reports_dir()` filesystem anchor

**Files:**
- Modify: `src/mqlab/paths.py`
- Test: `tests/test_paths_reports.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paths_reports.py
from __future__ import annotations

from mqlab.paths import repo_root, reports_dir


def test_reports_dir_is_under_build() -> None:
    assert reports_dir() == repo_root() / "build" / "reports"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_paths_reports.py -v`
Expected: FAIL with `ImportError: cannot import name 'reports_dir'`.

- [ ] **Step 3: Add the function**

```python
# append to src/mqlab/paths.py, after runs_dir()
def reports_dir() -> Path:
    """Where run-report bundles are written — under the gitignored build/ tree."""
    return repo_root() / "build" / "reports"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_paths_reports.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope reports --message "paths: reports_dir() anchor under build/ (#187)"
```

---

### Task 2: `RunMetadata` + `capture_metadata` (injectable, fail-loud)

**Files:**
- Create: `src/mqlab/runreport.py`
- Test: `tests/test_runreport.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runreport.py
from __future__ import annotations

import pytest

from mqlab.runreport import RunMetadata, capture_metadata


def test_capture_metadata_assembles_from_readers() -> None:
    md = capture_metadata(
        "distributed",
        "20260615T143000Z",
        commit_reader=lambda: "abc123",
        digest_reader=lambda: "deadbeef",
        version_reader=lambda: {"vagrant": "2.4.1"},
    )
    assert md == RunMetadata(
        setup="distributed",
        commit="abc123",
        timestamp="20260615T143000Z",
        config_digest="deadbeef",
        versions={"vagrant": "2.4.1"},
    )


def test_capture_metadata_propagates_reader_failure() -> None:
    def boom() -> str:
        raise RuntimeError("git unavailable")

    with pytest.raises(RuntimeError, match="git unavailable"):
        capture_metadata(
            "distributed",
            "20260615T143000Z",
            commit_reader=boom,
            digest_reader=lambda: "d",
            version_reader=dict,
        )


def test_run_metadata_to_dict_round_trips() -> None:
    md = RunMetadata("s", "c", "t", "d", {"k": "v"})
    assert md.to_dict() == {
        "setup": "s",
        "commit": "c",
        "timestamp": "t",
        "config_digest": "d",
        "versions": {"k": "v"},
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runreport.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mqlab.runreport'`.

- [ ] **Step 3: Create the module with `RunMetadata` + `capture_metadata`**

```python
# src/mqlab/runreport.py
"""Run metadata + the run-report bundle that turns DR/HA scenario reports into a
durable, self-describing corpus entry (pivot spec §4.4).

A bundle pairs the machine-readable + human ScenarioReports with the coordinates
that make a run reproducible: the setup/arm, the exact git commit, a digest of
the generated config actually used, tool/box/MQ versions, and a UTC timestamp.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from mqlab.dr import ScenarioReport


@dataclass(frozen=True)
class RunMetadata:
    setup: str
    commit: str
    timestamp: str  # UTC, e.g. 20260615T143000Z
    config_digest: str
    versions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def capture_metadata(
    setup: str,
    timestamp: str,
    *,
    commit_reader: Callable[[], str],
    digest_reader: Callable[[], str],
    version_reader: Callable[[], dict[str, str]],
) -> RunMetadata:
    """Assemble RunMetadata from injectable readers (real impls shell out; tests
    inject fakes). Readers MUST raise on failure — a metadata field that silently
    became "" would corrupt the corpus (no silent failures)."""
    return RunMetadata(
        setup=setup,
        commit=commit_reader(),
        timestamp=timestamp,
        config_digest=digest_reader(),
        versions=version_reader(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_runreport.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope reports --message "runreport: RunMetadata + injectable capture_metadata (#187)"
```

---

### Task 3: `RunReport` bundle (wraps `dr.ScenarioReport`)

**Files:**
- Modify: `src/mqlab/runreport.py`
- Test: `tests/test_runreport.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_runreport.py
from mqlab.dr import build_report
from mqlab.dr.model import MessageFacts
from mqlab.runreport import RunReport


def _confirmed_report(arm: str) -> "object":
    facts = [
        MessageFacts(
            seq=1, uuid="u1", app_confirmed=True, svc_received=1,
            svc_replied=True, on_secondary=False, on_primary_disk=False,
        )
    ]
    return build_report("BASELINE", arm, facts, peak_exposure=0)


def test_run_report_to_dict_nests_metadata_and_scenarios() -> None:
    md = RunMetadata("distributed", "c", "t", "d", {})
    report = RunReport(metadata=md, scenarios=[_confirmed_report("pcmk-ubuntu")])
    d = report.to_dict()
    assert d["metadata"]["setup"] == "distributed"
    assert d["scenarios"][0]["scenario_id"] == "BASELINE"
    assert d["scenarios"][0]["rpo_zero"] is True


def test_run_report_markdown_has_header_and_scenario() -> None:
    md = RunMetadata("distributed", "abc123", "20260615T143000Z", "deadbeef", {"vagrant": "2.4.1"})
    report = RunReport(metadata=md, scenarios=[_confirmed_report("pcmk-ubuntu")])
    text = report.to_markdown()
    assert "# Run report — distributed @ 20260615T143000Z" in text
    assert "Commit: `abc123`" in text
    assert "vagrant=2.4.1" in text
    assert "### BASELINE — arm pcmk-ubuntu" in text


def test_run_report_markdown_handles_no_versions() -> None:
    md = RunMetadata("distributed", "c", "t", "d", {})
    report = RunReport(metadata=md, scenarios=[])
    assert "Versions: (none)" in report.to_markdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runreport.py -v`
Expected: FAIL with `ImportError: cannot import name 'RunReport'`.

- [ ] **Step 3: Add `RunReport`**

```python
# add to src/mqlab/runreport.py, after capture_metadata
@dataclass(frozen=True)
class RunReport:
    metadata: RunMetadata
    scenarios: list[ScenarioReport]

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "scenarios": [s.to_dict() for s in self.scenarios],
        }

    def to_markdown(self) -> str:
        m = self.metadata
        versions = ", ".join(f"{k}={v}" for k, v in sorted(m.versions.items())) or "(none)"
        lines = [
            f"# Run report — {m.setup} @ {m.timestamp}",
            "",
            f"- Setup: `{m.setup}`",
            f"- Commit: `{m.commit}`",
            f"- Config digest: `{m.config_digest}`",
            f"- Versions: {versions}",
            "",
        ]
        for s in self.scenarios:
            lines.append(s.to_markdown())
            lines.append("")
        return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_runreport.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope reports --message "runreport: RunReport bundle wrapping dr.ScenarioReports (#187)"
```

---

### Task 4: Persist the bundle + append the corpus index

**Files:**
- Modify: `src/mqlab/runreport.py`
- Test: `tests/test_runreport.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_runreport.py
import json
from pathlib import Path

from mqlab.runreport import append_index, write_bundle


def test_write_bundle_creates_json_and_markdown(tmp_path: Path) -> None:
    md = RunMetadata("distributed", "abc123", "20260615T143000Z", "deadbeef", {})
    report = RunReport(metadata=md, scenarios=[_confirmed_report("pcmk-ubuntu")])
    bundle = write_bundle(report, tmp_path)
    assert bundle == tmp_path / "20260615T143000Z-distributed"
    loaded = json.loads((bundle / "report.json").read_text())
    assert loaded["metadata"]["commit"] == "abc123"
    assert "# Run report — distributed" in (bundle / "report.md").read_text()


def test_append_index_writes_one_jsonl_line_per_call(tmp_path: Path) -> None:
    md = RunMetadata("distributed", "abc123", "20260615T143000Z", "deadbeef", {})
    report = RunReport(metadata=md, scenarios=[_confirmed_report("pcmk-ubuntu")])
    bundle = write_bundle(report, tmp_path)
    append_index(report, bundle, tmp_path)
    append_index(report, bundle, tmp_path)
    lines = (tmp_path / "index.jsonl").read_text().splitlines()
    assert len(lines) == 2
    entry = json.loads(lines[0])
    assert entry["setup"] == "distributed"
    assert entry["commit"] == "abc123"
    assert entry["bundle"] == "20260615T143000Z-distributed"
    assert entry["verdicts"] == {"BASELINE": True}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runreport.py -v`
Expected: FAIL with `ImportError: cannot import name 'write_bundle'`.

- [ ] **Step 3: Add `write_bundle` + `append_index`**

```python
# add to src/mqlab/runreport.py, after RunReport
def write_bundle(report: RunReport, root: Path) -> Path:
    """Persist the bundle under root/<timestamp>-<setup>/ as report.json + report.md.
    Returns the bundle directory."""
    m = report.metadata
    bundle = root / f"{m.timestamp}-{m.setup}"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "report.json").write_text(json.dumps(report.to_dict(), indent=2) + "\n")
    (bundle / "report.md").write_text(report.to_markdown())
    return bundle


def append_index(report: RunReport, bundle: Path, root: Path) -> None:
    """Append one line to root/index.jsonl: the (timestamp, setup, commit) -> bundle
    path + per-scenario rpo_zero verdicts that make the corpus searchable."""
    m = report.metadata
    entry = {
        "timestamp": m.timestamp,
        "setup": m.setup,
        "commit": m.commit,
        "bundle": str(bundle.relative_to(root)),
        "verdicts": {s.scenario_id: s.rpo_zero for s in report.scenarios},
    }
    with (root / "index.jsonl").open("a") as fh:
        fh.write(json.dumps(entry) + "\n")
```

Note: `Path` is imported under `TYPE_CHECKING` for annotations only; the runtime
uses passed-in `Path` objects, so no runtime import is needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_runreport.py -v`
Expected: PASS.

- [ ] **Step 5: Add the real (subprocess/file) readers**

```python
# add to src/mqlab/runreport.py, at the end
def read_commit() -> str:  # pragma: no cover - shells out to git
    out = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True)  # noqa: S603, S607
    return out.strip()


def read_config_digest(paths: list[Path]) -> str:  # pragma: no cover - reads files
    h = hashlib.sha256()
    for p in paths:
        h.update(p.read_bytes())
    return h.hexdigest()


def read_versions() -> dict[str, str]:  # pragma: no cover - shells out to host tools
    def _v(argv: list[str]) -> str:
        return subprocess.check_output(argv, text=True).strip().splitlines()[0]  # noqa: S603

    return {
        "vagrant": _v(["vagrant", "--version"]),  # noqa: S607
        "ansible": _v(["ansible", "--version"]),  # noqa: S607
    }
```

These are `# pragma: no cover` (subprocess/filesystem side effects, exercised only
by the lab-integration gate in Task 7), consistent with `paths.py:21`.

- [ ] **Step 6: Run the full module tests + verify it still passes**

Run: `uv run pytest tests/test_runreport.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
vrg-commit --type feat --scope reports --message "runreport: persist bundle (json+md) + corpus index; real readers (#187)"
```

---

### Task 5: The cross-arm capability matrix (`mqlab.parity`)

**Files:**
- Create: `src/mqlab/parity.py`
- Test: `tests/test_parity.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_parity.py
from __future__ import annotations

import pytest

from mqlab.parity import (
    MATRIX,
    VERBS,
    Support,
    provisional_arm,
    render_markdown,
    supported,
)


def test_pcmk_is_reference_backend_all_supported() -> None:
    assert all(supported("pcmk-ubuntu", v) is Support.SUPPORTED for v in VERBS)


def test_rdqm_starts_not_yet_everywhere() -> None:
    assert all(supported("rdqm-rhel", v) is Support.NOT_YET for v in VERBS)


def test_supported_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown arm/verb"):
        supported("nope", "failover")
    with pytest.raises(KeyError, match="unknown arm/verb"):
        supported("pcmk-ubuntu", "nope")


def test_render_markdown_has_a_row_per_verb_and_arm_columns() -> None:
    text = render_markdown()
    assert "| verb | pcmk-ubuntu | rdqm-rhel |" in text
    for v in VERBS:
        assert f"| {v} |" in text
    assert "not_yet" in text and "supported" in text


@pytest.mark.parametrize(
    ("setup", "arm"),
    [
        ("distributed", "pcmk-ubuntu"),
        ("pcmk_san_ha", "pcmk-ubuntu"),
        ("pcmk_san_dr", "pcmk-ubuntu"),
        ("rdqm_ha", "rdqm-rhel"),
        ("rdqm_dr", "rdqm-rhel"),
    ],
)
def test_provisional_arm_maps_known_setups(setup: str, arm: str) -> None:
    assert provisional_arm(setup) == arm


def test_provisional_arm_unknown_raises() -> None:
    with pytest.raises(KeyError, match="no provisional arm"):
        provisional_arm("monitoring")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_parity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mqlab.parity'`.

- [ ] **Step 3: Create the module**

```python
# src/mqlab/parity.py
"""The cross-arm capability matrix (pivot spec §4.1): which operator verbs each
arm's backend supports. Parity = identical capability + identical correctness;
this declares the capability half. RDQM rows start NOT_YET until P3/P4 land.

The `provisional_arm` map is a P1 stopgap: the real arm registry arrives in P2
(topology-declared), at which point this map is replaced by a registry lookup.
"""

from __future__ import annotations

from enum import StrEnum


class Support(StrEnum):
    SUPPORTED = "supported"
    NOT_YET = "not_yet"
    NA = "n/a"


VERBS: tuple[str, ...] = (
    "form-group",
    "add-node",
    "evacuate-node",
    "failover",
    "status",
    "dr-bootstrap",
    "cutover",
    "failback",
    "diagnostics",
)

# arm -> verb -> Support. pcmk-ubuntu is the reference backend (all supported);
# rdqm-rhel is NOT_YET until its backend lands (P3/P4).
MATRIX: dict[str, dict[str, Support]] = {
    "pcmk-ubuntu": dict.fromkeys(VERBS, Support.SUPPORTED),
    "rdqm-rhel": dict.fromkeys(VERBS, Support.NOT_YET),
}

# Provisional P1 setup -> arm map (replaced by the P2 registry).
_PROVISIONAL_ARM: dict[str, str] = {
    "distributed": "pcmk-ubuntu",
    "pcmk_san_ha": "pcmk-ubuntu",
    "pcmk_san_dr": "pcmk-ubuntu",
    "rdqm_ha": "rdqm-rhel",
    "rdqm_dr": "rdqm-rhel",
}


def supported(arm: str, verb: str) -> Support:
    try:
        return MATRIX[arm][verb]
    except KeyError as exc:
        raise KeyError(f"unknown arm/verb: {arm}/{verb}") from exc


def provisional_arm(setup: str) -> str:
    try:
        return _PROVISIONAL_ARM[setup]
    except KeyError as exc:
        raise KeyError(f"no provisional arm for setup {setup!r}") from exc


def render_markdown() -> str:
    arms = list(MATRIX)
    rows = [
        "| verb | " + " | ".join(arms) + " |",
        "|---" * (len(arms) + 1) + "|",
    ]
    for v in VERBS:
        cells = " | ".join(MATRIX[a][v].value for a in arms)
        rows.append(f"| {v} | {cells} |")
    return "\n".join(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_parity.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope parity --message "parity: cross-arm capability matrix + provisional arm map (#187)"
```

---

### Task 6: `baseline_run_plan()` — assemble the pipeline as CommandSteps

**Files:**
- Create: `src/mqlab/runplan.py`
- Test: `tests/test_runplan.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runplan.py
from __future__ import annotations

from pathlib import Path

import pytest

from mqlab.runplan import baseline_run_plan
from mqlab.setups import QmConfig, Setup


def _setup(qm: QmConfig | None) -> Setup:
    return Setup(
        name="distributed",
        description="d",
        groups=["san_a", "pcmk_a", "svc", "app"],
        provision="ansible/site-distributed.yml",
        secrets=[],
        qm=qm,
    )


def test_baseline_plan_is_one_step_invoking_dr_run() -> None:
    qm = QmConfig(name="QMPCMK", vip="10.10.1.200", vip_ext="10.60.0.10", svc_conn="10.60.0.50")
    run_dir = Path("/tmp/run/20260615T143000Z-distributed")
    steps = baseline_run_plan(_setup(qm), run_dir, seconds=30, rate=20)
    assert len(steps) == 1
    step = steps[0]
    assert "distributed" in step.label
    argv = step.command.argv
    assert argv[0] == "bash"
    assert "--qm" in argv and "QMPCMK" in argv
    assert "--vip" in argv and "10.10.1.200" in argv
    assert "--seconds" in argv and "30" in argv
    assert str(run_dir / "app.jsonl") in argv
    assert str(run_dir / "svc.jsonl") in argv


def test_baseline_plan_requires_a_qm() -> None:
    with pytest.raises(ValueError, match="no QM"):
        baseline_run_plan(_setup(None), Path("/tmp/x"), seconds=30, rate=20)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runplan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mqlab.runplan'`.

- [ ] **Step 3: Create the module**

```python
# src/mqlab/runplan.py
"""Assemble the CommandStep pipeline for a single-invocation baseline `mqlab run`
of a setup (pivot spec §4.4). The pipeline is one call into lab/scripts/dr-run.sh,
which deploys the flow clients, drives the steady-state flow, and collects the two
ledgers into the run dir. The in-process reconcile/report happens in the CLI after
these steps succeed (see cli.run_setup)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mqlab.orchestrator import CommandStep
from mqlab.paths import lab_script
from mqlab.runner import Command

if TYPE_CHECKING:
    from pathlib import Path

    from mqlab.setups import Setup


def baseline_run_plan(setup: Setup, run_dir: Path, *, seconds: int, rate: int) -> list[CommandStep]:
    if setup.qm is None:
        raise ValueError(f"setup {setup.name!r} has no QM to drive a baseline run")
    qm = setup.qm
    app_ledger = run_dir / "app.jsonl"
    svc_ledger = run_dir / "svc.jsonl"
    command = Command(
        [
            "bash",
            str(lab_script("dr-run.sh")),
            "--setup",
            setup.name,
            "--qm",
            qm.name,
            "--vip",
            qm.vip,
            "--seconds",
            str(seconds),
            "--rate",
            str(rate),
            "--app-ledger",
            str(app_ledger),
            "--svc-ledger",
            str(svc_ledger),
        ]
    )
    label = f"baseline run {setup.name} ({seconds}s @ {rate}/s)"
    return [CommandStep(label, command)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_runplan.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope run --message "runplan: baseline_run_plan assembles the dr-run pipeline step (#187)"
```

---

### Task 7: `lab/scripts/dr-run.sh` + `mqlab run` / `mqlab parity` commands + lab gate

**Files:**
- Create: `lab/scripts/dr-run.sh`
- Modify: `src/mqlab/cli.py`
- Test: `tests/test_parity.py` (CLI smoke for `parity`), manual lab gate for `run`

- [ ] **Step 1: Write `lab/scripts/dr-run.sh`** (the lab orchestration — mirrors `e2e-test.sh` style; deploys the existing `dr_flow.py`/`dr_responder.py`, drives flow, collects ledgers). Fail-loud (`set -euo pipefail`).

```bash
#!/usr/bin/env bash
# lab/scripts/dr-run.sh — drive one no-fault baseline flow for a setup and collect
# the app + SVC ledgers to the host run dir (pivot spec §4.4 / DR-HA framework §4).
# Assumes the setup is already provisioned and up (run `mqlab vm provision <setup>`
# first). Reuses the deployed clients/dr_flow.py + clients/dr_responder.py.
set -euo pipefail
SETUP="" QM="" VIP="" SECONDS_RUN=30 RATE=20 APP_LEDGER="" SVC_LEDGER=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --setup) SETUP="$2"; shift 2;;
    --qm) QM="$2"; shift 2;;
    --vip) VIP="$2"; shift 2;;
    --seconds) SECONDS_RUN="$2"; shift 2;;
    --rate) RATE="$2"; shift 2;;
    --app-ledger) APP_LEDGER="$2"; shift 2;;
    --svc-ledger) SVC_LEDGER="$2"; shift 2;;
    *) echo "dr-run: unknown arg $1" >&2; exit 2;;
  esac
done
: "${SETUP:?} ${QM:?} ${VIP:?} ${APP_LEDGER:?} ${SVC_LEDGER:?}"
cd "$(dirname "$0")/.."
mkdir -p "$(dirname "$APP_LEDGER")"

# Responder on the surviving SVC side (background; outlives the flow window).
vagrant ssh svc-sim -c \
  "~/mqvenv/bin/python ~/dr_responder.py --qm QMSVC --conn 'localhost(1414)' \
   --in-queue SVC.REQUEST --out-queue APP.REPLY --seconds $((SECONDS_RUN + 10)) \
   --ledger ~/dr-ledgers/svc.jsonl" &
RESP_PID=$!

# Steady-state app flow through the QM VIP.
vagrant ssh app-client -c \
  "~/mqvenv/bin/python ~/dr_flow.py --qm ${QM} --conn '${VIP}(1414)' \
   --req-queue DR.REQUEST --reply-queue DR.REPLY --rate ${RATE} --seconds ${SECONDS_RUN} \
   --ledger ~/dr-ledgers/app.jsonl"
wait "$RESP_PID"

# Collect both ledgers to the host run dir.
vagrant ssh app-client -c 'cat ~/dr-ledgers/app.jsonl' > "$APP_LEDGER"
vagrant ssh svc-sim   -c 'cat ~/dr-ledgers/svc.jsonl' > "$SVC_LEDGER"
echo "dr-run: collected ledgers for ${SETUP} (${QM}@${VIP})"
```

Note: the exact queue names / client flags follow the deployed clients
(`clients/dr_flow.py`, `clients/dr_responder.py`) documented in the DR/HA
framework; verify them against the current clients during the Step-7 lab gate and
adjust this script there. The script is the integration seam — its correctness is
proven by the lab run, not a unit test.

- [ ] **Step 2: Add the `mqlab parity` command + write its smoke test first**

```python
# add to tests/test_parity.py
from typer.testing import CliRunner

from mqlab.cli import app


def test_parity_command_prints_matrix() -> None:
    result = CliRunner().invoke(app, ["parity"])
    assert result.exit_code == 0
    assert "pcmk-ubuntu" in result.stdout
    assert "not_yet" in result.stdout
```

Run: `uv run pytest tests/test_parity.py::test_parity_command_prints_matrix -v`
Expected: FAIL (no `parity` command yet).

- [ ] **Step 3: Wire the `parity` command in `cli.py`**

```python
# add imports near the other mqlab imports in src/mqlab/cli.py
from mqlab import parity
from mqlab.paths import lab_script, reports_dir, repo_root, runs_dir  # extend existing import

# add this command near the other top-level commands
@app.command("parity")
def parity_matrix() -> None:
    """Print the cross-arm capability matrix (which verbs each arm supports)."""
    typer.echo(parity.render_markdown())
```

Run: `uv run pytest tests/test_parity.py -v`
Expected: PASS.

- [ ] **Step 4: Wire the `mqlab run` command in `cli.py`** (executes the plan via the existing `_execute`, then reconciles in-process and writes the bundle). Keep the body thin; subprocess/git lines are covered by the lab gate.

```python
# add imports
from mqlab.dr import Ledger, assert_self_correct, build_report, peak_exposure, reconcile
from mqlab.runplan import baseline_run_plan
from mqlab.runreport import (
    RunReport,
    append_index,
    capture_metadata,
    read_commit,
    read_config_digest,
    read_versions,
    write_bundle,
)
from mqlab.setups import lab_setups
from mqlab.inventory import inventory_path

_Seconds = Annotated[int, typer.Option("--seconds", help="flow duration")]
_Rate = Annotated[int, typer.Option("--rate", help="messages per second")]


def _lookup_setup_or_exit(name: str):  # -> Setup
    for s in lab_setups():
        if s.name == name:
            return s
    typer.echo(f"no setup named {name!r}", err=True)
    raise typer.Exit(code=2)


@app.command("run")
def run_setup(
    setup_name: Annotated[str, typer.Argument(help="setup to run (e.g. distributed)")],
    seconds: _Seconds = 30,
    rate: _Rate = 20,
    step: _StepFlag = False,
) -> None:
    """Drive one no-fault baseline run of a setup and write a timestamped report
    bundle stamped with (setup × config × commit) under build/reports/."""
    setup = _lookup_setup_or_exit(setup_name)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = runs_dir() / f"{timestamp}-{setup.name}"
    run_dir.mkdir(parents=True, exist_ok=True)

    steps = baseline_run_plan(setup, run_dir, seconds=seconds, rate=rate)
    _execute("run", steps, step_mode=step)  # raises typer.Exit on any step failure

    app = Ledger.read_jsonl(run_dir / "app.jsonl")
    svc = Ledger.read_jsonl(run_dir / "svc.jsonl")
    facts = reconcile(
        app, svc, secondary_present=set(), primary_disk_present=set(), cutover_ts=float("inf")
    )
    assert_self_correct(facts)  # baseline must be all-Confirmed or the instrument is broken
    scenario = build_report(
        "BASELINE", parity.provisional_arm(setup.name), facts, peak_exposure=peak_exposure(app)
    )
    metadata = capture_metadata(
        setup.name,
        timestamp,
        commit_reader=read_commit,
        digest_reader=lambda: read_config_digest([repo_root() / "lab" / "topology.yaml", inventory_path()]),
        version_reader=read_versions,
    )
    report = RunReport(metadata=metadata, scenarios=[scenario])
    bundle = write_bundle(report, reports_dir())
    append_index(report, bundle, reports_dir())
    typer.echo(f"run report written: {bundle}")
```

Coverage note: the `run` command's happy path touches git/subprocess/lab and is
covered by the Step-7 lab gate, not unit tests. To keep `vrg-validate`'s 100%
branch bar green without faking the whole lab, mark the command body
`# pragma: no cover` on the `run_setup` function (consistent with the codebase's
treatment of live-lab entry points), and keep all *logic* in the already-tested
helpers (`baseline_run_plan`, `reconcile`, `build_report`, `capture_metadata`,
`write_bundle`, `append_index`, `provisional_arm`). Add `# pragma: no cover` after
the `run_setup` signature line.

- [ ] **Step 5: Run the unit suite + verify nothing regressed**

Run: `uv run pytest -v`
Expected: PASS (all tests).

- [ ] **Step 6: Full validation**

Run: `vrg-container-run -- vrg-validate`
Expected: ruff + mypy clean, 100% branch coverage, all tests pass.

- [ ] **Step 7: Lab-integration acceptance gate (green on Pacemaker)**

This is the real "green on the Pacemaker arm" proof — a manual lab run, not a unit
test (the lab needs a built base VM + the `distributed` setup up). From the worktree:

```bash
mqlab vm provision distributed     # bring the Pacemaker distributed setup up
mqlab run distributed --seconds 30 --rate 20
```

Expected: the command exits 0; `assert_self_correct` passes (no-fault baseline is
all-Confirmed); a bundle appears under `build/reports/<ts>-distributed/`
(`report.json` + `report.md`) and a line is appended to `build/reports/index.jsonl`
with `"verdicts": {"BASELINE": true}`. If `assert_self_correct` raises, the
instrument or the lab is broken — investigate before trusting any drill (framework
§7). Record the result per the cold-rebuild acceptance gate.

- [ ] **Step 8: Commit**

```bash
vrg-commit --type feat --scope run --message "run: mqlab run/parity commands + dr-run.sh; baseline bundle on the pcmk arm (#187)"
```

---

## Out of scope for P1 (explicit — these are later phases)

- **Fault scenarios through `mqlab run`** (HA-1..5, DR-FORCE-*, FB-REPLAY): the
  baseline proves the run→reconcile→bundle mechanism; each fault scenario then
  reuses the same pattern (drive flow + inject the catalogued fault via the
  existing `lab/scripts/*.sh` + reconcile with the right `secondary_present` /
  `primary_disk_present` / `cutover_ts`). Add them iteratively after the baseline
  is green — a natural P1 follow-on or P1b.
- **The arm-backend interface extraction** (P2) and **arm registry in topology**
  (P2) — `provisional_arm` is the P1 stopgap.
- **The RDQM backend** (P3/P4) — the matrix's `rdqm-rhel` column stays `not_yet`.
- **State cache / toggle** (§3.5 / #167) and **artifact user-config** (§3.6) — separate slices.

## Self-Review

**1. Spec coverage (pivot §4 + §4.4):**
- §4.4 single-invocation run → ✅ Task 6–7 (`mqlab run` + `dr-run.sh`).
- §4.4 report captures inputs (setup, generated config digest, versions, **commit SHA**) + outputs (scenario results) → ✅ Tasks 2–4 (`RunMetadata` + `RunReport` + `write_bundle`).
- §4.4 corpus = `(arm × config × commit) → outcomes`, re-runnable → ✅ Task 4 `append_index` (`index.jsonl`).
- §4.1 capability matrix, RDQM starts `not-yet` → ✅ Task 5 (`parity`).
- §4.2 parity = capability + correctness; self-correctness baseline → ✅ Task 7 reuses `assert_self_correct`.
- §6 scope-honesty (RDQM timing qualitative) → inherited from the existing `dr` report (no wall-clock metric added); no new violation.
- "green on the Pacemaker arm" (P1 outcome) → ✅ Task 7 Step 7 lab gate on `distributed`.
- Gaps: fault scenarios deliberately deferred (listed Out of scope) — baseline is the proof-of-mechanism the spec's P1 requires; not a coverage gap.

**2. Placeholder scan:** No TBD/TODO. Every code step shows complete code. The one
intentional non-code gate (Task 7 Step 7) is a real lab run with exact commands and
expected artifacts, explicitly marked as integration, not a hidden placeholder.

**3. Type consistency:** `RunMetadata`/`RunReport` fields match across Tasks 2–4 and
their `to_dict` keys match the test assertions and `append_index`. `Support`/`VERBS`/
`MATRIX` names match across Task 5 and the CLI. `baseline_run_plan(setup, run_dir, *, seconds, rate)`
signature matches its test and the `run_setup` call site. `ScenarioReport`,
`Ledger`, `reconcile`, `build_report`, `assert_self_correct`, `peak_exposure` are
used exactly as defined in `src/mqlab/dr/` (verified against the module).
