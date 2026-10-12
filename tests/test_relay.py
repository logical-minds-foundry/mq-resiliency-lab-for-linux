"""Grafana port-forward relay discovery (#1450): the unit + workstation port are read
from the installed vergil-portforward units, never assumed to be :3000."""

from __future__ import annotations

import subprocess

import pytest

from mqlab import relay
from mqlab.relay import list_relay_units as _real_list_relay_units  # before the autouse stub

ANONYMOUS = (
    "vergil-portforward-3001.service loaded active running "
    "Vergil port-forward relay (:3001 -> 10.50.0.2:3000)\n"
)
OTHER = (
    "vergil-portforward-8080.service loaded active running "
    "Vergil port-forward relay (:8080 -> 10.50.0.9:8080)\n"
)


def _listing(monkeypatch, text: str) -> None:
    monkeypatch.setattr(relay, "list_relay_units", lambda: text)


def test_grafana_backend_is_derived_from_grafana_url():
    assert relay.GRAFANA_BACKEND == "10.50.0.2:3000"


def test_vergil_user_profile_relay_is_port_3000():
    # the autouse conftest stub is the vergil-user listing
    (only,) = relay.grafana_relays()
    assert only == relay.Relay("vergil-portforward-3000.service", 3000, "10.50.0.2:3000")
    assert only.workstation_url == "http://localhost:3000"


def test_anonymous_profile_relay_is_port_3001(monkeypatch):
    # [vm.anonymous] forwards "3001|10.50.0.2:3000" — the unit and URL follow the port.
    _listing(monkeypatch, OTHER + ANONYMOUS)
    (only,) = relay.grafana_relays()
    assert only.unit == "vergil-portforward-3001.service"
    assert only.workstation_url == "http://localhost:3001"


def test_multiple_grafana_relays_are_all_returned_sorted_by_port(monkeypatch):
    twin = ANONYMOUS.replace("3001", "3000", 2)
    _listing(monkeypatch, ANONYMOUS + "\n" + twin)
    assert [r.port for r in relay.grafana_relays()] == [3000, 3001]


def test_no_grafana_relay_fails_loud_with_the_mqlab_remedy(monkeypatch):
    _listing(monkeypatch, OTHER)
    with pytest.raises(relay.RelayError) as exc:
        relay.grafana_relays()
    assert "10.50.0.2:3000" in str(exc.value)
    assert "port_forwards" in str(exc.value)
    assert "mqlab obs relay-heal" in str(exc.value)


def test_no_relay_units_at_all_fails_loud(monkeypatch):
    _listing(monkeypatch, "")
    with pytest.raises(relay.RelayError, match="no vergil port-forward relay"):
        relay.grafana_relays()


def test_unparseable_description_fails_loud_not_as_absent(monkeypatch):
    # a vergil-vm description drift must not masquerade as "no Grafana relay"
    _listing(monkeypatch, "vergil-portforward-3000.service loaded active running socat\n")
    with pytest.raises(relay.RelayError, match="cannot read the forward"):
        relay.grafana_relays()


def test_list_relay_units_runs_the_glass_box_command(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout=ANONYMOUS, stderr="")

    monkeypatch.setattr(relay.subprocess, "run", fake_run)
    assert _real_list_relay_units() == ANONYMOUS
    assert seen["argv"] == relay.RELAY_LIST_ARGV
    assert seen["argv"][:2] == ("systemctl", "list-units")
    assert seen["argv"][-1] == "vergil-portforward-*.service"
    assert seen["kwargs"]["check"] is True


def test_list_relay_units_failure_is_a_relay_error(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv, stderr="Failed to connect to bus")

    monkeypatch.setattr(relay.subprocess, "run", fake_run)
    with pytest.raises(relay.RelayError, match="Failed to connect to bus"):
        _real_list_relay_units()


def test_list_relay_units_missing_systemctl_is_a_relay_error(monkeypatch):
    def fake_run(argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "systemctl")

    monkeypatch.setattr(relay.subprocess, "run", fake_run)
    with pytest.raises(relay.RelayError, match="No such file"):
        _real_list_relay_units()
