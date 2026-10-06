"""`mqlab component build|status` CLI (epic .github#294 T4)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mqlab import cli, component
from mqlab.runtime import RuntimePinError
from mqlab.versions import VersionError

runner = CliRunner()


@pytest.fixture
def known(monkeypatch):
    monkeypatch.setattr(component, "known_components", lambda: ["alpha", "beta"])


def test_build_needs_names_or_all(known):
    result = runner.invoke(cli.app, ["component", "build"])
    assert result.exit_code == 2
    assert "name at least one component or pass --all (components: alpha, beta)" in result.output


def test_build_with_no_components_yet(monkeypatch):
    monkeypatch.setattr(component, "known_components", list)
    result = runner.invoke(cli.app, ["component", "build"])
    assert result.exit_code == 2
    assert "components: none yet" in result.output


def test_build_unknown_component(known):
    result = runner.invoke(cli.app, ["component", "build", "gamma"])
    assert result.exit_code == 2
    assert "unknown component(s): gamma" in result.output


def test_build_all_builds_each(known, monkeypatch):
    built: list[str] = []
    monkeypatch.setattr(component, "build", lambda name, **_k: built.append(name))
    result = runner.invoke(cli.app, ["component", "build", "--all"])
    assert result.exit_code == 0
    assert built == ["alpha", "beta"]


def test_build_named(known, monkeypatch):
    built: list[str] = []
    monkeypatch.setattr(component, "build", lambda name, **_k: built.append(name))
    assert runner.invoke(cli.app, ["component", "build", "beta"]).exit_code == 0
    assert built == ["beta"]


@pytest.mark.parametrize(
    "error",
    [
        component.ComponentError("tests failed"),
        RuntimePinError("bad pin"),
        VersionError("bad yaml"),
    ],
)
def test_build_failure_exits_1_naming_the_component(known, monkeypatch, error):
    def boom(name, **_k):
        raise error

    monkeypatch.setattr(component, "build", boom)
    result = runner.invoke(cli.app, ["component", "build", "alpha", "beta"])
    assert result.exit_code == 1
    assert f"mqlab component build alpha: {error}" in result.output


def test_status_renders_every_component_by_default(known, monkeypatch):
    seen: list = []

    def render(names, _runner, hosts):
        seen.append((names, hosts))
        return "TABLE"

    monkeypatch.setattr(component, "render_status", render)
    result = runner.invoke(cli.app, ["component", "status", "--host", "a1", "--host", "a2"])
    assert result.exit_code == 0
    assert "TABLE" in result.output
    assert seen == [(["alpha", "beta"], ["a1", "a2"])]


def test_status_named(known, monkeypatch):
    seen: list = []
    monkeypatch.setattr(
        component, "render_status", lambda names, _r, hosts: seen.append(names) or "T"
    )
    assert runner.invoke(cli.app, ["component", "status", "beta"]).exit_code == 0
    assert seen == [["beta"]]


def test_status_with_no_components(monkeypatch):
    monkeypatch.setattr(component, "known_components", list)
    result = runner.invoke(cli.app, ["component", "status"])
    assert result.exit_code == 0
    assert "no components under components/ yet" in result.output


def test_status_unknown_component(known):
    assert runner.invoke(cli.app, ["component", "status", "gamma"]).exit_code == 2


def test_status_error_exits_1(known, monkeypatch):
    def boom(*_a):
        raise component.ComponentError("ansible returned no JSON")

    monkeypatch.setattr(component, "render_status", boom)
    result = runner.invoke(cli.app, ["component", "status"])
    assert result.exit_code == 1
    assert "mqlab component status: ansible returned no JSON" in result.output


def test_install_passes_hosts_and_version(known, monkeypatch):
    seen: list = []
    monkeypatch.setattr(
        component, "install", lambda name, hosts, **kw: seen.append((name, hosts, kw["wanted"]))
    )
    args = ["component", "install", "alpha", "--host", "a1", "--host", "a2", "--version", "0.2"]
    assert runner.invoke(cli.app, args).exit_code == 0
    assert seen == [("alpha", ["a1", "a2"], "0.2")]


def test_install_defaults_to_heads_artifact(known, monkeypatch):
    seen: list = []
    monkeypatch.setattr(component, "install", lambda name, hosts, **kw: seen.append(kw["wanted"]))
    assert runner.invoke(cli.app, ["component", "install", "beta", "--host", "a1"]).exit_code == 0
    assert seen == [None]


def test_install_requires_a_host(known):
    result = runner.invoke(cli.app, ["component", "install", "alpha"])
    assert result.exit_code == 2
    assert "--host" in result.output


def test_install_unknown_component(known):
    result = runner.invoke(cli.app, ["component", "install", "gamma", "--host", "a1"])
    assert result.exit_code == 2
    assert "unknown component(s): gamma" in result.output


@pytest.mark.parametrize(
    "error",
    [
        component.ComponentError("components/alpha has uncommitted changes — commit them"),
        RuntimePinError("bad pin"),
        VersionError("bad yaml"),
    ],
)
def test_install_failure_exits_1(known, monkeypatch, error):
    def boom(name, hosts, **_k):
        raise error

    monkeypatch.setattr(component, "install", boom)
    result = runner.invoke(cli.app, ["component", "install", "alpha", "--host", "a1"])
    assert result.exit_code == 1
    assert f"mqlab component install alpha: {error}" in result.output
