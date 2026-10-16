"""mqlab console open: two-hop access to the live mqweb (epic .github#341 spec §2.3)."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner, Result

from mqlab import cli, mqweb_endpoints, mqweb_probe, paths, relay
from mqlab import console as c
from mqlab.stacks import lab_stacks

if TYPE_CHECKING:
    from collections.abc import Callable

CONSOLE_RELAY = (
    "vergil-portforward-9443.service loaded active running "
    "Vergil port-forward relay (:9443 -> 127.0.0.1:19443)\n"
)
GRAFANA_RELAY = (
    "vergil-portforward-3000.service loaded active running "
    "Vergil port-forward relay (:3000 -> 10.50.0.2:3000)\n"
)
VERGIL_USER = relay.Relay("vergil-portforward-9443.service", 9443, "127.0.0.1:19443")


# --- forwarder -------------------------------------------------------------------------


def test_forward_argv_targets_live_instance_on_fixed_port() -> None:
    argv = c.forward_argv("https://10.10.1.92:9443", "ubuntu")
    assert argv[:2] == ["sudo", "systemd-run"]
    assert f"--unit={c.UNIT}" in argv
    assert "--uid=ubuntu" in argv
    assert f"TCP4-LISTEN:{c.FORWARD_PORT},bind=127.0.0.1,reuseaddr,fork" in argv
    assert argv[-1] == "TCP:10.10.1.92:9443"
    assert argv[argv.index("-T") + 1] == str(c.IDLE_TIMEOUT)


def test_forward_argv_defaults_the_mqweb_port() -> None:
    assert c.forward_argv("https://10.10.1.92", "u")[-1] == "TCP:10.10.1.92:9443"


def test_forwarder_replaces_previous_unit() -> None:
    # Review Focus 5: a re-run after failover stops the old forwarder first.
    assert c.stop_argv() == ["sudo", "systemctl", "stop", c.UNIT]


def test_forward_backend_is_the_vergil_relay_target() -> None:
    # vergil.toml port_forwards "9443|127.0.0.1:19443": the relay target is this forwarder.
    assert c.FORWARD_BACKEND == "127.0.0.1:19443"


class _Sock:
    def close(self) -> None:
        return None


def _connector(failures: int) -> tuple[Callable[[tuple[str, int], float], _Sock], list[float]]:
    calls: list[float] = []

    def connect(addr: tuple[str, int], timeout: float) -> _Sock:
        assert addr == ("127.0.0.1", c.FORWARD_PORT)
        calls.append(timeout)
        if len(calls) <= failures:
            raise ConnectionRefusedError("refused")
        return _Sock()

    return connect, calls


def test_wait_listening_retries_until_the_forwarder_accepts() -> None:
    connect, calls = _connector(failures=2)
    slept: list[float] = []
    c.wait_listening(timeout=1.0, interval=0.25, connect=connect, sleep=slept.append)
    assert len(calls) == 3
    assert slept == [0.25, 0.25]


def test_wait_listening_fails_loud_when_never_accepting() -> None:
    connect, calls = _connector(failures=99)
    slept: list[float] = []
    with pytest.raises(c.ConsoleError, match=r"not accepting on 127\.0\.0\.1:19443.*journalctl"):
        c.wait_listening(timeout=1.0, interval=0.25, connect=connect, sleep=slept.append)
    assert len(calls) == 4
    assert len(slept) == 3  # no pointless sleep after the last attempt


# --- hop 1: where the workstation comes from ---------------------------------------------


def test_workstation_access_gives_lima_and_cloud_urls_on_the_relay_port() -> None:
    text = c.workstation_access(VERGIL_USER)
    assert "https://localhost:9443/ibmmq/console/login.html" in text
    # Cloud: the VM's external IP on the same port, never a hardcoded address.
    cloud = "https://<the VM's external IP, from `vrg-vm list`>:9443/ibmmq/console/login.html"
    assert cloud in text
    assert "vergil-portforward-9443.service" in text
    assert "admits only tcp:3000" in text
    assert c.ACCESS_DOCS in text


def test_workstation_access_follows_the_relay_port() -> None:
    anonymous = relay.Relay("vergil-portforward-9444.service", 9444, "127.0.0.1:19443")
    text = c.workstation_access(anonymous)
    assert "https://localhost:9444/ibmmq/console/login.html" in text
    assert ">:9444/ibmmq/console/login.html" in text


# --- banner ------------------------------------------------------------------------------


def test_banner_names_url_logins_cert_warning_and_rerun_quirk() -> None:
    text = c.banner("nativeha-rhel-crr", "NHARCAPP", "https://10.10.1.92:9443", "ACCESS")
    assert "ACCESS" in text
    assert "NHARCAPP" in text
    assert "127.0.0.1:19443 -> 10.10.1.92:9443" in text
    for user in ("mqadmin", "mqreadonly", "mqoperator"):
        assert f"{user} / {user}" in text
    assert "warns once about the certificate" in text
    assert "Accept and\ncontinue" in text
    assert "re-run `mqlab console open nativeha-rhel-crr`." in text


def test_banner_rerun_keeps_the_site() -> None:
    text = c.banner("nativeha-rhel-crr", "NHARCAPP", "https://10.10.2.92:9443", "", "b")
    assert "re-run `mqlab console open nativeha-rhel-crr --site b`." in text


# --- CLI ---------------------------------------------------------------------------------

_RECS = [
    {
        "stack": "nativeha-rhel-crr",
        "kind": "active-instance",
        "endpoints": {
            "site-a": ["https://10.10.1.91:9443", "https://10.10.1.92:9443"],
        },
    },
    {"stack": "svc-sim", "kind": "counterparty", "endpoints": {"site-a": ["https://10.60.0.50:9443"]}},
]  # fmt: skip


class _Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.runs: list[tuple[list[str], bool]] = []
        self.asked: list[tuple[str, str, str, str]] = []
        self.states = {"https://10.10.1.91:9443": "replica", "https://10.10.1.92:9443": "running"}
        self.fail_forward = False
        self.listening = True
        monkeypatch.setattr(mqweb_endpoints, "lab_mqweb_endpoints", lambda: _RECS)
        monkeypatch.setattr(mqweb_probe, "qm_state", self.qm_state)
        monkeypatch.setattr(subprocess, "run", self.run)
        monkeypatch.setattr("getpass.getuser", lambda: "ubuntu")
        monkeypatch.setattr(c, "wait_listening", self.wait_listening)
        monkeypatch.setattr(relay, "list_relay_units", lambda: GRAFANA_RELAY + CONSOLE_RELAY)
        ca = paths.state("secrets", "pki", "ca", "app-org", "ca.crt")
        ca.parent.mkdir(parents=True, exist_ok=True)
        ca.write_text("CA")
        self.ca = ca

    def qm_state(self, url: str, qm: str, **kw: object) -> mqweb_probe.Answer:
        self.asked.append((url, qm, str(kw["user"]), str(kw["password"])))
        assert kw["cafile"] == self.ca
        state = self.states.get(url)
        return mqweb_probe.Answer(state, state or "unreachable (refused)")

    def run(self, argv: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        self.runs.append((argv, check))
        if check and self.fail_forward:
            raise subprocess.CalledProcessError(1, argv)
        return subprocess.CompletedProcess(argv, 0 if check else 5)

    def wait_listening(self) -> None:
        if not self.listening:
            raise c.ConsoleError("not accepting")

    def invoke(self, *args: str) -> Result:
        return CliRunner().invoke(cli.app, ["console", "open", *args])


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    return _Harness(monkeypatch)


def test_cli_unknown_stack_exits_2(harness: _Harness) -> None:
    result = harness.invoke("no-such-stack")
    assert result.exit_code == 2
    assert "unknown stack 'no-such-stack'" in result.stderr
    assert harness.runs == []


def test_cli_site_without_endpoints_exits_2(harness: _Harness) -> None:
    result = harness.invoke("nativeha-rhel-crr", "--site", "b")
    assert result.exit_code == 2
    assert "no site-b mqweb endpoints" in result.stderr


def test_cli_missing_lab_ca_exits_1_before_probing(harness: _Harness) -> None:
    harness.ca.unlink()
    result = harness.invoke("nativeha-rhel-crr")
    assert result.exit_code == 1
    assert "app-org CA" in result.stderr
    assert harness.asked == []


def test_cli_probe_error_exits_1_and_starts_nothing(harness: _Harness) -> None:
    harness.states = {"https://10.10.1.91:9443": "replica"}
    result = harness.invoke("nativeha-rhel-crr")
    assert result.exit_code == 1
    qm = lab_stacks()["nativeha-rhel-crr"].qm.qm_app
    assert f"{qm}: no candidate reports 'running'" in result.stderr
    assert harness.runs == []


def test_cli_success_replaces_the_forwarder_and_prints_the_banner(
    harness: _Harness,
) -> None:
    result = harness.invoke("nativeha-rhel-crr")
    assert result.exit_code == 0, result.output
    qm = lab_stacks()["nativeha-rhel-crr"].qm.qm_app
    # The probe logs in as mqreadonly and stops at the running instance.
    assert [a[0] for a in harness.asked] == ["https://10.10.1.91:9443", "https://10.10.1.92:9443"]
    assert {a[1:] for a in harness.asked} == {(qm, "mqreadonly", "mqreadonly")}
    # Stop first (unchecked: an absent unit is fine), then the checked forward.
    assert harness.runs == [
        (c.stop_argv(), False),
        (c.forward_argv("https://10.10.1.92:9443", "ubuntu"), True),
    ]
    out = result.stdout
    assert "https://localhost:9443/ibmmq/console/login.html" in out
    assert "external IP, from `vrg-vm list`>:9443" in out
    assert "mqadmin / mqadmin" in out
    assert "live instance https://10.10.1.92:9443" in out


def test_cli_svc_sim_probes_the_counterparty_qm(harness: _Harness) -> None:
    harness.states = {"https://10.60.0.50:9443": "running"}
    result = harness.invoke("svc-sim")
    assert result.exit_code == 0, result.output
    svc = next(iter(lab_stacks().values())).qm.qm_svc
    assert harness.asked[0][1] == svc


def test_cli_without_the_console_relay_exits_1(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(relay, "list_relay_units", lambda: GRAFANA_RELAY)
    result = harness.invoke("nativeha-rhel-crr")
    assert result.exit_code == 1
    assert "9443|127.0.0.1:19443" in result.stderr
    assert harness.runs == []


def test_cli_unreadable_relay_listing_exits_1(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> str:
        raise relay.RelayError("systemctl failed")

    monkeypatch.setattr(relay, "list_relay_units", broken)
    result = harness.invoke("nativeha-rhel-crr")
    assert result.exit_code == 1
    assert "systemctl failed" in result.stderr


def test_cli_forward_start_failure_exits_1(harness: _Harness) -> None:
    harness.fail_forward = True
    result = harness.invoke("nativeha-rhel-crr")
    assert result.exit_code == 1
    assert "starting the forwarder failed" in result.stderr


def test_cli_forwarder_not_listening_exits_1(harness: _Harness) -> None:
    harness.listening = False
    result = harness.invoke("nativeha-rhel-crr")
    assert result.exit_code == 1
    assert "not accepting" in result.stderr
    assert "mqadmin" not in result.stdout
