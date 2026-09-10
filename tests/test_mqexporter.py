"""mq_prometheus exporter build/cache orchestration (#1065).

The binary is built once in the Go container and cached; the `mq-exporter` role
copies it in. These tests cover the host-side ensure/gate logic with injected seams
(no real container runs) and lock the build ref to the role default the version
manifest reports.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from mqlab import mqexporter

_ROLE_DEFAULTS = (
    Path(__file__).resolve().parents[1]
    / "ansible"
    / "roles"
    / "mq-exporter"
    / "defaults"
    / "main.yml"
)


def test_needs_exporter_binary_true_for_exporter_boxes():
    assert mqexporter.needs_exporter_binary(["obs-ubuntu2404"])
    assert mqexporter.needs_exporter_binary(["rhel/9.6-x86_64", "mq-ubuntu2404"])


def test_needs_exporter_binary_false_otherwise():
    assert not mqexporter.needs_exporter_binary([])
    assert not mqexporter.needs_exporter_binary(["logsearch-ubuntu2404", "infra-ubuntu2404"])


def test_exporter_binary_path_is_under_cache_mq_exporter(tmp_path):
    expected = tmp_path / "mq-exporter" / "mq_prometheus-x64"
    assert mqexporter.exporter_binary_path(tmp_path) == expected


def test_ensure_returns_cached_binary_without_building(tmp_path):
    calls: list[tuple] = []
    out = mqexporter.ensure_mq_exporter_binary(
        tmp_path,
        exists=lambda _p: True,
        build=lambda *a: calls.append(a),
    )
    assert out == mqexporter.exporter_binary_path(tmp_path)
    assert calls == []  # cache-hit: no build


def test_ensure_builds_once_on_cache_miss(tmp_path):
    calls: list[tuple] = []
    out = mqexporter.ensure_mq_exporter_binary(
        tmp_path,
        exists=lambda _p: False,
        build=lambda root, outdir: calls.append((root, outdir)),
    )
    assert out == mqexporter.exporter_binary_path(tmp_path)
    assert calls == [(tmp_path, out.parent)]  # miss: built once, into the cache dir


def test_build_ref_matches_role_default():
    # The version manifest reports the role's mq_exporter_ref; the container build uses
    # MQ_EXPORTER_REF. They must be the same pin so a bump moves together.
    defaults = yaml.safe_load(_ROLE_DEFAULTS.read_text())
    assert defaults["mq_exporter_ref"] == mqexporter.MQ_EXPORTER_REF
