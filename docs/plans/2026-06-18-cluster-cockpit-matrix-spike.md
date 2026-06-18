# Cluster Cockpit — Matrix Engine Spike — Plan

> **For agentic workers:** This is a **SPIKE**, not a TDD build plan. Its deliverable is a
> *decision* (Canvas vs Table) plus a **captured, working panel JSON** that becomes the
> contract for the follow-on build plan. Steps are exploratory — run them against the
> lab's actual Grafana, capture evidence, decide. Do **not** write `matrix()` or
> `clusterboard.py` here — that is the build plan, written once this spike picks the engine.

**Goal:** Decide *how* to render the cluster node × component matrix (coloured cells,
grouped headers, inline DRBD replication band, cluster-unit summary, ★ owner) in the
lab's actual Grafana (**v13.0.2**), by prototyping it **two ways — Table and Canvas —**
against real `cluster_*` series, judging on fixed criteria, and freezing the winner as the
build contract.

**Architecture:** Seed authentic `cluster_*` series into the running Prometheus by driving
the **existing #195 collector** over its fixtures (healthy + a hand-edited mid-cutover
variant) — no live PCMK cluster needed. Build the matrix both ways as hand-authored
dashboard JSON, load each into the running Grafana via the provisioning dir, evaluate
against both states, pick one, and capture the winning panel JSON + the metric→cell recipe.

**Tech Stack:** Grafana (provisioned dashboards under `/var/lib/grafana/dashboards`),
Prometheus (`job="node"`, the `cluster_*` series via the node_exporter textfile collector),
`src/mqlab/clusterstate.py` (the #195 collector — `parse_*` + `render_cluster_state_prom`),
the lab's obs stack (`obs` box, `:3000`, reachable at `localhost:3000` per #264).

**Spec:** `docs/specs/2026-06-18-cluster-cockpit-canvas-rebuild-design.md` §4 (rendering —
this spike decides it), §5 (board layout), §6.2 (the three matrix sections), and the
authoritative mockups in `docs/specs/diagrams/cluster-drill-cockpit-*.html`. Background on
the matrix content and colour vocabulary: `docs/specs/2026-06-14-cluster-drill-cockpit-design.md`
§3.1 + §4.3.

## Global Constraints

- **Lab is operated by the human.** Steps that touch the running lab (writing into a node's
  textfile dir, loading a dashboard, viewing Grafana) are run by the human or via the
  agreed lab access; narrate what to run, don't assume direct VM mutation.
- **Parallel-safe.** Do not touch the RDQM 3+3 experiment occupying the lab, the native-HA
  arm work in flight, `dashboard.py`, or the `grafana` role's existing files. The spike adds
  only a *temporary* synthetic textfile and a *temporary* probe dashboard, both removed at
  the end.
- **No timing claims.** Replication is shown as **out-of-sync bytes + resync %**, never an
  RPO in seconds (TCG, functional-only).
- **Fail-loud / STALE** vocabulary per 2026-06-14 §4.3: green healthy · amber
  transitional/resyncing · red down/failed · grey standby/n-a · hatched STALE on a silent
  collector.
- **The winner must be code-generatable.** Whichever engine wins, its panel JSON must be a
  mechanical projection of (rows × columns × metric-map) — because the build plan generates
  it from `matrix()`, never by hand. Reject an approach that only works as bespoke
  hand-authoring.

---

## Step 1: Produce authentic `cluster_*` seed data from the #195 collector

The matrix binds to the `cluster_*` series. Rather than stand up the six-node PCMK cluster
(the lab is busy with the RDQM experiment), drive the **real collector** over its fixtures
to emit a correct-shape textfile, for two states.

- [ ] **1a. Healthy snapshot.** From the repo root, render the healthy textfile for each
  PCMK member by driving the existing `parse_*` + `render_cluster_state_prom` over the
  committed fixtures (`tests/fixtures/clusterstate/`). Write a throwaway script
  `build/spike/seed_healthy.py` (the `build/` tree is gitignored — spike scaffolding, not
  committed):

```python
# build/spike/seed_healthy.py — emit a healthy lab_cluster_state.prom for all PCMK members
from pathlib import Path
from mqlab import clusterstate as cs

FX = Path("tests/fixtures/clusterstate")
crm = cs.parse_crm((FX / "crm_mon.xml").read_text())
drbd = cs.parse_drbd((FX / "drbd_status.json").read_text())
stonith = cs.parse_stonith((FX / "stonith_history.txt").read_text())
iscsi = cs.parse_iscsi((FX / "iscsi_session.txt").read_text())
daemons = {"corosync": True, "pacemaker": True, "drbd": True}
NOW = 1_750_000_000  # fixed; Date.now() is unavailable and we want determinism

# Each node reports its own view (label node=<self>); the cluster-wide facts are
# redundant across nodes per 2026-06-14 §4.3.
out = []
for node in ("pcmk-a1", "pcmk-a2", "pcmk-a3", "pcmk-b1", "pcmk-b2", "pcmk-b3"):
    out.append(cs.render_cluster_state_prom(
        node=node, crm=crm, stonith=stonith, iscsi=iscsi, daemons=daemons,
        drbd=drbd, now=NOW, fresh_sources=("crm", "drbd", "stonith", "iscsi"),
    ))
for node in ("san-a", "san-b"):
    out.append(cs.render_cluster_state_prom(
        node=node, crm=None, stonith=None, iscsi=iscsi, daemons={"drbd": True},
        drbd=drbd, now=NOW, fresh_sources=("drbd", "iscsi"),
    ))
Path("build/spike/lab_cluster_state_healthy.prom").write_text("\n".join(out))
print("wrote build/spike/lab_cluster_state_healthy.prom")
```

  Run: `uv run python build/spike/seed_healthy.py`
  Expected: the `.prom` file exists and contains `cluster_quorate`, `cluster_node_online`,
  `cluster_resource_owner{...,holder="pcmk-a1"}`, `cluster_drbd_role`,
  `cluster_drbd_out_of_sync_bytes`, etc.

- [ ] **1b. Mid-cutover snapshot.** Copy the two state-bearing fixtures and edit them to the
  mockup's mid-cutover picture (`cluster-drill-cockpit-pcmk-matrix.html` snapshot ②):
  copy `crm_mon.xml` → `build/spike/crm_mon_cutover.xml` and set `pcmk-a1` `online="false"`
  + `unclean="true"`, and move the `mq_group` resource `node` from `pcmk-a1` to `pcmk-b1`;
  copy `drbd_status.json` → `build/spike/drbd_status_cutover.json` and set `san-a`
  `role="Secondary"`, `san-b` `role="Primary"`, with `connection-state="SyncTarget"`,
  `percent-in-sync: 78`, `out-of-sync: 12582912` (12 MiB). Then a `seed_cutover.py`
  identical to 1a but reading the edited copies and adding `stonith` history for `pcmk-a1`
  (one `was fenced` line in a copied `stonith_history.txt`). Write
  `build/spike/lab_cluster_state_cutover.prom`.

  Run: `uv run python build/spike/seed_cutover.py`
  Expected: the cutover `.prom` shows `cluster_node_online{...,member="pcmk-a1"} 0`,
  `cluster_node_unclean{...,member="pcmk-a1"} 1`,
  `cluster_resource_owner{...,holder="pcmk-b1"}`, `cluster_drbd_out_of_sync_bytes 12582912`,
  `cluster_drbd_resync_pct 78`.

- [ ] **1c. Make the series visible to Prometheus.** Place the healthy `.prom` as
  `lab_cluster_state.prom` in a node_exporter **textfile dir scraped by the obs Prometheus**
  on an up node (the `node` label is carried in each sample, so any scraped exporter
  surfaces the series under the right node names). The collector's default dir is
  `/var/lib/node_exporter/textfile/`. Confirm in Grafana Explore (Prometheus datasource)
  that `cluster_quorate` and `cluster_drbd_out_of_sync_bytes` return data. Swap to the
  cutover `.prom` when evaluating the mid-cutover state.

  Verify: in Grafana Explore, `count(cluster_node_online)` returns the member set; toggling
  the `.prom` file flips the values within one scrape interval.

## Step 2: Build the matrix as a Table panel

The compute matrix (§6.2 ②): rows = the six members, columns = `corosync`, `pacemaker`,
`iSCSI`, `fence`, then resource-group `mq_fs`, `mq_vip`, `mq_vip_ext`, `mq_qm`.

- [ ] **2a.** In the running Grafana, create a throwaway dashboard "SPIKE — matrix (table)".
  Add a **Table** panel. Drive it from the `cluster_*` series with an instant query per
  column family, joined by the `member` label, using **Transformations** (`Labels to
  fields` + `Merge` + `Organize`) to produce one row per member and one column per
  component. Encode each component's state as a numeric/string value (e.g.
  `cluster_daemon_up`, `cluster_node_online`, `cluster_fence_count == 0`, and
  `cluster_resource_owner` presence for the resource-group columns).
- [ ] **2b.** Apply **cell-background colour** via value mappings / thresholds in the field
  config (green/amber/red/grey), and a hatched/STALE treatment via a mapping on the
  collector-staleness signal (`cluster_state_last_write_timestamp` age). Fake the grouped
  headers via column naming (e.g. `node·corosync`).
- [ ] **2c.** Capture: export the panel JSON (`build/spike/matrix-table.json`) and a
  screenshot of both the healthy and cutover states.

## Step 3: Build the matrix as a Canvas panel

- [ ] **3a.** Add a **Canvas** panel to the spike dashboard "SPIKE — matrix (canvas)".
  Reproduce the mockup: grouped section headers, a coloured cell element per (member,
  component), the ★ on the owner row, and the inline DRBD replication band (resync % +
  out-of-sync bytes). Bind each cell element's background colour to its series value via
  the element's colour/threshold config.
- [ ] **3b.** Confirm whether per-element data binding scales to the full grid (~48 cells)
  and whether the JSON is a mechanical product of (rows × cols × metric-map) — i.e.
  generatable by code, not bespoke. Note any element that needs hand-placement that
  wouldn't survive a different node count.
- [ ] **3c.** Capture: export the panel JSON (`build/spike/matrix-canvas.json`) and
  screenshots of both states.

## Step 4: Judge against fixed criteria

- [ ] Score Table vs Canvas, healthy and mid-cutover, on:
  1. **Faithfulness** to the mockup (grouped headers, cells, band, ★, summary).
  2. **Mid-cutover legibility** — does a fenced `pcmk-a1` row + the owner jumping to
     `pcmk-b1` + DRBD flipping read at a glance (success criterion §11.2)?
  3. **STALE distinctness** — is hatched STALE unmistakable from red?
  4. **Code-generatability** — is the winning JSON a clean projection of
     (rows × cols × metric-map) for `matrix()` to emit? (Hard gate — see Global Constraints.)
  5. **Robustness / maintainability** — JSON volume, version-sensitivity, dynamic row count.
- [ ] Record the scores in the decision (Step 5). If Canvas can't bind a dense grid cleanly
  or its JSON isn't code-generatable, **Table wins** — that is an expected, acceptable
  outcome, not a failure.

## Step 5: Decide and freeze the contract

- [ ] **5a.** Write the decision into the design spec: append a short "Spike outcome"
  note to `docs/specs/2026-06-18-cluster-cockpit-canvas-rebuild-design.md` §4 — the chosen
  engine, the scores, and *why*.
- [ ] **5b.** Commit the **winning panel JSON** as the build contract:
  `docs/specs/diagrams/cluster-matrix-<engine>-contract.json` (the frozen reference the
  build plan's `matrix()` must reproduce), plus the **metric→cell recipe** (which series →
  which cell, which value → which colour) as prose in the spec note.
- [ ] **5c.** Commit via `vrg-commit --type docs --scope obs` (the contract + spec note are
  the only committed artifacts; `build/spike/` is gitignored).
- [ ] **5d.** Remove the throwaway spike dashboards from the running Grafana and restore the
  healthy/empty textfile state, so the lab is left clean.

## Deliverable → hands off to the build plan

The build plan (written next) consumes: **(1)** the chosen engine, **(2)** the frozen
`cluster-matrix-<engine>-contract.json` as the exact structure `matrix(rows, cols,
metric_map, y)` must emit, and **(3)** the metric→cell recipe. With those, `matrix()` and
its 100%-branch unit tests can be written concretely — the test asserts `matrix(...)`
reproduces the frozen contract JSON for the seeded rows/columns.

## Self-review notes

- This plan touches **no committed code** — only a gitignored `build/spike/` scratch area, a
  temporary textfile in the lab, and (at the end) a committed contract JSON + a spec note.
- It does not depend on the PCMK cluster being up, so it runs **parallel-safe** alongside the
  RDQM experiment and the native-HA arm work.
- The hard gate (code-generatability) prevents the spike from picking a pretty-but-bespoke
  Canvas that `matrix()` can't emit — directly serving the durability lesson of #219.
