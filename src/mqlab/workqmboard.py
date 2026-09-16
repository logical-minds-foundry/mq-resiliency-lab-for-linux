"""Work-edition QM-view board generator (#964, epic .github#169 Wave 1a).

The **first** work-edition board, and the one that sets the conventions the Queue/channel
(#965) and Infra (#967) boards follow. It answers spec §7.1's question — *"is this queue
manager healthy, and is it trending toward trouble?"* — and is **QM-scoped by design**:
every query is filtered by ``$qmgr`` and all per-queue / per-channel breach detail lives on
the Flow board (#965), one drill-link away, **not** here.

Built on the portable-render foundation (`workboards.py`): every panel's datasource is the
``${datasource}`` template variable and the QM is the ``$qmgr`` variable, so the board
imports into any MQ Grafana with a Prometheus datasource (spec §5, the portability
contract). It reuses `clusterboard`'s tested panel primitives (`_stat`, `_timeseries`,
`_row_header`, `_t`) verbatim — `clusterboard` stays lab-object-driven; portability rides on
top via `portable_dashboard`.

Every PromQL binds to the Prometheus schema-note contract
(``contracts/prometheus-schema-note.md``, Task 1): only ``ibmmq_qmgr_*`` series verified
live on the exporter. The contract's failover finding (§5) is a design input:

- **Trend panels bound to publication-driven series (§4)** — message rate and recovery-log %
  — tolerate the documented Native-HA failover **publication gap** via ``spanNulls``: a
  multi-minute hole while the client-mode exporter re-subscribes is the system behaving
  correctly, not a dead panel ("never render a gap as broken", spec §6 / schema-note §5).
- **Health that must survive a failover** leads with the failover-resilient §3 object-status
  floor (QM status, uptime, services, connection count) — those keep reporting across a
  failover because they come from PCF/command-queue polling, not publications.

Structure (spec §7.1, the signal-in-the-noise model §6.4):

- ① **Status & services** — QM status · uptime · connections, plus the three service pieces
  (channel-initiator · command-server · listeners) as compact **always-on** up/down pills:
  the calm-state census a glance can read, the failover-resilient §3 object-status floor.
- ② **Trends** — a 3×2 grid of derivatives and resource/health tachometers. Row A: message
  rate (put vs get, ``rate()`` on the §4.1 counters) · byte throughput · recovery-log %.
  Row B: CPU load (1/5/15-min) · MQI failure rate (any failed verb / s, empty = healthy) ·
  log write latency. Every y-axis is floored at 0 (recovery-log % fully pinned 0–100) so a
  calm series can't auto-zoom into a misleading diagonal.
- ③ **Attention — services down** — the empty=healthy hero (spec §6.4). A table whose PromQL
  *itself* filters to only the DOWN services (``!= 2`` for the two service statuses, ``== 0``
  for listeners), so a healthy service produces **no series** and the table is **empty when
  all is well** — the literal signal in the noise, achieved **in-query, never by row color**.
  When a piece is down it names which. Complements ①: ① is the always-visible census, ③ the
  "is anything wrong?" hero.
- ④ **Event / error feed** — a **SEAM only**. The ES feed content is Wave 1b (#966), blocked
  on LogSearch; this ships a clearly-marked placeholder, **no** Loki panel (work has no
  Loki; the feed is built once against Elasticsearch, spec §4).
- **Drill-down** — a data link carrying ``$qmgr`` → the Queue/channel board (`work-flow`).

This is a **v1 starting point**: the panel design is tuned interactively with the human
afterward (spec §3.3), folding good live tweaks back into this generator.
"""

from __future__ import annotations

from typing import Any

from mqlab.clusterboard import _STALE_MAP, _STATUS_MAP, _ds, _row_header, _stat, _t, _timeseries
from mqlab.workboards import DS_REF, datasource_var, portable_dashboard, qmgr_var

# The pinned Grafana uid / filename stem for this board, and the Queue/channel board's uid
# this board drills into. `work-flow` is the convention Task 4 (#965) adopts — set here
# because this is the first board and the drill-link needs a stable target.
QM_BOARD_UID = "work-qm"
FLOW_BOARD_UID = "work-flow"

# The QM selector every query is scoped by — the $qmgr template variable, never a lab literal.
_QM = '{qmgr="$qmgr"}'

# QM status shares the lab family's -1/0/1/2 coloured vocabulary, plus STALE so a truly-null
# series reads no-data, never a false healthy (fail-loud).
_QM_STATUS_MAP: list[dict[str, Any]] = [*_STATUS_MAP, _STALE_MAP]

# A single service piece's up/down pill (folded into ① Status): -1 no-data / 0 down / 1 up, + STALE.
_UPDOWN_MAP: list[dict[str, Any]] = [
    {
        "type": "value",
        "options": {
            "-1": {"text": "No data", "color": "grey", "index": 0},
            "0": {"text": "⚠ down", "color": "red", "index": 1},
            "1": {"text": "✓ up", "color": "green", "index": 2},
        },
    },
    _STALE_MAP,
]

_COMPACT_VALUE_SIZE = 22


# ── binding expressions (all QM-scoped, all schema-note metrics) ─────────────────


def _status_expr() -> str:
    # §3.1 object-status floor; also the $qmgr variable's source metric. Sentinel so a null
    # series reads No-status, never a false healthy.
    return f"max(ibmmq_qmgr_status{_QM}) or vector(-1)"


def _uptime_expr() -> str:
    return f"max(ibmmq_qmgr_uptime{_QM}) or vector(-1)"


def _connections_expr() -> str:
    return f"max(ibmmq_qmgr_connection_count{_QM}) or vector(-1)"


def _initiator_ok() -> str:
    return f"(max(ibmmq_qmgr_channel_initiator_status{_QM}) == bool 2)"


def _command_server_ok() -> str:
    return f"(max(ibmmq_qmgr_command_server_status{_QM}) == bool 2)"


def _listeners_ok() -> str:
    return f"(max(ibmmq_qmgr_active_listeners{_QM}) > bool 0)"


def _msg_put_rate() -> str:
    # §4.1 counter → rate(). $__rate_interval adapts to the target Prometheus scrape step, so
    # the board is portable to work's exporter without hardcoding a window (spec §5).
    return f"sum(rate(ibmmq_qmgr_interval_mqput_mqput1_total_count{_QM}[$__rate_interval]))"


def _msg_get_rate() -> str:
    return f"sum(rate(ibmmq_qmgr_interval_destructive_get_total_count{_QM}[$__rate_interval]))"


def _recovery_log_expr() -> str:
    # §4.1 gauge — the richer publication-driven recovery-log % (raw, no rate). Publication-
    # driven, so it briefly disappears during a failover (schema-note §5) → the panel is
    # rendered gap-tolerant (see _gap_tolerant).
    return f"max(ibmmq_qmgr_log_current_primary_space_in_use_percentage{_QM})"


def _byte_put_rate() -> str:
    # §4.1 byte counter → rate(): data *volume* put, the companion to the message-count rate.
    return f"sum(rate(ibmmq_qmgr_interval_mqput_mqput1_total_bytes{_QM}[$__rate_interval]))"


def _byte_get_rate() -> str:
    return f"sum(rate(ibmmq_qmgr_interval_destructive_get_total_bytes{_QM}[$__rate_interval]))"


def _cpu_load(window: str) -> str:
    # QM-host CPU load average (gauge, already a percentage). window ∈ {one, five, fifteen}.
    return f"max(ibmmq_qmgr_cpu_load_{window}_minute_average_percentage{_QM})"


# The MQI failures that mean an app genuinely can't do its work: the connect→open→put→close
# lifecycle. Deliberately EXCLUDES failed_mqget (and browse): in MQ, an MQGET that returns
# MQRC_NO_MSG_AVAILABLE (2033) — the normal "empty queue" poll — increments failed_mqget, so a
# healthy lab with any polling consumer shows a constant non-zero failed_mqget. Folding that in
# would paint a permanent line and destroy the "empty = healthy" reading this panel is for.
# (A __name__=~ regex union can't be used: rate() drops __name__, collapsing every failed_*
# series to one identical labelset — "vector cannot contain metrics with the same labelset".)
_MQI_FAILURE_VERBS = (
    "failed_mqconn_mqconnx_count",
    "failed_mqopen_count",
    "failed_mqput_count",
    "failed_mqput1_count",
    "failed_mqclose_count",
)


def _mqi_failure_rate() -> str:
    # Genuine MQI failures / s — one leading-indicator line (empty = healthy). Per-verb
    # sum(rate()) added together (see _MQI_FAILURE_VERBS for why not a regex union).
    return " + ".join(
        f"sum(rate(ibmmq_qmgr_{verb}{_QM}[$__rate_interval]))" for verb in _MQI_FAILURE_VERBS
    )


def _log_write_latency() -> str:
    # Recovery-log write latency (gauge, seconds) — Native-HA disk/replication health: rising
    # latency is an early RPO-risk signal, well before the recovery-log % itself moves.
    return f"max(ibmmq_qmgr_log_write_latency_seconds{_QM})"


# ── panel assembly ──────────────────────────────────────────────────────────────


def _gap_tolerant(panel: dict[str, Any]) -> dict[str, Any]:
    """Make a §4 (publication-driven) trend panel tolerate the documented failover
    publication gap: connect across nulls so a multi-minute re-subscription hole reads as the
    system behaving correctly, not a dead panel (schema-note §5, spec §6)."""
    panel["fieldConfig"]["defaults"].setdefault("custom", {})["spanNulls"] = True
    return panel


def _axis(panel: dict[str, Any], *, lo: float = 0, hi: float | None = None) -> dict[str, Any]:
    """Pin a trend panel's y-axis so Grafana can't auto-zoom a low, calm series into a
    misleading full-height diagonal (the recovery-log % v1 complaint). ``lo`` anchors the floor
    (defaults to 0 — rates/latencies/percentages never go negative) while real spikes size the
    top; adding ``hi`` fixes the whole range (recovery-log % → 0–100, an honest
    headroom-to-full view)."""
    defaults = panel["fieldConfig"]["defaults"]
    defaults["min"] = lo
    if hi is not None:
        defaults["max"] = hi
    return panel


def _banner(y: int) -> dict[str, Any]:
    content = "## Queue Manager · $qmgr — health & trend (portable, work edition)"
    return {
        "type": "text",
        "title": "",
        "transparent": True,
        "gridPos": {"h": 2, "w": 24, "x": 0, "y": y},
        "options": {"mode": "markdown", "content": content},
    }


def _status_band(y: int) -> list[dict[str, Any]]:
    """① Status & services — the failover-resilient §3 object-status floor in one dense row:
    QM status · uptime · connections, then the three service pieces (channel-initiator ·
    command-server · listeners) folded in directly as compact up/down pills. v1's separate
    "which piece is down" row is gone — the pieces live here. The status tile carries the
    drill-link to the Flow board, passing $qmgr + the chosen $datasource so the drill lands
    scoped."""
    status = _stat(
        "QM status",
        _status_expr(),
        DS_REF,
        0,
        y,
        mappings=_QM_STATUS_MAP,
        w=6,
        h=4,
        value_size=_COMPACT_VALUE_SIZE,
    )
    status["fieldConfig"]["defaults"]["links"] = [
        {
            "title": "Queue/channel detail ↗",
            "url": f"/d/{FLOW_BOARD_UID}?var-qmgr=$qmgr&var-datasource=${{datasource}}",
        }
    ]
    uptime = _stat(
        "Uptime",
        _uptime_expr(),
        DS_REF,
        6,
        y,
        unit="dtdurations",
        w=5,
        h=4,
        value_size=_COMPACT_VALUE_SIZE,
    )
    connections = _stat(
        "Connections",
        _connections_expr(),
        DS_REF,
        11,
        y,
        w=4,
        h=4,
        value_size=_COMPACT_VALUE_SIZE,
    )
    initiator = _stat(
        "Chl initiator",
        f"{_initiator_ok()} or vector(-1)",
        DS_REF,
        15,
        y,
        mappings=_UPDOWN_MAP,
        w=3,
        h=4,
        value_size=_COMPACT_VALUE_SIZE,
    )
    command_server = _stat(
        "Cmd server",
        f"{_command_server_ok()} or vector(-1)",
        DS_REF,
        18,
        y,
        mappings=_UPDOWN_MAP,
        w=3,
        h=4,
        value_size=_COMPACT_VALUE_SIZE,
    )
    listeners = _stat(
        "Listeners",
        f"{_listeners_ok()} or vector(-1)",
        DS_REF,
        21,
        y,
        mappings=_UPDOWN_MAP,
        w=3,
        h=4,
        value_size=_COMPACT_VALUE_SIZE,
    )
    return [status, uptime, connections, initiator, command_server, listeners]


def _trend_band(y: int) -> list[dict[str, Any]]:
    """② Trends — a 3×2 grid of derivatives and resource/health tachometers.
    Row A (y): message rate (put vs get on one graph — the depth derivative) · byte throughput
    (the volume companion) · recovery-log %. Row B (y+7): CPU load (1/5/15-min) · MQI failure
    rate (any failed verb / s, empty = healthy) · log write latency. The publication-driven
    panels (message/byte rate, recovery-log %) are gap-tolerant across a failover; every panel
    is y-axis-floored at 0, and recovery-log % is fully pinned 0–100 so a calm series can't
    auto-zoom into a misleading diagonal (the v1 complaint)."""
    y2 = y + 7
    msg_rate = _axis(
        _gap_tolerant(
            _timeseries(
                "Message rate (put vs get / s)",
                [_t("A", _msg_put_rate(), "put / s"), _t("B", _msg_get_rate(), "get / s")],
                DS_REF,
                0,
                y,
                w=8,
                unit="short",
            )
        ),
        lo=0,
    )
    byte_rate = _axis(
        _gap_tolerant(
            _timeseries(
                "Byte throughput (put vs get / s)",
                [_t("A", _byte_put_rate(), "put B/s"), _t("B", _byte_get_rate(), "get B/s")],
                DS_REF,
                8,
                y,
                w=8,
                unit="Bps",
            )
        ),
        lo=0,
    )
    recovery = _axis(
        _gap_tolerant(
            _timeseries(
                "Recovery log % (log space in use)",
                [_t("A", _recovery_log_expr(), "log used")],
                DS_REF,
                16,
                y,
                w=8,
                unit="percent",
            )
        ),
        lo=0,
        hi=100,
    )
    cpu = _axis(
        _timeseries(
            "CPU load (1 / 5 / 15-min avg %)",
            [
                _t("A", _cpu_load("one"), "1-min"),
                _t("B", _cpu_load("five"), "5-min"),
                _t("C", _cpu_load("fifteen"), "15-min"),
            ],
            DS_REF,
            0,
            y2,
            w=8,
            unit="percent",
        ),
        lo=0,
    )
    mqi_fail = _axis(
        _timeseries(
            "MQI failure rate (failed verbs / s)",
            [_t("A", _mqi_failure_rate(), "failures / s")],
            DS_REF,
            8,
            y2,
            w=8,
            unit="short",
        ),
        lo=0,
    )
    log_latency = _axis(
        _timeseries(
            "Log write latency (s)",
            [_t("A", _log_write_latency(), "write latency")],
            DS_REF,
            16,
            y2,
            w=8,
            unit="s",
        ),
        lo=0,
    )
    return [msg_rate, byte_rate, recovery, cpu, mqi_fail, log_latency]


def _es_feed_seam(y: int) -> dict[str, Any]:
    """④ Event / error feed — a SEAM only. The ES-backed QM-event + error-log feed is Wave 1b
    (#966), blocked on the LogSearch project (Elasticsearch doc/field contract). This is a
    marked placeholder, NOT a wired panel: no Loki (work has no Loki; the feed is built once
    against Elasticsearch, spec §4). The interactive-tuning + Wave-1b pass fills it in."""
    content = (
        "### ④ Events & errors — Elasticsearch feed *(seam)*\n\n"
        "QM event feed + error-log feed, severity-filtered — **Wave 1b (#966)**, blocked on "
        "the LogSearch project (the Elasticsearch doc/field contract). Built once against "
        "Elasticsearch (`${logs}`), **not** Loki. Placeholder in Wave 1a."
    )
    return {
        "type": "text",
        "title": "④ Events & errors — Elasticsearch feed (Wave 1b · #966)",
        "gridPos": {"h": 6, "w": 24, "x": 0, "y": y},
        "options": {"mode": "markdown", "content": content},
    }


# ── ③ Attention — services down (empty = healthy) ────────────────────────────────

# Every row this table can show is a DOWN service, so any numeric value means "attention":
# a single range mapping (whole real line) paints it red with a "⚠ DOWN" label. There is no
# green state — a healthy service is *absent*, not a green row (that is the whole point).
_ATTENTION_MAP: list[dict[str, Any]] = [
    {
        "type": "range",
        "options": {
            "from": None,
            "to": None,
            "result": {"text": "⚠ DOWN", "color": "red", "index": 0},
        },
    },
]

# The qmgr-class labels the union carries but the Attention table doesn't need shown — dropped
# so the table reads as a clean "Service | State" pair.
_ATTENTION_DROP_LABELS = ("qmgr", "platform", "description", "hostname", "__name__")


def _attention_expr() -> str:
    """The ③ Attention union: one row per DOWN service, and **no series at all when every
    service is healthy** (empty = healthy). The filter lives in the PromQL — ``!= 2`` on the
    two service-status gauges (2 = running) and ``== 0`` on the listener count — so a healthy
    service is filtered *out* and simply doesn't appear; `label_replace` tags each surviving
    series with the human-readable ``service`` name. This is spec §6.4's "push the filter into
    the query, not the row color". Binds only schema-note §3.1 qmgr-class object-status series
    (failover-resilient), so the panel keeps working across a Native-HA failover."""
    initiator = (
        f"label_replace(ibmmq_qmgr_channel_initiator_status{_QM} != 2, "
        f'"service", "Channel initiator", "qmgr", ".*")'
    )
    command = (
        f"label_replace(ibmmq_qmgr_command_server_status{_QM} != 2, "
        f'"service", "Command server", "qmgr", ".*")'
    )
    listeners = (
        f"label_replace(ibmmq_qmgr_active_listeners{_QM} == 0, "
        f'"service", "Listeners", "qmgr", ".*")'
    )
    return f"{initiator}\nor {command}\nor {listeners}"


def _attention_panel(y: int) -> dict[str, Any]:
    """③ Attention — a query-filtered table that is **empty when the QM's services are all
    healthy** and grows one red "⚠ DOWN" row per stopped service (spec §6.4). Instant table
    format; the `service` label becomes the row name and the value column is mapped to the
    DOWN indicator. Datasource is `${datasource}` via `_ds(DS_REF)` (portable)."""
    return {
        "type": "table",
        "title": "③ Attention — services down (empty = all healthy)",
        "datasource": _ds(DS_REF),
        "gridPos": {"h": 6, "w": 24, "x": 0, "y": y},
        "targets": [
            {
                "refId": "A",
                "expr": _attention_expr(),
                "format": "table",
                "instant": True,
                "datasource": _ds(DS_REF),
            }
        ],
        "transformations": [
            {
                "id": "organize",
                "options": {
                    "renameByName": {
                        "service": "Service",
                        "Value": "State",
                        "Value #A": "State",
                    },
                    "excludeByName": {
                        "Time": True,
                        **dict.fromkeys(_ATTENTION_DROP_LABELS, True),
                    },
                },
            },
        ],
        "fieldConfig": {
            "defaults": {"custom": {"align": "left"}},
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "State"},
                    "properties": [
                        {
                            "id": "custom.cellOptions",
                            "value": {"type": "color-background", "mode": "basic"},
                        },
                        {"id": "mappings", "value": _ATTENTION_MAP},
                        {"id": "color", "value": {"mode": "fixed"}},
                    ],
                }
            ],
        },
        "options": {"cellHeight": "sm", "showHeader": True},
    }


def work_qm_dashboard() -> dict[str, Any]:
    """The portable work-edition QM-view board (spec §7.1). Datasource-portable and
    QM-scoped: pick a Prometheus (`$datasource`) and a queue manager (`$qmgr`) on import;
    every panel follows. Returns the Grafana dashboard dict; `portable_dashboard` rewrites
    all datasources to the template variables (the portability contract)."""
    panels: list[dict[str, Any]] = [_banner(y=0)]
    panels.append(
        _row_header(
            "① Status & services — status · uptime · connections · service pills",
            y=2,
        )
    )
    panels.extend(_status_band(y=3))
    panels.append(
        _row_header(
            "② Trends — message/byte rate · recovery-log % · CPU · MQI failures · latency",
            y=7,
        )
    )
    panels.extend(_trend_band(y=8))
    panels.append(
        _row_header(
            "③ Attention — services down (empty = healthy; filter in query, not row color)",
            y=22,
        )
    )
    panels.append(_attention_panel(y=23))
    panels.append(_es_feed_seam(y=29))
    return portable_dashboard(
        "Queue Manager — Health & Trend (portable)",
        QM_BOARD_UID,
        panels,
        [datasource_var(), qmgr_var()],
        tags=["work", "portable", "qm"],
    )
