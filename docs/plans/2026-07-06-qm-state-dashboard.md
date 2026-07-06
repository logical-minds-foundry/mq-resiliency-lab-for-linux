# Per-QM state dashboard — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.
> TDD per task; `vrg-container-run -- vrg-validate` is the gate. Right-sized (not
> per-line transcription): the builder mirrors `src/mqlab/messagingboard.py` closely, so
> steps name the exact interfaces, metrics, and assertions rather than repeating 300 lines.

**Goal:** One code-templated Grafana board per app QM (`uid: lab-qm-<short>`) — QM health
band + critical-queues table + critical-channels table — from the already-scraped
`ibmmq_*` series.

**Architecture:** A pure builder `src/mqlab/qmboard.py` mirroring `messagingboard.py`
(data→Grafana dicts, no I/O), rendered one-per-app-QM from topology, provisioned by the
Grafana role. Reuses `clusterboard`'s `_stat`/`_row_header`/`_ds`/`_STALE_MAP` helpers.

**Tech Stack:** Python (mqlab, pytest, 100%-branch gate), Grafana JSON, Prometheus, Ansible.

## Global Constraints

- **No hardcoded QM literals** — the app QM is `f"{short}APP"` from each stack's `short`
  (#351); the svc QM is `f"{topo['svc']['short']}QM"`. Fixed MQSC *object* names
  (`APP.REPLY`, `APP.SVRCONN`) are constants, not QM names.
- **Object-driven, not metric-driven** — every curated QM/queue/channel tile renders at all
  times; a missing series reads as coloured no-data (`or vector(-1)` / `_STALE_MAP`), never a
  vanished row or a false zero. (Mirrors `dashboard.py`/`messagingboard.py`.)
- **`uid` pinned** = `lab-qm-<short>` (e.g. `lab-qm-nhau`).
- **Pure builder** — `qmboard.py` does no I/O; the CLI does the file write (like
  `messagingboard.write_messaging_dashboards`).
- **100% branch coverage**; `vrg-validate` is the only validation.

---

### Task 1: `qmboard.py` — the pure builder

**Files:**
- Create: `src/mqlab/qmboard.py`
- Test: `tests/test_qmboard.py`

**Interfaces (produces):**
- `DASHBOARD_UID_PREFIX = "lab-qm-"` and `qm_board_uid(short) -> str` (= `f"lab-qm-{short.lower()}"`).
- `render_qm_board(topo: dict, name: str, cfg: dict, ds_uid: str = "prometheus") -> dict` —
  pure: one stack's app-QM board dict.
- `qm_dashboard_paths_and_texts() -> list[tuple[Path, str]]` **or** `write_qm_dashboards() -> None`
  — mirrors `messagingboard.write_messaging_dashboards`: loops `lab_stacks()`, writes one
  `work("grafana","dashboards", f"lab-qm-{short}.json")` per app QM. (Match the messaging
  signature so cli wiring is symmetric.)
- Internal builders: `_qm_band(...)`, `_queues_table(...)`, `_channels_table(...)`, plus small
  expr helpers.

**Object model (derive from `short`; svc_qm = `f"{topo['svc']['short']}QM"`):**
- `app_qm = f"{short}APP"`.
- Critical queues: `["APP.REPLY", svc_qm]` (svc_qm is the XMITQ, `QLOCAL USAGE(XMITQ)`).
- Critical channels: `["APP.SVRCONN", f"{app_qm}.{svc_qm}", f"{svc_qm}.{app_qm}"]`
  (SVRCONN, SDR, RCVR).

**Metric → tile mapping** (reuse `messagingboard._qm_status_expr`/`_queue_depth_expr`/
`_channel_status_expr` where they fit; new helpers for the rest). Every value expr is wrapped
`... or vector(-1)` for object-driven absence:
- **① QM band** (stat tiles): status `ibmmq_qmgr_status` · uptime `ibmmq_qmgr_uptime`
  (unit `dtdurations`) · connections `ibmmq_qmgr_connection_count` · msg-rate
  `rate(ibmmq_qmgr_interval_mqput_mqput1_total_count[1m]) + rate(ibmmq_qmgr_interval_destructive_get_total_count[1m])`
  (mirror `dashboard.py:_qm_rate_panel`; sparkline) · services pill = `min` over
  `ibmmq_qmgr_channel_initiator_status` + `ibmmq_qmgr_command_server_status` +
  `(ibmmq_qmgr_active_listeners > 0)` · recovery-log % from `ibmmq_qmgr_log_size_restart` /
  (`_restart`+`_reusable`).
- **② Queues table** (row per queue): depth `ibmmq_queue_depth` · %full
  `100*depth/ibmmq_queue_attribute_max_depth` · oldest-age `ibmmq_queue_oldest_message_age` ·
  uncommitted `ibmmq_queue_uncommitted_messages` · in/out handles
  `ibmmq_queue_input_handles`/`_output_handles` · put/get rate
  `rate(ibmmq_queue_mqput_mqput1_total_count[1m])`/`rate(ibmmq_queue_mqget_count[1m])` ·
  time-since-get `ibmmq_queue_time_since_get`.
- **③ Channels table** (row per channel): status `ibmmq_channel_status_squash` + substate
  `ibmmq_channel_substate` · messages `ibmmq_channel_messages` · bytes
  `ibmmq_channel_bytes_sent`/`_rcvd` · batches `ibmmq_channel_batches` · nettime
  `ibmmq_channel_nettime_short` · time-since-msg `ibmmq_channel_time_since_msg` · cur-inst
  `ibmmq_channel_cur_inst`.

> **Verify-at-build:** the exact counter names for msg-rate, queue put/get rate, and the
> log-% ratio are confirmed against a live exporter's `/metrics` during the observe pass; the
> object-driven `or vector(-1)` wrapper makes any that differ read as no-data rather than a
> false zero. Build the tables with the message-board's table helper (`messagingboard`
> `_object_table` / the Table-engine pattern at lines ~170-230) so columns + the STALE
> mapping come for free.

**Tests (100% branch), in `tests/test_qmboard.py`** — fixture topology with ≥1 stack + a
`svc:` block (model on `tests/test_messagingboard.py`):
- `render_qm_board(FIXTURE,"nha-x",cfg)["uid"] == "lab-qm-nhax"`; band + both tables present.
- Names derived from `short`: exprs contain `qmgr="NHAXAPP"`, the queues `APP.REPLY` and the
  svc XMITQ (`queue="SVCQM"`), and the three channels `APP.SVRCONN` / `NHAXAPP.SVCQM` /
  `SVCQM.NHAXAPP` — assert no other stack's QM name appears.
- Object-driven: every value tile's expr ends `or vector(-1)` and status tiles carry
  `_STALE_MAP`.
- Rename a stack's `short` → the board follows it (nothing hardcoded).
- Real-topology smoke (`test_topology_integrity` style): `write_qm_dashboards()` (or the
  paths/texts fn) emits a valid board per app QM (pcmk/rdqm/nhar/nhau), each `uid`
  `lab-qm-<short>`, `json.loads` succeeds.

---

### Task 2: render + provision + drill-in

**Files:**
- Modify: `src/mqlab/cli.py` — both render sites that call `write_messaging_dashboards()`
  (the `obs dashboard` command ~line 486-491 and the provision render ~line 699-700): add the
  symmetric `write_qm_dashboards()` call + its `deps.renderer.command(...)` line.
- Modify: `ansible/roles/grafana/tasks/main.yml` — after the messaging-board copy (~line 96),
  add a glob copy task for `lab-qm-*.json` (mirror the messaging task exactly:
  `src: "{{ playbook_dir }}/../build/work/grafana/dashboards/lab-qm-*.json"`,
  `dest: "/var/lib/grafana/dashboards/{{ item | basename }}"`, `with_fileglob`,
  `notify: restart grafana`).
- Modify: `src/mqlab/messagingboard.py` — wire the drill seam: the app-QM status tile links to
  `lab-qm-<short>` for this stack (set the existing `drill_uid` for the QM tile to
  `qm_board_uid(short)` imported from `qmboard`; the seam machinery at lines ~180-211 already
  exists — just supply the real uid instead of the stub for the QM-level link).
- Test: `tests/test_cli_obs.py` (the per-QM boards are written by `obs dashboard`), and
  `tests/test_messagingboard.py` (the QM tile's data-link url now targets `lab-qm-<short>`).

**Interfaces (consumes):** `write_qm_dashboards()` and `qm_board_uid()` from Task 1.

- [ ] Extend the existing `obs dashboard` render test: after `obs dashboard`, a
  `lab-qm-<short>.json` exists per app QM with `json.loads(...)["uid"] == "lab-qm-<short>"`.
- [ ] Add a `messagingboard` test: the app-QM tile's link url contains `/d/lab-qm-<short>`.
- [ ] Run `vrg-container-run -- vrg-validate` to green; fix any exact-command/count tests the
  new render call shifts.
- [ ] Commit.

## Self-review

- **Spec coverage:** QM band (T1 ①) · queues table (T1 ②) · channels table (T1 ③) ·
  object-model-from-short (T1) · render+provision (T2) · drill-in (T2) · testing (each).
- **No hardcoded QM literals**; `uid = lab-qm-<short>` consistent across T1/T2.
- **No new collector** — all series exist; verify-at-build note covers the 3 rate/log exprs.
- Per-object drill-down detail boards + the SVC-QM board are out of scope (spec).
