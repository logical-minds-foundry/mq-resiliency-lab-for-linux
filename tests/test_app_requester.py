"""Unit tests for the app-requester round-trip signal (#429).

The pymqi MQ loop can't run here (no client libs), but the metric logic is pure —
app_requester imports pymqi lazily inside run(), so the module (and its
RoundTripStats / render_prom / write_textfile helpers) imports without it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "app_requester", _REPO / "clients" / "app_requester.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ar = _load()


def test_stats_counts_successes_and_failures():
    s = ar.RoundTripStats()
    s.record_success(3.0)
    s.record_success(150.0)
    s.record_failure()
    assert (s.total, s.failures, s.successes) == (3, 1, 2)
    assert s.sum_ms == 153.0


def test_render_prom_histogram_is_cumulative_and_wellformed():
    s = ar.RoundTripStats()
    s.record_success(3.0)  # lands in le=5 and every larger bucket
    s.record_success(75.0)  # lands in le=100 and up
    s.record_failure()  # an attempt, not a latency observation
    text = ar.render_prom(s)
    assert "app_roundtrip_total 3" in text
    assert "app_roundtrip_failures_total 1" in text
    # cumulative buckets: 3ms only in le=5; both in le=100
    assert 'app_roundtrip_latency_ms_bucket{le="5"} 1' in text
    assert 'app_roundtrip_latency_ms_bucket{le="100"} 2' in text
    assert 'app_roundtrip_latency_ms_bucket{le="+Inf"} 2' in text
    assert "app_roundtrip_latency_ms_count 2" in text  # observations = successes
    assert "app_roundtrip_latency_ms_sum 78" in text
    # HELP/TYPE headers present (a valid exposition)
    assert "# TYPE app_roundtrip_latency_ms histogram" in text
    assert "# TYPE app_roundtrip_total counter" in text


def test_write_textfile_is_atomic_and_world_readable(tmp_path):
    p = tmp_path / "app_roundtrip.prom"
    ar.write_textfile(str(p), "hello\n")
    assert p.read_text() == "hello\n"
    assert p.stat().st_mode & 0o044  # group+world readable (node-exporter reads it)
    assert list(tmp_path.iterdir()) == [p]  # no leftover temp file


def test_default_args_stream_forever_at_one_per_second():
    args = ar._build_args([])
    assert args.count == 0  # 0 == infinite (the service default)
    assert args.rate == 1.0
    assert args.textfile == ""  # metrics off unless a path is given
