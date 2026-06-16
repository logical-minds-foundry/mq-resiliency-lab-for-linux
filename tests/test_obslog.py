from __future__ import annotations

import json
from datetime import UTC, datetime

from mqlab.obslog import _format_ts, emit, format_line


def test_format_line_is_one_json_object_with_exactly_ts_level_msg():
    line = format_line("info", "hello world", "2026-06-12T14:03:01.123Z")
    obj = json.loads(line)
    assert obj == {"ts": "2026-06-12T14:03:01.123Z", "level": "info", "msg": "hello world"}
    assert "\n" not in line  # the newline is added by emit's print, not the record


def test_format_ts_is_rfc3339_utc_with_millis_and_z():
    ts = _format_ts(datetime(2026, 6, 12, 14, 3, 1, 123456, tzinfo=UTC))
    assert ts == "2026-06-12T14:03:01.123Z"


def test_emit_writes_a_single_json_line_to_an_injected_stream():
    import io

    buf = io.StringIO()
    fixed = datetime(2026, 6, 12, 14, 3, 1, 0, tzinfo=UTC)
    emit("warn", "ack 7: 0001", stream=buf, clock=lambda: fixed)
    out = buf.getvalue()
    assert out.endswith("\n")
    assert out.count("\n") == 1
    obj = json.loads(out)
    assert obj["level"] == "warn"
    assert obj["msg"] == "ack 7: 0001"
    assert obj["ts"] == "2026-06-12T14:03:01.000Z"


def test_emit_defaults_to_stdout_and_real_clock(capsys):
    emit("info", "default path")
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    obj = json.loads(out)
    assert obj["level"] == "info"
    assert obj["msg"] == "default path"
    # real clock -> RFC3339 UTC string ending in Z
    assert obj["ts"].endswith("Z")
