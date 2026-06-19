# Native HA Cockpit — PR2: the board (arm render path) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A code-generated `lab-nativeha-cluster` Grafana board — title banner + ① hero band +
first-class integrity light (the §6 reframing) + ② instances matrices (Live site A / Recovery
site B) — rendered by extending `render_cluster_dashboard(topo, arm="nativeha-rhel")`, reusing
the existing `clusterboard.py` primitives against the `cluster_*`/`cluster_nha_*` series PR1's
collector emits.

**Architecture:** `render_cluster_dashboard` gains an `arm="nativeha-rhel"` branch. The PCMK
path is untouched (regression-safe). The integrity core is extracted from `integrity_panel`
into `_integrity_from_expr(...)` so both arms share the Stat+maps+full-width shape with
arm-specific hazard exprs. Native-HA input tables (`_NATIVEHA_INSTANCE_COLS`, a `"role"` cell
mapping, the hero exprs, the hazard expr) are added as data — no new low-level builders.

**Tech Stack:** Python 3.11+ pure render funcs, Grafana v13 Table/Stat panels, Prometheus
`cluster_*`/`cluster_nha_*` (PR1), Ansible `grafana` role.

## Global Constraints

- **No new low-level builders.** Reuse `matrix`, `_stat`, `fold_side`/`active_side`,
  `_title_banner`, `_row_header`. Parametrizing/extracting a shared core counts as reuse;
  adding a *new* arm's input tables is the sanctioned extension (§3.2 contract).
- **PCMK board output is unchanged.** The existing `tests/test_clusterboard.py` must stay green
  — the `arm="pcmk"` default path produces byte-identical panels.
- **Integrity reframing (spec §6):** Native HA *cannot* split-brain. The hazard is
  **quorum-lost ∨ no-Active ∨ replica-not-in-sync**, gated on data present so no-data → STALE
  (never a false green).
- **Owner resource is `QMNATIVE`** (not `mq_qm`); `active_side`/the Active-instance hero read
  `cluster_resource_owner{resource="QMNATIVE"}`.
- **Board uid:** `lab-nativeha-cluster`; tags `["lab","cockpit","nativeha-rhel"]`.
- **Deferred to later PRs (spec §8):** CRR card + timeline (PR3); logs + perf/network (PR4);
  overview roll-up + drill-link (PR5). PR2 assembles only title + hero + integrity + instances.
- **Validation:** `vrg-container-run -- vrg-validate` only; 100% branch coverage.

---

## Task 1: Extract the integrity core + Native-HA integrity panel

**Files:** Modify `src/mqlab/clusterboard.py`; Test `tests/test_clusterboard.py`.

**Interfaces:**
- Produces: `_integrity_from_expr(expr: str, ds_uid: str, y: int) -> dict` (the Stat + green/
  HAZARD/STALE maps + full-width gridPos, extracted verbatim from `integrity_panel`).
  `nativeha_integrity_panel(ds_uid: str, y: int) -> dict`.
- `integrity_panel` (PCMK) now delegates to `_integrity_from_expr` — same output.
- Native-HA hazard expr:
  `((min(cluster_quorate) == bool 0) + (absent(cluster_resource_owner{resource="QMNATIVE"}) or vector(0)) + (count(cluster_nha_insync == 0) or vector(0))) and on() (count(cluster_nha_role) > 0)`

- [ ] **Step 1: Write the failing test**

```python
def test_nativeha_integrity_is_quorum_active_insync_gated_on_data():
    from mqlab.clusterboard import nativeha_integrity_panel
    p = nativeha_integrity_panel("promtest", y=7)
    assert p["type"] == "stat"
    assert p["gridPos"]["w"] == 24
    expr = p["targets"][0]["expr"]
    assert "cluster_quorate" in expr
    assert 'cluster_resource_owner{resource="QMNATIVE"}' in expr
    assert "cluster_nha_insync == 0" in expr
    assert "count(cluster_nha_role) > 0" in expr  # gated -> no-data reads STALE
    # the STALE special-mapping is present (never a false green)
    kinds = [m["type"] for m in p["fieldConfig"]["defaults"]["mappings"]]
    assert "special" in kinds


def test_pcmk_integrity_panel_unchanged():
    # regression: the PCMK integrity panel still carries the DRBD hazard expr
    from mqlab.clusterboard import integrity_panel
    p = integrity_panel("promtest", y=7)
    assert 'cluster_drbd_conn{conn="StandAlone"}' in p["targets"][0]["expr"]
    assert p["gridPos"]["w"] == 24
```

- [ ] **Step 2: Run — expect FAIL** (`cannot import name 'nativeha_integrity_panel'`).
Run: `vrg-container-run -- uv run pytest tests/test_clusterboard.py -k integrity -q`

- [ ] **Step 3: Implement.** Extract the body of `integrity_panel` into `_integrity_from_expr`,
have `integrity_panel` call it with the existing DRBD `hazards`/gate expr, and add:

```python
def nativeha_integrity_panel(ds_uid: str, y: int) -> dict[str, Any]:
    """Native HA can't split-brain (raft); the hazard is quorum-lost / no-Active /
    replica-not-in-sync, gated on data present so no-data reads STALE."""
    hazards = (
        "(min(cluster_quorate) == bool 0)"
        ' + (absent(cluster_resource_owner{resource="QMNATIVE"}) or vector(0))'
        " + (count(cluster_nha_insync == 0) or vector(0))"
    )
    expr = f"({hazards}) and on() (count(cluster_nha_role) > 0)"
    return _integrity_from_expr(expr, ds_uid, y)
```

- [ ] **Step 4: Run — expect PASS** (incl. the unchanged PCMK regression test).
- [ ] **Step 5: Commit** — `feat(obs): nativeha integrity panel + extract integrity core (#279)`.

---

## Task 2: Native-HA hero tiles

**Files:** Modify `src/mqlab/clusterboard.py`; Test `tests/test_clusterboard.py`.

**Interfaces:**
- Produces: `nativeha_hero_tiles(ds_uid: str, y: int) -> list[dict]` — four `_stat` tiles:
  - **Active instance** — `max by (holder)(cluster_resource_owner{resource="QMNATIVE"})`,
    `text_mode="name"` (shows the holder, e.g. `nha-rhel-a1`).
  - **Quorum** — `max(cluster_nha_quorum)` (the in-quorum node count).
  - **Instances in-sync** — `sum(max by (member)(cluster_nha_insync))`.
  - **HA status** — `min(max by (member)(cluster_nha_hastatus{status="Normal"}))` with the
    DOWN/healthy/STALE maps (1 → all Normal; 0/absent → not-all-Normal / STALE).

- [ ] **Step 1: Write the failing test**

```python
def test_nativeha_hero_tiles_band():
    from mqlab.clusterboard import nativeha_hero_tiles
    tiles = nativeha_hero_tiles("promtest", y=3)
    titles = [t["title"] for t in tiles]
    assert titles == ["Active instance", "Quorum", "Instances in-sync", "HA status"]
    active = tiles[0]
    assert active["options"]["textMode"] == "name"
    assert 'cluster_resource_owner{resource="QMNATIVE"}' in active["targets"][0]["expr"]
    assert "cluster_nha_quorum" in tiles[1]["targets"][0]["expr"]
    assert "cluster_nha_insync" in tiles[2]["targets"][0]["expr"]
    # the tiles tile the top row left-to-right
    assert [t["gridPos"]["x"] for t in tiles] == [0, 6, 12, 18]
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** `nativeha_hero_tiles` using `_stat` (mirror `hero_tiles`; reuse
  `_STALE_MAP` and the health maps for the HA-status tile).
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `feat(obs): nativeha hero tiles (#279)`.

---

## Task 3: `"role"` cell mapping + instances matrices (Live / Recovery)

**Files:** Modify `src/mqlab/clusterboard.py`; Test `tests/test_clusterboard.py`.

**Interfaces:**
- Adds `_MAPPINGS["role"]`: value-maps a role code → coloured text —
  `2 → "Active"` (green), `1 → "Replica"` (blue), `0 → "Unknown"` (red).
- Produces `_nativeha_instance_cols(site_regex: str) -> list[Column]`:
  - **online** — `cluster_node_online{member=~"<site_regex>"}` (up).
  - **role** — `2*…{role="Active"} + …{role="Replica"}` coded expr (role mapping).
  - **in-sync** — `cluster_nha_insync{member=~"<site_regex>"}` (up).
  - **HA Normal** — `cluster_nha_hastatus{status="Normal",member=~"<site_regex>"}` (up).
- The two matrices use site regexes `nha-rhel-a.*` (Live) and `nha-rhel-b.*` (Recovery).

> The role column is numeric-coded so the existing `matrix()` colour-cell machinery applies; a
> down instance reports `role="Unknown"` → code 0 → red (not STALE); a vanished node reports
> nothing → null → STALE.

- [ ] **Step 1: Write the failing test**

```python
def test_role_mapping_codes_active_replica_unknown():
    from mqlab.clusterboard import _MAPPINGS
    opts = _MAPPINGS["role"][0]["options"]
    assert opts["2"]["text"] == "Active"
    assert opts["1"]["text"] == "Replica"
    assert opts["0"]["text"] == "Unknown"


def test_nativeha_instance_cols_for_a_site():
    from mqlab.clusterboard import _nativeha_instance_cols
    cols = _nativeha_instance_cols("nha-rhel-a.*")
    titles = [c[0] for c in cols]
    assert titles == ["online", "role", "in-sync", "HA Normal"]
    role_expr = cols[1][1]
    assert 'role="Active"' in role_expr and 'role="Replica"' in role_expr
    assert all('member=~"nha-rhel-a.*"' in c[1] or "role=" in c[1] for c in cols)
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** `_MAPPINGS["role"]` and `_nativeha_instance_cols` (use `_norm` with
  a `member=~"<site>"` filter; the role column is a hand-built coded expr normalized on `n`).
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `feat(obs): nativeha instances matrix columns + role mapping (#279)`.

---

## Task 4: `render_cluster_dashboard(arm="nativeha-rhel")` — assemble the board

**Files:** Modify `src/mqlab/clusterboard.py`; Test `tests/test_clusterboard.py`.

**Interfaces:**
- `render_cluster_dashboard(topo, arm="pcmk", ds_uid="prometheus")` branches: `arm ==
  "nativeha-rhel"` assembles `[_title_banner, ① row header, *nativeha_hero_tiles,
  nativeha_integrity_panel, ② Live matrix, ② Recovery matrix]` with `uid="lab-nativeha-cluster"`,
  title `"Native HA Cluster · Infrastructure View"`, tags include `"nativeha-rhel"`. The PCMK
  path is unchanged.
- Add `_ARM_NAMES["nativeha-rhel"] = "MQ raft Native HA + CRR cross-region · RHEL 9.6 (x86_64)"`.

- [ ] **Step 1: Write the failing test**

```python
def test_nativeha_board_uid_sections_and_tags():
    from mqlab.clusterboard import render_cluster_dashboard
    d = render_cluster_dashboard({}, arm="nativeha-rhel")
    assert d["uid"] == "lab-nativeha-cluster"
    assert "nativeha-rhel" in d["tags"]
    titles = [p.get("title", "") for p in d["panels"]]
    assert any("Live" in t for t in titles)       # ② Live instances matrix
    assert any("Recovery" in t for t in titles)    # ② Recovery instances matrix
    # the hero band's Active-instance tile is present
    assert any(p.get("title") == "Active instance" for p in d["panels"])
    # no PCMK-only columns leak in
    blob = json.dumps(d)
    assert "corosync" not in blob and "cluster_drbd" not in blob


def test_pcmk_board_still_renders_unchanged():
    from mqlab.clusterboard import render_cluster_dashboard
    d = render_cluster_dashboard({}, arm="pcmk")
    assert d["uid"] == "lab-pcmk-cluster"
    assert any(p.get("title") == "② Compute — node × component" for p in d["panels"])
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** the `arm == "nativeha-rhel"` branch (early-return a nativeha board
  dict). Reuse `matrix("② Instances — Live (site A)", _nativeha_instance_cols("nha-rhel-a.*"),
  ds_uid, y=10, h=5)` and a Recovery matrix at `y=15`. `import json` already present.
- [ ] **Step 4: Run — expect PASS** (incl. the PCMK regression test).
- [ ] **Step 5: Commit** — `feat(obs): render lab-nativeha-cluster board (arm dispatch) (#279)`.

---

## Task 5: CLI wiring + grafana provisioning

**Files:** Modify `src/mqlab/clusterboard.py`, `src/mqlab/cli.py`,
`ansible/roles/grafana/tasks/main.yml`; Test `tests/test_cli_obs.py`.

**Interfaces:**
- `nativeha_dashboard_path() -> Path` = `build/grafana/dashboards/lab-nativeha-cluster.json`;
  `lab_nativeha_dashboard() -> str` = `json.dumps(render_cluster_dashboard(lab_topo,
  arm="nativeha-rhel"), indent=2) + "\n"`.
- `obs up` / `obs dashboard` write the nativeha board alongside `lab-status.json` and
  `lab-pcmk-cluster.json`. The `grafana` role copies it to `/var/lib/grafana/dashboards/`.

- [ ] **Step 1: Write the failing test** (extend the obs-up test family — mirror the
  `test_obs_up_also_renders_the_cockpit_board` test, asserting `lab-nativeha-cluster.json`
  exists with `uid == "lab-nativeha-cluster"`).
- [ ] **Step 2: Run — expect FAIL** (file not written).
- [ ] **Step 3: Implement** `nativeha_dashboard_path`/`lab_nativeha_dashboard`; add the write in
  `_obs_up_steps()` and `obs_dashboard()`; add the `grafana` role copy task (mirror the
  cockpit-board deploy task, `notify: restart grafana`).
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Offline gate** — `vrg-container-run -- vrg-validate` (green; 100% branch).
- [ ] **Step 6: Commit** — `feat(obs): render + provision lab-nativeha-cluster board (#279)`.

---

## Task 6: Provision obs onto the nha arm + live-verify the board (LAB)

> The nha arm is **not yet in observability** (no node-exporter scrape, no Prometheus targets
> for `nha-rhel-*`). This task provisions it and verifies the board against the live 3+3.

- [ ] **Step 1: Provision observability onto the nha arm.** From the worktree (so the deployed
  collector + dashboards are *this* branch's), wire the lab plumbing and run:

```bash
mqlab obs up            # node-exporter + alloy + nativeha-state + Prometheus targets + boards + relay
```

  (If running raw ansible, target `--limit nha_rhel_a:nha_rhel_b` and ensure the worktree's
  `build/` resolves the inventory + `obs/reach-peers.json`.)

- [ ] **Step 2: Confirm the series flow.**

```bash
curl -s 'http://10.50.0.2:9090/api/v1/query' --data-urlencode \
  'query=cluster_resource_owner{resource="QMNATIVE"}' | jq '.data.result'
curl -s 'http://10.50.0.2:9090/api/v1/query' --data-urlencode \
  'query=cluster_nha_group_status' | jq '.data.result'
```

  Expect the Active holder + per-group status across `nha-rhel-*`.

- [ ] **Step 3: Live-verify the board.** Browse `localhost:3000/d/lab-nativeha-cluster`:
  - Active-instance hero shows `nha-rhel-a1`; Quorum 3; Instances in-sync 6 (3 Live + 3
    Recovery replicas); HA status ✓.
  - Integrity reads **✓ integrity** (quorum held).
  - ② Live matrix: a1 Active (green), a2/a3 Replica, all online/in-sync/Normal.
  - ② Recovery matrix: b1/b2/b3 Replica, online/in-sync/Normal.
- [ ] **Step 4** (optional, load-bearing for the spec §7 acceptance — defer if disruptive):
  power off the Active and watch the matrix re-elect + quorum dip to 2/3 + integrity stay clean.

---

## Self-review

- **Spec coverage (PR2 scope):** §5 ① (hero + integrity) and ② (instances matrices, banded
  Live/Recovery) for `arm="nativeha-rhel"`; §6 integrity reframing (quorum/Active/in-sync, no
  split-brain, STALE-on-no-data); §3.2 (reuse builders — only input tables + an arm branch
  added). CRR card/timeline (PR3), logs/perf/net (PR4), roll-up (PR5) explicitly deferred.
- **Placeholder scan:** none — every code step shows the expr/code; commands exact.
- **Type consistency:** `Column` reused; `nativeha_hero_tiles(ds_uid,y)->list`,
  `nativeha_integrity_panel(ds_uid,y)->dict`, `_nativeha_instance_cols(site)->list[Column]`,
  `render_cluster_dashboard(topo,arm,ds_uid)`, `nativeha_dashboard_path`/`lab_nativeha_dashboard`
  consistent across tasks. PCMK regression tests pin the unchanged default path.
- **Coverage note:** the new `arm` branch, the `role` mapping, both site matrices, and the
  integrity/hero exprs are each hit by Tasks 1–4 tests → 100% branch.
