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

Structure (spec §7.1):

- ① **Status band** — QM status · uptime · services (folded) · connections.
- ② **Trend band** — message rate (put vs get, ``rate()`` on the §4.1 counters) ·
  recovery-log % · connections over time.
- ③ **Attention** — which service piece is down (channel-initiator · command-server ·
  listeners), the three signals the ① services pill folds.
- ④ **Event / error feed** — a **SEAM only**. The ES feed content is Wave 1b (#966), blocked
  on LogSearch; this ships a clearly-marked placeholder, **no** Loki panel (work has no
  Loki; the feed is built once against Elasticsearch, spec §4).
- **Drill-down** — a data link carrying ``$qmgr`` → the Queue/channel board (`work-flow`).

This is a **v1 starting point**: the panel design is tuned interactively with the human
afterward (spec §3.3), folding good live tweaks back into this generator.
"""

from __future__ import annotations

from typing import Any

from mqlab.clusterboard import _STALE_MAP, _STATUS_MAP, _row_header, _stat, _t, _timeseries
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

# The folded services pill: 1 (all up) / 0 (something down) / -1 (no data), + STALE.
_SERVICES_MAP: list[dict[str, Any]] = [
    {
        "type": "value",
        "options": {
            "-1": {"text": "No data", "color": "grey", "index": 0},
            "0": {"text": "⚠ issue", "color": "red", "index": 1},
            "1": {"text": "✓ up", "color": "green", "index": 2},
        },
    },
    _STALE_MAP,
]

# A single service piece's up/down pill (③ Attention): -1 no-data / 0 down / 1 up, + STALE.
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


def _services_expr() -> str:
    # The folded plumbing pill: channel-initiator running ∧ command-server running ∧ ≥1 active
    # listener. Each term is a plain 0/1 vector (max() strips labels so they multiply); an
    # absent series → empty product → vector(-1) (no data), never a false green.
    return f"{_initiator_ok()} * {_command_server_ok()} * {_listeners_ok()} or vector(-1)"


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


# ── panel assembly ──────────────────────────────────────────────────────────────


def _gap_tolerant(panel: dict[str, Any]) -> dict[str, Any]:
    """Make a §4 (publication-driven) trend panel tolerate the documented failover
    publication gap: connect across nulls so a multi-minute re-subscription hole reads as the
    system behaving correctly, not a dead panel (schema-note §5, spec §6)."""
    panel["fieldConfig"]["defaults"].setdefault("custom", {})["spanNulls"] = True
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
    """① The failover-resilient status floor (all §3 object-status): QM status · uptime ·
    services (folded) · connections. The status tile carries the drill-link to the Flow
    board, passing $qmgr and the chosen $datasource through so the drill lands scoped."""
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
        w=6,
        h=4,
        value_size=_COMPACT_VALUE_SIZE,
    )
    services = _stat(
        "Services",
        _services_expr(),
        DS_REF,
        12,
        y,
        mappings=_SERVICES_MAP,
        w=6,
        h=4,
        value_size=_COMPACT_VALUE_SIZE,
    )
    connections = _stat(
        "Connections",
        _connections_expr(),
        DS_REF,
        18,
        y,
        w=6,
        h=4,
        value_size=_COMPACT_VALUE_SIZE,
    )
    return [status, uptime, services, connections]


def _trend_band(y: int) -> list[dict[str, Any]]:
    """② Trends (the derivative + the *when*): message rate (put vs get on one graph — the
    depth derivative) · recovery-log % · connections over time. The two publication-driven
    panels are gap-tolerant across a failover; connections comes from the §3 floor."""
    msg_rate = _gap_tolerant(
        _timeseries(
            "Message rate (put vs get / s)",
            [_t("A", _msg_put_rate(), "put / s"), _t("B", _msg_get_rate(), "get / s")],
            DS_REF,
            0,
            y,
            w=8,
            unit="short",
        )
    )
    recovery = _gap_tolerant(
        _timeseries(
            "Recovery log %",
            [_t("A", _recovery_log_expr(), "log used")],
            DS_REF,
            8,
            y,
            w=8,
            unit="percent",
        )
    )
    connections = _timeseries(
        "Connections over time",
        [_t("A", _connections_expr(), "connections")],
        DS_REF,
        16,
        y,
        w=8,
    )
    return [msg_rate, recovery, connections]


def _attention_band(y: int) -> list[dict[str, Any]]:
    """③ Attention — which service piece is down: the three signals the ① services pill folds,
    each as its own up/down pill so a glance names the culprit."""
    initiator = _stat(
        "Channel initiator",
        f"{_initiator_ok()} or vector(-1)",
        DS_REF,
        0,
        y,
        mappings=_UPDOWN_MAP,
        w=8,
        h=4,
        value_size=_COMPACT_VALUE_SIZE,
    )
    command_server = _stat(
        "Command server",
        f"{_command_server_ok()} or vector(-1)",
        DS_REF,
        8,
        y,
        mappings=_UPDOWN_MAP,
        w=8,
        h=4,
        value_size=_COMPACT_VALUE_SIZE,
    )
    listeners = _stat(
        "Listeners",
        f"{_listeners_ok()} or vector(-1)",
        DS_REF,
        16,
        y,
        mappings=_UPDOWN_MAP,
        w=8,
        h=4,
        value_size=_COMPACT_VALUE_SIZE,
    )
    return [initiator, command_server, listeners]


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


def work_qm_dashboard() -> dict[str, Any]:
    """The portable work-edition QM-view board (spec §7.1). Datasource-portable and
    QM-scoped: pick a Prometheus (`$datasource`) and a queue manager (`$qmgr`) on import;
    every panel follows. Returns the Grafana dashboard dict; `portable_dashboard` rewrites
    all datasources to the template variables (the portability contract)."""
    panels: list[dict[str, Any]] = [_banner(y=0)]
    panels.append(_row_header("① QM status — status · uptime · services · connections", y=2))
    panels.extend(_status_band(y=3))
    panels.append(_row_header("② Trends — message rate · recovery-log % · connections", y=7))
    panels.extend(_trend_band(y=8))
    panels.append(_row_header("③ Attention — which service piece is down", y=15))
    panels.extend(_attention_band(y=16))
    panels.append(_es_feed_seam(y=20))
    return portable_dashboard(
        "Queue Manager — Health & Trend (portable)",
        QM_BOARD_UID,
        panels,
        [datasource_var(), qmgr_var()],
        tags=["work", "portable", "qm"],
    )
