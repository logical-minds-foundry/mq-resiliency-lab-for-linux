# Cockpit Panel Command Tooltips — Implementation Plan (Phase A: Native HA spike)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every collector-backed panel on the Native HA cockpit board (`lab-nativeha-cluster`) a native Grafana ⓘ hover tooltip showing the read-only command behind its data plus read-only diagnostics, sourced from one drift-checked catalog.

**Architecture:** A new `tooltips.py` holds a single `TOOLTIPS` catalog (logical-panel key → command templates) and a `render_description(key, qm)` renderer. `clusterboard.py` gains a `QM_NATIVE` constant (de-hardcoding the board's PromQL), an optional `description=` on its `matrix()`/`_stat()` builders, and wiring that attaches rendered tooltips to the five ① band tiles, the two ② instance matrices, and the ③ CRR card. A test binds each catalog "behind" command to the collector's real argv so the read half cannot drift.

**Tech Stack:** Python 3 (stdlib + dataclasses), pytest, the existing `mqlab` package. No new dependencies.

## Global Constraints

- **Spec:** `docs/specs/2026-06-23-cockpit-panel-command-tooltips-design.md` — this plan implements **Phase A only**.
- **QM name is a variable, never a literal** — the catalog stores `{qm}` templates; `QM_NATIVE = "QMNATIVE"` is the single source of truth (spec §2.6).
- **No mutating commands** in any tooltip — read + diagnose only (spec §1).
- **Fail loud** — unknown catalog key raises `KeyError`; no empty-string fallback (spec §9).
- **100% branch coverage** is enforced by `vrg-validate` — every new branch must be exercised both ways (spec §10).
- **Validation:** the only validation command is `vrg-container-run -- vrg-validate`. Do not run individual linters.
- **Git:** use `vrg-git` / `vrg-commit` (raw `git`/`gh` are denied). All work happens in the worktree `.worktrees/issue-313-cockpit-tooltips/` on branch `feature/313-cockpit-tooltips`. `cd` into the worktree for every command.
- **Investigate commands** are read-only diagnostics; the concrete strings in this plan are verified against IBM Docs 9.4 / `docs/reference/` and the live `QMNATIVE` during Task 2's verification step before the catalog is finalized (spec §8).

---

### Task 1: `QM_NATIVE` constant + de-hardcode the Native HA board PromQL

Pure refactor — no behavior change. Replaces the scattered `"QMNATIVE"` string literals in the Native HA board exprs with one constant, so the QM name has a single source of truth. The rendered PromQL text is byte-identical, so existing board tests stay green.

**Files:**
- Modify: `src/mqlab/clusterboard.py` (add the constant near the top of the module; update the Native HA exprs — `nativeha_status_band` at clusterboard.py:435 and `_nativeha_integrity_expr`, plus any other `"QMNATIVE"` literal in the NHA path)
- Test: `tests/test_clusterboard.py`

**Interfaces:**
- Produces: `clusterboard.QM_NATIVE: str` (value `"QMNATIVE"`) — consumed by Tasks 3 and 5 and by `test_tooltips.py`.

- [ ] **Step 1: Find every `QMNATIVE` PromQL literal in the Native HA path**

Run: `cd .worktrees/issue-313-cockpit-tooltips && grep -n '"QMNATIVE"' src/mqlab/clusterboard.py`
Expected: two PromQL-selector matches — `_nativeha_integrity_expr` (~line 409) and the "Active instance" tile in `nativeha_status_band` (~line 435). These are the only `resource="QMNATIVE"` selectors and the only literals this task de-hardcodes.

Note: the name also appears as **prose**, not PromQL — a comment (~line 160) and the *logs-panel* description path `/var/mqm/qmgrs/QMNATIVE/errors/...` (~line 708). The logs panel is out of scope for this spike (spec §7), and `grep '"QMNATIVE"'` (double-quoted) deliberately won't match those, so they stay as-is here.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_clusterboard.py`:

```python
def test_qm_native_is_a_single_constant_used_by_the_board():
    from mqlab.clusterboard import QM_NATIVE

    assert QM_NATIVE == "QMNATIVE"
    # the board's PromQL must be built FROM the constant, not a stray literal:
    # rendering still emits the same selector text.
    tiles = nativeha_status_band("promtest", y=3)
    assert f'cluster_resource_owner{{resource="{QM_NATIVE}"}}' in tiles[0]["targets"][0]["expr"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd .worktrees/issue-313-cockpit-tooltips && uv run pytest tests/test_clusterboard.py::test_qm_native_is_a_single_constant_used_by_the_board -v`
Expected: FAIL with `ImportError: cannot import name 'QM_NATIVE'`.

- [ ] **Step 4: Add the constant and de-hardcode the exprs**

In `src/mqlab/clusterboard.py`, near the other module constants, add:

```python
QM_NATIVE = "QMNATIVE"  # single source of truth for the Native HA QM name (#313)
```

Then replace each `"QMNATIVE"` literal in the Native HA exprs with the constant via f-string. For the "Active instance" tile in `nativeha_status_band`:

```python
        _stat(
            "Active instance",
            f'max by (holder)(cluster_resource_owner{{resource="{QM_NATIVE}"}})',
            ds_uid,
            0,
            y,
            text_mode="name",
            w=5,
            h=h,
            value_size=vs,
        ),
```

Apply the same `f'...{QM_NATIVE}...'` substitution to `_nativeha_integrity_expr` (and any other NHA expr the Step 1 grep found). Leave the PromQL text otherwise unchanged.

- [ ] **Step 5: Run the new test + the existing NHA board tests**

Run: `cd .worktrees/issue-313-cockpit-tooltips && uv run pytest tests/test_clusterboard.py -k 'nativeha or qm_native' -v`
Expected: PASS — including the pre-existing `test_nativeha_status_band_is_one_compact_full_width_row` and `test_nativeha_board_scopes_shared_metrics_to_nha_groups` (proves the de-hardcode is byte-identical).

- [ ] **Step 6: Confirm no `QMNATIVE` PromQL literal remains**

Run: `cd .worktrees/issue-313-cockpit-tooltips && grep -n '"QMNATIVE"' src/mqlab/clusterboard.py`
Expected: exactly **one** match — the constant definition `QM_NATIVE = "QMNATIVE"`. The two PromQL selectors (lines ~409, ~435) now go through `QM_NATIVE`; the prose occurrences (comment, logs-panel path) are intentionally untouched per Step 1.

- [ ] **Step 7: Refactor**

Look for:
- **Placement** — `QM_NATIVE` belongs with the other module-level constants near the top, not buried beside one expr. Move it if it landed mid-file.
- **Uniformity** — every de-hardcoded expr should interpolate the constant the same way (`f'…{QM_NATIVE}…'`); no NHA expr still inlines the literal (the Step 6 grep proves this).
- **Naming for the family** — `QM_NATIVE` should read as the template for the future `QM_RDQM` / `QM_PCMK` (spec §11). Keep the name shape consistent so the fan-out is mechanical.

Re-run `uv run pytest tests/test_clusterboard.py -k 'nativeha or qm_native' -v` after any change — still PASS.

- [ ] **Step 8: Commit**

```bash
cd .worktrees/issue-313-cockpit-tooltips && vrg-git add src/mqlab/clusterboard.py tests/test_clusterboard.py && vrg-commit --type refactor --scope obs --message "single QM_NATIVE constant; de-hardcode the Native HA board PromQL (#313)"
```

---

### Task 2: `tooltips.py` — catalog + `render_description`

The single source of truth and its renderer. The `behind`/`investigate` templates carry `{qm}`; `render_description` substitutes it and emits the markdown Grafana shows.

**Files:**
- Create: `src/mqlab/tooltips.py`
- Test: `tests/test_tooltips.py`

**Interfaces:**
- Produces:
  - `PanelDoc` — frozen dataclass with `behind: tuple[str, ...]`, `investigate: tuple[str, ...]`, `source: tuple[str, ...]` (`behind` and `source` are parallel, index-aligned).
  - `TOOLTIPS: dict[str, PanelDoc]` — keys `nativeha.status.{active,quorum,insync,hastatus,integrity}`, `nativeha.instances`, `nativeha.crr`.
  - `render_description(key: str, qm: str) -> str` — consumed by Task 5.
- Consumes: nothing (pure data + string formatting).

- [ ] **Step 1: Verify the Investigate command strings before encoding them**

The `behind` commands are authoritative (they come from the collector, Task 3 proves it). The `investigate` commands are curated and must be **real and read-only** — verify each against IBM Docs 9.4 (Native HA / `dspmq`) and `docs/reference/`, and confirm on the live `QMNATIVE` that it exists and is non-mutating. The verified Phase-A set (all read-only):

- `dspmq -m {qm} -o nativeha -g` — Native HA recovery-group / CRR view (the collector's other probe).
- `dspmq -o status` — overall queue-manager status on this host.
- `cat /var/mqm/qmgrs/{qm}/errors/AMQERR01.LOG` — the QM's error log (file-based; cf. the existing logs-panel note, `test_nativeha_log_panel_notes_amqerr_is_file_based`).

If the live output shows a different path or flag, adjust the strings here before Step 2. Record confirmation in the commit message.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_tooltips.py`:

```python
from __future__ import annotations

import pytest

from mqlab.tooltips import TOOLTIPS, render_description


def test_render_substitutes_qm_and_shows_both_sections():
    md = render_description("nativeha.crr", "QMX")
    assert "**Behind this data**" in md
    assert "**Investigate**" in md
    assert "dspmq -m QMX -o nativeha -g" in md  # {qm} substituted
    assert "{qm}" not in md                      # no unsubstituted template field


def test_every_nativeha_key_renders_nonempty():
    for key in (k for k in TOOLTIPS if k.startswith("nativeha.")):
        md = render_description(key, "QMNATIVE")
        assert md.strip()


def test_unknown_key_raises_keyerror():
    with pytest.raises(KeyError):
        render_description("nativeha.does-not-exist", "QMNATIVE")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd .worktrees/issue-313-cockpit-tooltips && uv run pytest tests/test_tooltips.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mqlab.tooltips'`.

- [ ] **Step 4: Implement `tooltips.py`**

Create `src/mqlab/tooltips.py`:

```python
"""Per-panel command tooltips for the cockpit dashboards (#313).

Single source of truth: a catalog mapping a logical-panel key -> PanelDoc. Each PanelDoc
holds command TEMPLATES (with a {qm} field, never a hardcoded QM name) — the read commands
behind a panel (`behind`) and read-only diagnostics (`investigate`) — plus the collector
command key(s) each `behind` template derives from (`source`, index-aligned with `behind`).

render_description substitutes the QM and emits the markdown Grafana shows as the panel's
info (i) tooltip. The `behind` half is bound to the collectors' real argv by
tests/test_tooltips.py, so it cannot silently drift from what the lab actually runs. The
`investigate` half is curated, read-only diagnostics (no mutating commands).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PanelDoc:
    behind: tuple[str, ...]       # command templates, index-aligned with `source`
    investigate: tuple[str, ...]  # read-only diagnostic templates
    source: tuple[str, ...]       # collector command keys, index-aligned with `behind`


_NHA_X = "dspmq -m {qm} -o nativeha -x"
_NHA_G = "dspmq -m {qm} -o nativeha -g"
_STATUS = "dspmq -o status"
_ERRLOG = "cat /var/mqm/qmgrs/{qm}/errors/AMQERR01.LOG"

# All five ① band tiles + the ② instance matrices read the same `-x` probe; the ③ CRR card
# reads `-g`. Investigate lists are tile-specific framing over the verified read-only set.
TOOLTIPS: dict[str, PanelDoc] = {
    "nativeha.status.active": PanelDoc(
        behind=(_NHA_X,), investigate=(_NHA_G, _STATUS), source=("nativeha_x",)
    ),
    "nativeha.status.quorum": PanelDoc(
        behind=(_NHA_X,), investigate=(_NHA_X, _STATUS), source=("nativeha_x",)
    ),
    "nativeha.status.insync": PanelDoc(
        behind=(_NHA_X,), investigate=(_NHA_G, _NHA_X), source=("nativeha_x",)
    ),
    "nativeha.status.hastatus": PanelDoc(
        behind=(_NHA_X,), investigate=(_NHA_X, _ERRLOG), source=("nativeha_x",)
    ),
    "nativeha.status.integrity": PanelDoc(
        behind=(_NHA_X,), investigate=(_NHA_X, _NHA_G, _ERRLOG), source=("nativeha_x",)
    ),
    "nativeha.instances": PanelDoc(
        behind=(_NHA_X,), investigate=(_NHA_X, _ERRLOG), source=("nativeha_x",)
    ),
    "nativeha.crr": PanelDoc(
        behind=(_NHA_G,), investigate=(_NHA_X, _STATUS), source=("nativeha_g",)
    ),
}


def render_description(key: str, qm: str) -> str:
    """Render TOOLTIPS[key] to Grafana panel-description markdown, with {qm} substituted.
    Unknown key raises KeyError (fail-loud — never a blank tooltip)."""
    doc = TOOLTIPS[key]
    behind = "\n".join(c.format(qm=qm) for c in doc.behind)
    investigate = "\n".join(c.format(qm=qm) for c in doc.investigate)
    return (
        "**Behind this data**\n"
        f"```sh\n{behind}\n```\n"
        "**Investigate**\n"
        f"```sh\n{investigate}\n```"
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd .worktrees/issue-313-cockpit-tooltips && uv run pytest tests/test_tooltips.py -v`
Expected: PASS (all three tests).

- [ ] **Step 6: Refactor**

Look for:
- **Command literals** — every command string should live in a module constant (`_NHA_X`, `_NHA_G`, `_STATUS`, `_ERRLOG`); no raw `dspmq …` string inline in a `PanelDoc`. This is what lets the cross-check and the wiring share one definition.
- **Render duplication** — the **Behind**/**Investigate** blocks in `render_description` are near-identical; if it reads cleaner, extract a local `_section(heading, templates, qm)` returning the bold heading + fenced block, and call it twice.
- **Key naming** — keys follow one scheme (`nativeha.status.*`, `nativeha.instances`, `nativeha.crr`); no stray casing or separators.

Re-run `uv run pytest tests/test_tooltips.py -v` after any change — still PASS.

- [ ] **Step 7: Commit**

```bash
cd .worktrees/issue-313-cockpit-tooltips && vrg-git add src/mqlab/tooltips.py tests/test_tooltips.py && vrg-commit --type feat --scope obs --message "tooltips catalog + render_description for the Native HA panels (#313)" --body "Investigate commands verified read-only against IBM Docs 9.4 and live QMNATIVE."
```

---

### Task 3: collector cross-check — bind `behind` to the real argv

The anti-drift guard. Normalizes a collector's argv to command tokens and asserts each catalog `behind` template equals its paired collector command, token for token.

**Files:**
- Modify: `src/mqlab/tooltips.py` (add `command_tokens` + `behind_is_backed`)
- Test: `tests/test_tooltips.py`

**Interfaces:**
- Consumes: `nativehastate._commands(qm) -> dict[str, tuple[list[str], int]]`; `clusterboard.QM_NATIVE` (Task 1); `TOOLTIPS` (Task 2).
- Produces:
  - `command_tokens(argv: list[str]) -> list[str]` — unwrap `su - <user> -c '<cmd>'`, strip an absolute `/…/bin/` path prefix, split into tokens.
  - `behind_is_backed(key: str, qm: str, commands: dict[str, tuple[list[str], int]]) -> bool` — collector accessor result passed in, so the same check serves all three arms in Phases B/C.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tooltips.py`:

```python
from mqlab import nativehastate
from mqlab.clusterboard import QM_NATIVE
from mqlab.tooltips import behind_is_backed, command_tokens


def test_command_tokens_unwraps_su_and_strips_abs_path():
    argv = ["su", "-", "mqm", "-c", "/opt/mqm/bin/dspmq -m QMNATIVE -o nativeha -x"]
    assert command_tokens(argv) == ["dspmq", "-m", "QMNATIVE", "-o", "nativeha", "-x"]


def test_command_tokens_passes_plain_argv_through():
    assert command_tokens(["crm_mon", "--one-shot"]) == ["crm_mon", "--one-shot"]


@pytest.mark.parametrize("key", [k for k in TOOLTIPS if k.startswith("nativeha.")])
def test_behind_command_is_backed_by_the_collector(key):
    commands = nativehastate._commands(QM_NATIVE)
    assert behind_is_backed(key, QM_NATIVE, commands)


def test_every_source_key_exists_in_the_collector():
    # a renamed/removed probe must fail loudly with a named source key (spec §6),
    # not a bare KeyError buried inside behind_is_backed.
    valid = set(nativehastate._commands(QM_NATIVE))
    for key, doc in TOOLTIPS.items():
        if key.startswith("nativeha."):
            missing = set(doc.source) - valid
            assert not missing, f"{key} references unknown collector keys: {missing}"


def test_drift_is_caught_when_behind_is_not_what_the_collector_runs():
    # a partial command must NOT pass (this is the loose-substring hole the check closes)
    commands = {"nativeha_x": (["su", "-", "mqm", "-c", "/opt/mqm/bin/dspmq -m QMNATIVE -o nativeha -x"], 3)}
    from mqlab.tooltips import PanelDoc
    import mqlab.tooltips as tt

    tt.TOOLTIPS["_probe.partial"] = PanelDoc(behind=("dspmq",), investigate=(), source=("nativeha_x",))
    try:
        assert not behind_is_backed("_probe.partial", "QMNATIVE", commands)
    finally:
        del tt.TOOLTIPS["_probe.partial"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd .worktrees/issue-313-cockpit-tooltips && uv run pytest tests/test_tooltips.py -k 'command_tokens or backed or drift or source' -v`
Expected: FAIL with `ImportError: cannot import name 'behind_is_backed'` (the module-level import fails until Step 3).

- [ ] **Step 3: Implement the helpers**

Append to `src/mqlab/tooltips.py`:

```python
def command_tokens(argv: list[str]) -> list[str]:
    """Normalize a collector argv to comparable command tokens: unwrap a
    `su - <user> -c '<cmd>'` shell (the `-c` payload is the real command), drop an absolute
    `/…/bin/` path prefix from the program name, and split on whitespace."""
    payload = argv[-1] if argv[:1] == ["su"] else " ".join(argv)
    tokens = payload.split()
    if tokens and tokens[0].startswith("/"):
        tokens[0] = tokens[0].rsplit("/", 1)[-1]
    return tokens


def behind_is_backed(
    key: str, qm: str, commands: dict[str, tuple[list[str], int]]
) -> bool:
    """True iff every `behind` template (with {qm} substituted) equals — token for token —
    the normalized argv of its paired `source` collector command. Equality, not containment:
    the displayed read command must BE the collector's command. The accessor result is passed
    in, so the same check serves nativeha/rdqm (_commands) and pcmk (PROBE_SETS) alike."""
    doc = TOOLTIPS[key]
    for template, src in zip(doc.behind, doc.source, strict=True):
        want = template.format(qm=qm).split()
        if want != command_tokens(commands[src][0]):
            return False
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/issue-313-cockpit-tooltips && uv run pytest tests/test_tooltips.py -v`
Expected: PASS (all tests, including the parametrized per-key checks and the drift test).

- [ ] **Step 5: Refactor**

Look for:
- **Readable normalization** — `command_tokens` does two distinct steps (unwrap the `su -c` payload; strip the `/…/bin/` prefix). Keep each self-evident; a one-line comment per step beats a clever one-liner.
- **Invariant guard** — `zip(doc.behind, doc.source, strict=True)` enforces the parallel-list contract; keep `strict=True` so a mismatched catalog entry fails loudly rather than silently truncating.
- **Reuse** — if any other module already normalizes argv this way, consolidate rather than duplicating; otherwise leave `command_tokens` as the single home (Phases B/C will reuse it).

Re-run `uv run pytest tests/test_tooltips.py -v` after any change — still PASS.

- [ ] **Step 6: Commit**

```bash
cd .worktrees/issue-313-cockpit-tooltips && vrg-git add src/mqlab/tooltips.py tests/test_tooltips.py && vrg-commit --type feat --scope obs --message "cross-check binding tooltip 'behind' commands to collector argv (#313)"
```

---

### Task 4: optional `description=` on `matrix()` and `_stat()`

Mirror the existing `_logs_panel` pattern so any panel can carry a tooltip.

**Files:**
- Modify: `src/mqlab/clusterboard.py` (`matrix` at clusterboard.py:171, `_stat` at clusterboard.py:290)
- Test: `tests/test_clusterboard.py`

**Interfaces:**
- Produces: `matrix(..., description: str | None = None)` and `_stat(..., description: str | None = None)` — when non-`None`, the returned panel dict gains `panel["description"]`. Consumed by Task 5.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_clusterboard.py`:

```python
def test_matrix_carries_optional_description():
    cols = [("c", "max by (n)(x)", "up")]
    assert "description" not in matrix("t", cols, DS, y=0)               # absent by default
    assert matrix("t", cols, DS, y=0, description="hi")["description"] == "hi"


def test_stat_carries_optional_description():
    from mqlab.clusterboard import _stat

    assert "description" not in _stat("t", "max(x)", DS, 0, 0)           # absent by default
    assert _stat("t", "max(x)", DS, 0, 0, description="hi")["description"] == "hi"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd .worktrees/issue-313-cockpit-tooltips && uv run pytest tests/test_clusterboard.py -k description -v`
Expected: FAIL — `_stat()`/`matrix()` got an unexpected keyword argument `description`.

- [ ] **Step 3: Add the parameter to both builders**

In `matrix()` (clusterboard.py:171), add `description: str | None = None` to the signature, and before `return`, build the panel dict then set the key. Simplest: assign the dict to a variable `panel`, then:

```python
    if description is not None:
        panel["description"] = description
    return panel
```

Do the same in `_stat()` (clusterboard.py:290): add `description: str | None = None` to the keyword-only args, capture the returned dict as `panel`, and apply the identical three lines before returning.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/issue-313-cockpit-tooltips && uv run pytest tests/test_clusterboard.py -k description -v`
Expected: PASS (both tests, both branches — present and absent).

- [ ] **Step 5: Refactor**

Look for (the real consolidation in this task):
- **Triplicated pattern** — `if description is not None: panel["description"] = description` now appears in `matrix()`, `_stat()`, **and** the pre-existing `_logs_panel()` (clusterboard.py:571). Extract one module-private helper and route all three through it:

```python
def _with_description(panel: dict[str, Any], description: str | None) -> dict[str, Any]:
    if description is not None:
        panel["description"] = description
    return panel
```

  Have `matrix`/`_stat` end with `return _with_description(panel, description)`, and refit `_logs_panel` to use it too (it currently inlines the same lines). This is the "consolidate with existing code" win.

Re-run `uv run pytest tests/test_clusterboard.py -k 'description or log_row' -v` after the change — still PASS (the `_logs_panel` description test must stay green).

- [ ] **Step 6: Commit**

```bash
cd .worktrees/issue-313-cockpit-tooltips && vrg-git add src/mqlab/clusterboard.py tests/test_clusterboard.py && vrg-commit --type feat --scope obs --message "optional description= on matrix()/_stat() builders (#313)"
```

---

### Task 5: wire tooltips into the Native HA board

Attach rendered descriptions to the five ① band tiles, the two ② instance matrices, and the ③ CRR card.

**Files:**
- Modify: `src/mqlab/clusterboard.py` (`nativeha_status_band` ~clusterboard.py:431, `nativeha_crr_card` ~clusterboard.py:745, and the two `matrix(...)` calls in `_nativeha_board` ~clusterboard.py:1422-1423)
- Test: `tests/test_clusterboard.py`

**Interfaces:**
- Consumes: `render_description` (Task 2), `QM_NATIVE` (Task 1), the `description=` param (Task 4).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_clusterboard.py`:

```python
def test_nativeha_panels_carry_command_tooltips():
    d = render_cluster_dashboard({}, arm="nativeha-rhel")
    by_title = {p.get("title"): p for p in d["panels"]}

    # ① all five band tiles, ② both instance matrices, ③ CRR card
    for title in (
        "Active instance", "Quorum", "Instances in-sync", "HA status", "Integrity",
        "Site A", "Site B", "CRR connected", "CRR in-sync", "CRR backlog",
    ):
        desc = by_title[title].get("description", "")
        assert "**Behind this data**" in desc, title
        assert "dspmq -m QMNATIVE -o nativeha" in desc, title

    # the CRR card specifically reads the -g group view
    assert "dspmq -m QMNATIVE -o nativeha -g" in by_title["CRR connected"]["description"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/issue-313-cockpit-tooltips && uv run pytest tests/test_clusterboard.py::test_nativeha_panels_carry_command_tooltips -v`
Expected: FAIL — `KeyError`/empty `description` (panels have no tooltip yet).

- [ ] **Step 3: Wire the band tiles**

At the top of `clusterboard.py`, add the import:

```python
from mqlab.tooltips import render_description
```

In `nativeha_status_band`, add `description=render_description(<key>, QM_NATIVE)` to each `_stat(...)`, mapping tile → key:

- "Active instance" → `"nativeha.status.active"`
- "Quorum" → `"nativeha.status.quorum"`
- "Instances in-sync" → `"nativeha.status.insync"`
- "HA status" → `"nativeha.status.hastatus"`
- "Integrity" → `"nativeha.status.integrity"`

Example for the first tile:

```python
        _stat(
            "Active instance",
            f'max by (holder)(cluster_resource_owner{{resource="{QM_NATIVE}"}})',
            ds_uid,
            0,
            y,
            text_mode="name",
            w=5,
            h=h,
            value_size=vs,
            description=render_description("nativeha.status.active", QM_NATIVE),
        ),
```

- [ ] **Step 4: Wire the CRR card**

In `nativeha_crr_card`, add `description=render_description("nativeha.crr", QM_NATIVE)` to each of the three `_stat(...)` tiles ("CRR connected", "CRR in-sync", "CRR backlog").

- [ ] **Step 5: Wire the instance matrices**

In `_nativeha_board`, add `description=render_description("nativeha.instances", QM_NATIVE)` to both `matrix("Site A", ...)` (clusterboard.py:1423) and `matrix("Site B", ...)` (clusterboard.py:1425) calls.

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd .worktrees/issue-313-cockpit-tooltips && uv run pytest tests/test_clusterboard.py::test_nativeha_panels_carry_command_tooltips -v`
Expected: PASS.

- [ ] **Step 7: Refactor**

Look for:
- **Repeated render call** — `render_description("…", QM_NATIVE)` now appears at ~10 call sites (5 band tiles + 3 CRR tiles + 2 matrices). Add a local helper near `_nativeha_board` and use it everywhere:

```python
def _nha_tip(key: str) -> str:
    return render_description(key, QM_NATIVE)
```

  so call sites read `description=_nha_tip("nativeha.crr")` — less noise, one place that binds the arm's QM.
- **Key/typo check** — every key passed matches a real `TOOLTIPS` entry (Task 2). A typo would raise `KeyError` at render — run the board render once to confirm none do.

Re-run `uv run pytest tests/test_clusterboard.py -k 'nativeha' -v` after the change — still PASS.

- [ ] **Step 8: Commit**

```bash
cd .worktrees/issue-313-cockpit-tooltips && vrg-git add src/mqlab/clusterboard.py tests/test_clusterboard.py && vrg-commit --type feat --scope obs --message "wire command tooltips onto the Native HA cockpit panels (#313)"
```

---

### Task 6: full validation + live-Grafana checkpoint

**Files:** none (verification only — no RED/GREEN/REFACTOR cycle; this task gates the work, it doesn't implement code).

- [ ] **Step 1: Run the full validation pipeline**

Run: `cd .worktrees/issue-313-cockpit-tooltips && vrg-container-run -- vrg-validate`
Expected: PASS, including 100% branch coverage. If coverage flags an uncovered branch, the likely culprits are the `description is not None` arms (Task 4) or the `behind_is_backed` loop — add the missing both-way assertion and re-run.

- [ ] **Step 2: Render the board and eyeball the JSON**

Render the Native HA dashboard JSON (via the existing render/obs path) and confirm the five band tiles, both matrices, and the CRR card each carry a `description` with both sections.

- [ ] **Step 3: Live-Grafana checkpoint (spec §7)**

Bring up the lab's Grafana, open `lab-nativeha-cluster`, hover the ⓘ on each wired panel, and confirm:
- the ⓘ appears in the panel header;
- the markdown renders (bold headings, two sections);
- **the fenced `sh` code blocks render as legible monospaced commands, not raw backticks** (this gates the §5 format choice — if they render poorly, switch to indented or inline code and re-verify).

This is the Phase A acceptance gate — rendered JSON looking right is not sufficient.

- [ ] **Step 4: Push the branch and open the PR**

```bash
cd .worktrees/issue-313-cockpit-tooltips && vrg-git push -u origin feature/313-cockpit-tooltips
```
Then open a PR into `develop` referencing #313 (via `vrg-gh pr`).

---

## Self-Review

**Spec coverage:**
- §3 `tooltips.py` (catalog, `PanelDoc`, `render_description`) → Task 2. ✓
- §3 `clusterboard.py` `QM_NATIVE` + de-hardcode → Task 1; `description=` on builders → Task 4; wiring → Task 5. ✓
- §5 rendering (markdown, `{qm}` substitution, fenced code) → Task 2 (impl) + Task 6 Step 3 (fenced-code checkpoint). ✓
- §6 cross-check (normalize, equality, accessor-as-parameter) → Task 3. ✓
- §7 five band tiles + matrices + CRR card → Task 5; checkpoint → Task 6. ✓
- §8 investigate commands verified, read-only → Task 2 Step 1. ✓
- §9 fail-loud `KeyError` → Task 2 (test + impl). ✓
- §10 test files + 100% branch coverage + both-way branches → Tasks 2-5 tests + Task 6 Step 1. ✓
- §11 Phase A scope only → whole plan; B/C explicitly excluded. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; investigate commands are concrete (with a verification step), not deferred. ✓

**Type consistency:** `render_description(key, qm)`, `command_tokens(argv) -> list[str]`, `behind_is_backed(key, qm, commands) -> bool`, `PanelDoc(behind, investigate, source)`, `QM_NATIVE` — names and signatures match across Tasks 1-5 and the tests. ✓
