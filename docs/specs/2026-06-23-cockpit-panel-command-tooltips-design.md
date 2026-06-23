# Cockpit Panel Command Tooltips — Design

**Date:** 2026-06-23
**Issue:** #313 (Phase A — Native HA spike); Phases B/C follow as their own issues.
**Builds on:** the arm-agnostic cockpit framework (`src/mqlab/clusterboard.py`) and its
three collectors — PCMK (`clusterstate.py`, #219), Native HA (`nativehastate.py`, #279),
RDQM (`rdqmstate.py`, #287). Adds **one new module + one new test**; no new panel builders.

---

## 1. Goal & scope

Make each cockpit panel **self-documenting** about the command line behind it. Every targeted
panel gains a native Grafana **ⓘ hover tooltip** with two parts:

- **Behind this data** — the read-only command(s) that produce exactly what the panel shows.
- **Investigate** — curated, read-only diagnostic commands to look deeper from the shell.

This turns each board into a glass-box teaching aid: an operator can read a tile, hover, and see
the `dspmq` / `drbdadm` / `crm_mon` they would run to reveal or chase the same fact — supportable
without the AI. It directly serves the lab's empowerment thesis: the dashboard explains its own
plumbing.

**In scope:** the **three cockpit boards** that are the real, finished dashboards —
`lab-nativeha-cluster`, `lab-rdqm-cluster`, `lab-pcmk-cluster`. Native HA ships first as the
spike; RDQM and PCMK reuse the proven pattern.

**Out of scope:**

- The first-pass layered board (`lab-fleet-node`, `dashboard.py`) and any other early boards —
  they are PromQL-over-exporter-metrics with no CLI behind them, and are not considered done.
- The future **queue-manager / application-workflow dashboard** — its own spec, later.
- Any **mutating** command (promote, resync, failover). The tooltips are read + diagnose only;
  state-changing operations stay in `docs/reference/`, never one hover away on a live board.
- New panel types or layout changes. Tooltips attach to existing panels.

## 2. Decisions captured

Five forks were resolved during brainstorming and are fixed for this design:

1. **Granularity:** per-**panel** ⓘ tooltips (Grafana's native `description`), not section/row
   tooltips — Grafana `type: "row"` panels carry no description, and per-panel is more precise.
2. **Content:** two parts — **Behind this data** (read) + **Investigate** (diagnose). No "act".
3. **Source of truth:** a **single catalog module, collector-checked** — the read half is bound by
   a test to the collectors' real argv so it cannot silently drift.
4. **Sequencing:** **spike Native HA first**, verify in live Grafana, checkpoint, then fan out.
5. **Status band:** **per-tile** ⓘ — all band tiles share the one read command; each gets a
   tile-specific Investigate hint.

## 3. Architecture

Mirrors the existing collector→board separation: pure data + render functions, no I/O.

- **`src/mqlab/tooltips.py`** *(new — the single source of truth)*
  - `TOOLTIPS: dict[str, PanelDoc]`, keyed by stable logical-panel keys
    (`"nativeha.status.active"`, `"nativeha.status.integrity"`, `"nativeha.instances"`,
    `"nativeha.crr"`, …).
  - `PanelDoc` (a dataclass or TypedDict) carries:
    - `behind: list[str]` — display command strings shown under **Behind this data**.
    - `investigate: list[str]` — display command strings shown under **Investigate**.
    - `source: tuple[str, ...]` — the collector command **key(s)** each `behind` entry derives
      from (e.g. `("nativeha_x",)`). This linkage is what the cross-check test asserts against.
  - `render_description(key: str) -> str` — renders a `PanelDoc` to the markdown that Grafana puts
    in the panel `description` (see §5). Unknown key **raises** `KeyError` (fail-loud — never a
    blank or silently-missing ⓘ).
- **`src/mqlab/clusterboard.py`** *(wiring — mirrors the existing `_logs_panel` pattern)*
  - Add an optional `description: str | None = None` keyword to `matrix()` and `_stat()`, setting
    `panel["description"] = description` when present — the exact three lines `_logs_panel`
    (clusterboard.py:571) already uses.
  - At the `_nativeha_board` call sites, attach descriptions **by key**:
    `matrix("Site A", …, description=render_description("nativeha.instances"))`, and likewise for
    each status-band tile and each CRR-card tile.
- **`tests/test_tooltips.py`** *(new — the anti-drift guard, see §6).*
- **`tests/test_clusterboard.py`** — extend to assert the targeted Native HA panels carry a
  non-empty `description` (so a future refactor can't silently drop the ⓘ).

## 4. Data flow

```
TOOLTIPS[key]  ──render_description──▶  markdown string
                                              │
                                              ▼
                         panel["description"] (clusterboard.py)
                                              │
                                              ▼
                          rendered dashboard JSON  ──▶  Grafana ⓘ hover

nativehastate._commands(qm)  ──normalize──▶  cross-check test  ◀── TOOLTIPS[key].behind / .source
```

The render path is unchanged downstream — panels simply carry a `description`. The test path runs
in parallel, validating the read half against the collector.

## 5. Tooltip rendering

`render_description` emits markdown (Grafana renders the panel `description` as markdown): a bold
**Behind this data** heading + a fenced `sh` code block of the read command(s), then a bold
**Investigate** heading + a fenced `sh` code block of the diagnostic command(s). Code fences keep
commands monospaced and copyable. The QM name is substituted from the same source the board uses
(`<qm>` placeholder resolved at render time), so the displayed command is the real one for the arm.

## 6. The collector cross-check (anti-drift)

`test_tooltips.py` makes the **Behind this data** half structurally incapable of drifting from what
actually runs:

- For each `PanelDoc` with a non-empty `source`, fetch the collector's argv for each referenced
  key and **normalize** it to a display form:
  - strip the privilege wrapper Native HA uses — `["su", "-", "mqm", "-c", "<cmd>"]` → `<cmd>`;
  - strip the absolute path prefix — `/opt/mqm/bin/dspmq …` → `dspmq …`.
- Assert each catalog `behind` string equals (or is a substring of) the normalized collector
  command — i.e. the displayed read command is one the collector genuinely issues.
- Assert every `source` key exists in the collector's command set (a renamed/removed probe fails
  the test loudly).
- Assert `render_description` produces non-empty markdown containing every `behind` and
  `investigate` line, and that **every** targeted catalog key renders (no `KeyError`).

The collector accessor is a **parameter** of the check helper, because the three collectors expose
their commands differently: Native HA and RDQM via `_commands(qm)` (a dict keyed by probe name),
PCMK via the module-level `PROBE_SETS` (keyed by role). The helper takes a callable that returns
`{key: argv}` for a given arm, so the same test logic covers all three in the fan-out phases.

## 7. Native HA spike scope (Phase 1)

Tooltips attach to the Native-HA-collector-backed panels of `_nativeha_board`:

| Panel | Catalog key(s) | Behind (read) | Investigate (draft — verify per §8) |
|---|---|---|---|
| ① band — active | `nativeha.status.active` | `dspmq -m <qm> -o nativeha -x` | which instance is active / role detail |
| ① band — quorum | `nativeha.status.quorum` | `dspmq -m <qm> -o nativeha -x` | quorum membership / connectivity checks |
| ① band — in-sync | `nativeha.status.insync` | `dspmq -m <qm> -o nativeha -x` | replica catch-up / lag checks |
| ① band — integrity | `nativeha.status.integrity` | `dspmq -m <qm> -o nativeha -x` | split-brain / dual-active hazard checks |
| ② Site A / Site B matrices | `nativeha.instances` | `dspmq -m <qm> -o nativeha -x` | per-instance state, QM lifecycle logs |
| ③ CRR card (connected / in-sync / backlog) | `nativeha.crr` | `dspmq -m <qm> -o nativeha -g` | recovery-group view, replication backlog |

The **read** column is authoritative (lifted from `nativehastate._commands`); the **Investigate**
column above is a *draft* and is content work per §8.

**Not in the spike:** Performance, Network, Failover/CRR timeline, and the Native HA **logs** row.
Perf/network are node_exporter metrics with no CLI collector; the logs panel already owns its
`description` (the wired-sources note); the timeline is derived. These can be revisited after the
three boards' collector-backed panels are done.

**Checkpoint:** render the board, confirm the ⓘ appears and the markdown renders correctly in
**live Grafana** before declaring Phase 1 done (rendered JSON looking right is not the gate).

## 8. Investigate-command authoring (honest boundary)

The **Investigate** commands are *not* issued by any collector, so nothing can auto-verify them —
this is the one unguarded surface, and the design names it rather than hiding it. They must be:

- **authored, not invented** — sourced from IBM Docs 9.4 (via the documented IBM-docs fetch
  approach) and the repo's own `docs/reference/` (e.g. the DRBD/DR operations notes), and
- **verified against the live lab** where practical (the command actually exists and is read-only
  on the target arm), the same discipline the collectors' parse code follows.

This is explicit, bounded content work — one Investigate list per catalog key — tracked in the
implementation plan, not a placeholder. The draft lists in §7 are starting points to verify, not
final text.

## 9. Error handling & fail-loud

- Referencing a catalog key that does not exist → `KeyError` from `render_description` (loud at
  render time; the dashboard render already runs in tests). No empty-string fallback.
- A collector probe renamed/removed out from under a `source` reference → `test_tooltips.py`
  fails. The read half cannot drift to a stale command silently.
- No `try/except` that swallows a missing key into a blank tooltip — a missing tooltip is a bug,
  surfaced, consistent with the repo's no-silent-failures stance.

## 10. Testing

- **`tests/test_tooltips.py`** (new): the §6 cross-check; `render_description` markdown shape;
  every Native HA key renders non-empty.
- **`tests/test_clusterboard.py`** (extend): the ① band tiles, ② matrices, and ③ CRR card carry a
  non-empty `description`.
- Full `vrg-container-run -- vrg-validate` (the only validation entry point) green, including the
  existing coverage gates.

## 11. Phases

- **Phase A — Native HA spike (#313):** `tooltips.py` + the Native HA catalog entries +
  `test_tooltips.py` cross-check + wire `description=` into the Native HA panels + extend
  `test_clusterboard.py`. Verify ⓘ in live Grafana → **checkpoint**.
- **Phase B — RDQM:** add RDQM catalog entries (DRBD + Pacemaker + cross-site DR panels), reusing
  the §6 helper with `rdqmstate._commands`. Read commands: `rdqmstatus -m <qm>`,
  `drbdsetup status --verbose --statistics`.
- **Phase C — PCMK:** add PCMK catalog entries, passing the `PROBE_SETS` accessor to the §6 helper.
  Read commands: `crm_mon --one-shot --output-as=xml`, `drbdsetup status …`,
  `stonith_admin --history '*'`, `iscsiadm -m session`.

Each phase is its own issue/PR; B and C add only catalog data + wiring + test rows — no new
machinery beyond Phase A.

## 12. Future (not this work)

- The queue-manager / application-workflow dashboard (new board, separate spec) — once it exists,
  the same catalog pattern extends to it.
- Reconsidering tooltips for perf/network/timeline panels once the collector-backed panels are
  fully covered across the three boards.
