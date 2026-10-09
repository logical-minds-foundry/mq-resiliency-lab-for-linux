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
# Synthetic MQ levels: the cache key is whatever the caller passes (box.py passes the
# lab/mq-version pin), so the tests need no real version literal.
_VER = "1.2.3.4"
_OTHER_VER = "1.2.3.5"


def test_needs_exporter_binary_true_for_exporter_roles():
    # Keyed by box ROLE (#1274), so it holds on every OS major; None is a base OS box.
    assert mqexporter.needs_exporter_binary(["obs"])
    assert mqexporter.needs_exporter_binary([None, "mq-client"])


def test_needs_exporter_binary_false_otherwise():
    assert not mqexporter.needs_exporter_binary([])
    assert not mqexporter.needs_exporter_binary(["pcmk", "infra", None])


def test_exporter_binary_path_is_under_cache_mq_exporter_keyed_by_mq_version(tmp_path):
    # Keyed by the MQ level whose SDK it links (#1407): a pin bump lands in a fresh
    # directory, so the stale binary built against the old level is never reused.
    expected = tmp_path / "mq-exporter" / _VER / mqexporter.binary_name()
    assert mqexporter.exporter_binary_path(tmp_path, _VER) == expected
    assert mqexporter.exporter_binary_path(tmp_path, _OTHER_VER) != expected


def test_binary_name_is_arch_suffixed():
    assert mqexporter.binary_name("arm64") == "mq_prometheus-arm64"
    assert mqexporter.binary_name("x64") == "mq_prometheus-x64"


def test_target_arch_maps_host_machine(monkeypatch):
    # Both branches: the host machine string -> the build-target arch suffix (#1100).
    for machine, expected in (
        ("aarch64", "arm64"),
        ("arm64", "arm64"),
        ("x86_64", "x64"),
        ("amd64", "x64"),
    ):
        monkeypatch.setattr(mqexporter.platform, "machine", lambda m=machine: m)
        assert mqexporter._target_arch() == expected
        assert mqexporter.binary_name() == f"mq_prometheus-{expected}"


def test_ensure_returns_cached_binary_without_building(tmp_path):
    calls: list[tuple] = []
    out = mqexporter.ensure_mq_exporter_binary(
        tmp_path,
        _VER,
        exists=lambda _p: True,
        build=lambda *a: calls.append(a),
    )
    assert out == mqexporter.exporter_binary_path(tmp_path, _VER)
    assert calls == []  # cache-hit: no build


def test_ensure_builds_once_on_cache_miss(tmp_path):
    calls: list[tuple] = []
    out = mqexporter.ensure_mq_exporter_binary(
        tmp_path,
        _VER,
        exists=lambda _p: False,
        build=lambda root, outdir, ver: calls.append((root, outdir, ver)),
    )
    assert out == mqexporter.exporter_binary_path(tmp_path, _VER)
    # miss: built once, into the version-keyed cache dir, against that MQ level's SDK
    assert calls == [(tmp_path, out.parent, _VER)]


def test_build_ref_matches_role_default():
    # The version manifest reports the role's mq_exporter_ref; the container build uses
    # MQ_EXPORTER_REF. They must be the same pin so a bump moves together.
    defaults = yaml.safe_load(_ROLE_DEFAULTS.read_text())
    assert defaults["mq_exporter_ref"] == mqexporter.MQ_EXPORTER_REF
