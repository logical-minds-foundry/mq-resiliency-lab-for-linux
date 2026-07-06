# Per-QM state dashboard — design

- **Task:** `mq-resiliency-lab-for-linux#489`, under epic `logical-minds-foundry/.github#8` (Lab observability stack)
- **Status:** design (brainstorm output), approved for a first pass
- **Date:** 2026-07-06

## Goal

A code-templated Grafana board — **one per app queue manager** — answering *"how is
**this** QM doing?"*: the queue manager's own health, its critical application queues,
and its critical channels. It complements the existing boards: the cluster cockpits are
HA/topology-oriented and the messaging board is end-to-end-flow-oriented; this is the
single-QM **operational** view. Follow-on per-object drill-downs are out of scope; this
first pass brings up the single page.

## Data source (settled)

Everything comes from the **already-scraped Prometheus `ibmmq_*` series** — the per-stack
`mq_prometheus` exporters emit far more than any board currently uses (verified against a
live exporter). **No new exporter, collector, or REST path** is needed, which is what keeps
this a single task. Confirmed-available families:

- **QM:** `ibmmq_qmgr_status`, `_uptime`, `_connection_count`, `_channel_initiator_status`,
  `_command_server_status`, `_active_listeners`, `_log_size_reusable`/`_restart`/`_media`.
- **Queue:** `ibmmq_queue_depth`, `_attribute_max_depth`, `_oldest_message_age`,
  `_uncommitted_messages`, `_input_handles`, `_output_handles`, `_qtime_short`/`_long`,
  `_time_since_get`/`_put`, `_qfile_current_size`.
- **Channel:** `ibmmq_channel_status`/`_status_squash`, `_substate`, `_messages`,
  `_bytes_sent`/`_rcvd`, `_batches`, `_nettime_short`/`_long`, `_time_since_msg`, `_cur_inst`.

Per-object put/get **rate** uses the queue/qmgr `mqput`/`mqget` counters the existing
boards already consume; each column's exact series is bound and verified at build/observe
(the object-driven fallback below makes a missing one read as absence, not a false zero).

## The object model (derived from the stack `short`, no hardcoded literals — #351)

On the app QM `{SHORT}APP` (e.g. `NHAUAPP`):

- **Critical queues (depth-bearing):** `APP.REPLY` (QLOCAL — replies land here) and
  `SVCQM` (QLOCAL `USAGE(XMITQ)` — the transmission queue to the counterparty). The
  `SVC.REQUEST` QREMOTE is only a pointer to the `SVCQM` XMITQ (MQ's normal request/reply
  shape), so **the XMITQ is the canonical representation of the request-outbound path** —
  the board shows the XMITQ, with a one-line "request → SVCQM via XMITQ" context note; no
  phantom-depth tile and no cross-QM reach into the SVC board.
- **Critical channels:** `APP.SVRCONN` (app client in), `{SHORT}APP.SVCQM` (SDR → svc),
  `SVCQM.{SHORT}APP` (RCVR ← svc). `MON.SVRCONN` is the exporter's own ops channel and is
  not app-critical (excluded).

## The board (three sections, top-down)

### ① QM header band — at-a-glance health
A row of stat tiles: **status** (running/stopped) · **uptime** · **connections**
(`connection_count`) · **msg rate** (interval put+get, with a sparkline) · a **services**
pill folding `channel_initiator_status` + `command_server_status` + `active_listeners`
(the plumbing is up) · **recovery-log %** (`log_size_reusable`/`_restart` — an early-warning
gauge for a filling log).

### ② Critical queues — a compact, data-rich table
One row per critical queue (`APP.REPLY`, `SVCQM` XMITQ), columns: **depth** · **%full**
(`depth`/`attribute_max_depth`) · **oldest-msg age** · **uncommitted** · **in/out handles**
(is a consumer attached?) · **put rate** · **get rate** · **time-since-get**. Depth, %full,
and oldest-age carry thresholds so a backlog jumps out.

### ③ Critical channels — a compact, data-rich table
One row per channel (`APP.SVRCONN`, SDR, RCVR), columns: **status** + **substate** ·
**messages** · **bytes** sent/rcvd · **batches** · **nettime** (network RTT) ·
**time-since-msg** (last activity) · **cur-inst**. Status coloured
(running / retrying / stopped / inactive / no-status).

Tables are the "compact but data-rich" choice — many columns per object — mirroring the
messaging board's queue/channel tables and the cluster cockpit's matrix panels.

## Architecture & components

- **`src/mqlab/qmboard.py` (new, pure builder):** topology dict + a stack → Grafana
  dashboard dict; no I/O in the builder. `lab_qm_dashboards()` renders one board per app QM
  from the real topology (mirroring `messagingboard.write_messaging_dashboards()`). uid
  pinned per QM: `lab-qm-<short>`. QM/queue/channel names derive from `short` — no hardcoded
  QM literals. Reuses `clusterboard`'s shared panel/table helpers where they fit.
- **Object-driven, not metric-driven** (the fleet convention): every curated QM/queue/channel
  signal renders a tile at all times; a missing series reads as a coloured "no data"
  (`or vector(-1)` / STALE), never a vanished row or a false zero.
- **Render + provision:** the `mqlab obs dashboard` render writes the per-QM boards to
  `build/work/grafana/dashboards/`, and the Grafana role copies them (the established
  render-in-mqlab / copy-in-role pattern — same as the messaging boards).
- **Drill-in:** the messaging board already documents a "drill-down data-link seam to the
  future per-QM/channel/queue detail boards"; wire that seam's link to `lab-qm-<short>`.

## Testing

- `qmboard.py` is a pure builder → unit-tested to the repo's 100%-branch gate: the emitted
  section/column structure, the queue/channel names derived per `short`, uid pinned, and the
  object-driven "tile present even with no series" rule. A real-topology smoke test renders
  the board for every app QM (pcmk/rdqm/nhar/nhau).
- `vrg-validate` is the only validation. Live proof (the tiles light with real depths,
  handles, channel status) is the cold-boot/observe pass.

## Scope & escalation

- **In:** the single per-QM page (three sections above), rendered per app QM, provisioned,
  drilled-into from the messaging board.
- **Out (first pass):** per-object drill-down detail boards; the SVC QM's own board; any new
  metric/collector work. The metric inventory shows none is needed — if a wanted signal
  turned out to be un-scraped, that would be the trigger to escalate #489 to a small epic.
  On this design, it stays a single task.
