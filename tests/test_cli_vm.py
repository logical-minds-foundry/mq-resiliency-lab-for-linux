from __future__ import annotations

import io

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.cli import _ensure_local_boxes as _real_ensure_local_boxes  # captured before the stub
from mqlab.hostfacts import AARCH64, X86_64, HostFacts
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult

_VIRSH = ["virsh", "-c", "qemu:///system"]


class _NoPause:
    def wait(self) -> None:
        return None


def _deps(runner, pauser):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("vm", "20260609T000000Z")),
        pauser=pauser,
    )


# --- vm inventory / roster: render the static maps from topology (stacks) ---


def test_vm_inventory_writes_and_echoes(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  san-a: {nics: {net-mgmt: 10.50.0.5}}\n"
        "groups:\n  san_a: [san-a]\n"
        "stacks:\n  pcmk-ubuntu: {short: PCMK, groups: [san_a]}\n"
    )
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "inventory"])
    assert result.exit_code == 0
    written = (tmp_path / "build" / "work" / "inventory.ini").read_text()
    assert "[san_a]" in written
    assert "san-a ansible_host=10.50.0.5" in written
    assert "[pcmk_ubuntu:children]" in written


def test_vm_roster_writes_and_echoes(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  san-a: {nics: {net-mgmt: 10.50.0.5}}\n"
        "groups:\n  san_a: [san-a]\n"
        "stacks:\n  pcmk-ubuntu: {short: PCMK, groups: [san_a]}\n"
    )
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "roster"])
    assert result.exit_code == 0
    written = (tmp_path / "build" / "work" / "salt" / "roster").read_text()
    assert "san-a:" in written
    assert "host: 10.50.0.5" in written
    assert "roster_groups:\n      - san_a" in written
    assert "roster_stacks:\n      - pcmk-ubuntu" in written


# --- vm ssh: TTY passthrough; runs from lab/ against the shared dotfile (#355) ---


def test_vm_ssh_execs_vagrant_in_lab(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    chdirs: list[str] = []
    execs: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(cli.os, "chdir", lambda p: chdirs.append(str(p)))
    monkeypatch.setattr(cli.os, "execvp", lambda f, a: execs.append((f, a)))
    result = CliRunner().invoke(cli.app, ["vm", "ssh", "node-a1"])
    assert result.exit_code == 0
    assert execs == [("vagrant", ["vagrant", "ssh", "node-a1"])]
    assert chdirs and chdirs[0].endswith("/lab")
    # vagrant ssh points at the shared dotfile so it finds a lab this checkout didn't create (#355)
    assert cli.os.environ["VAGRANT_DOTFILE_PATH"].endswith("build/state/vagrant")


def test_fetch_mq_tarball_delegates_to_download(monkeypatch, tmp_path):
    # the manifest fetch callback now auto-downloads (no-auth CDN) instead of raising (#276)
    calls = {}
    monkeypatch.setattr(
        cli, "download_mq_tarball", lambda name, dest: calls.update(name=name, dest=dest)
    )
    name = "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"
    cli._fetch_mq_tarball(name, tmp_path / "t")
    assert calls == {"name": name, "dest": tmp_path / "t"}


# --- local box auto-build (#276/#291): build/register the RHEL box on a fresh box ---
def test_parse_box_list():
    txt = "rhel/9.6-x86_64          (libvirt, 0, (arm64))\ncloud-image/ubuntu-24.04 (libvirt, 1)\n"
    assert cli.parse_box_list(txt) == {
        "rhel/9.6-x86_64": "(libvirt, 0, (arm64))",
        "cloud-image/ubuntu-24.04": "(libvirt, 1)",
    }


def test_parse_box_list_no_boxes():
    assert cli.parse_box_list("There are no installed boxes!\n") == {}


def _seed_resolved(tmp_path, body):
    (tmp_path / "build" / "work" / "lab").mkdir(parents=True)
    (tmp_path / "build" / "work" / "lab" / "topology.resolved.yaml").write_text(body)


def test_box_build_steps_passes_kvm_args(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    facts = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True)
    steps = cli._box_build_steps({"rhel/9.6-x86_64": "lab/boxes/rhel96/build-box.sh"}, {}, facts)
    assert [s.command.argv for s in steps] == [
        [
            "bash",
            str(tmp_path / "lab/boxes/rhel96/build-box.sh"),
            "--domain-type",
            "kvm",
            "--cpu-mode",
            "host-passthrough",
        ],
    ]


def test_box_build_steps_passes_tcg_args_on_arm(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    facts = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
    steps = cli._box_build_steps({"rhel/9.6-x86_64": "lab/boxes/rhel96/build-box.sh"}, {}, facts)
    assert steps[0].command.argv[-4:] == ["--domain-type", "qemu", "--cpu-mode", "maximum"]


def test_box_build_steps_skips_present(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    facts = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True)
    steps = cli._box_build_steps(
        {
            "mq-rdqm-rhel9": "lab/boxes/build-fatbox.sh",
            "obs-ubuntu2404": "lab/boxes/build-fatbox.sh",
        },
        {"mq-rdqm-rhel9": "(libvirt, 0)"},  # already registered -> skipped
        facts,
    )
    assert [s.label for s in steps] == ["box obs-ubuntu2404"]


# --- ensure_local_boxes now delegates box building to box.build_boxes (#91, T2),
#     keeping the DVD staging step inline. Bootstrap + `mqlab box build` share one core.
def test_ensure_local_boxes_delegates_to_build_core(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_resolved(tmp_path, "nodes:\n  rdqm-a1: {box: rhel/9.6-x86_64}\n")
    calls: dict = {}
    monkeypatch.setattr(
        cli.box, "build_boxes", lambda names, *, force: calls.update(names=names, force=force)
    )
    _real_ensure_local_boxes(["rdqm-a1"])
    assert calls == {"names": ["rhel/9.6-x86_64"], "force": False}


def test_ensure_local_boxes_noop_when_no_local_box(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_resolved(tmp_path, "nodes:\n  obs: {box: cloud-image/ubuntu-24.04}\n")
    built: list = []
    monkeypatch.setattr(cli.box, "build_boxes", lambda names, *, force: built.append(names))
    _real_ensure_local_boxes(["obs"])  # no local box + no dvd -> no build, no staging
    assert built == []


def test_guests_need_dvd(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_resolved(
        tmp_path,
        "nodes:\n  rdqm-a1: {box: rhel/9.6-x86_64, dvd: /pool/rhel.iso}\n"
        "  obs: {box: cloud-image/ubuntu-24.04}\n",
    )
    assert cli._guests_need_dvd(["rdqm-a1"]) is True
    assert cli._guests_need_dvd(["obs"]) is False


def test_ensure_local_boxes_stages_dvd(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_resolved(tmp_path, "nodes:\n  rdqm-a1: {box: rhel/9.6-x86_64, dvd: /pool/rhel.iso}\n")
    # box building is delegated away; only the DVD staging remains inline here
    monkeypatch.setattr(cli.box, "build_boxes", lambda names, *, force: None)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    _real_ensure_local_boxes(["rdqm-a1"])
    assert [c.argv for c in runner.recorded] == [
        ["bash", str(tmp_path / "lab/scripts/stage-rhel-iso.sh")]
    ]


def test_ensure_local_boxes_dvd_only_skips_build(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    # a (hypothetical) cloud box with a dvd -> no local box to build, but the dvd is staged
    _seed_resolved(tmp_path, "nodes:\n  n1: {box: cloud-image/ubuntu-24.04, dvd: /pool/x.iso}\n")
    built: list = []
    monkeypatch.setattr(cli.box, "build_boxes", lambda names, *, force: built.append(names))
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    _real_ensure_local_boxes(["n1"])
    assert built == []
    assert [c.argv for c in runner.recorded] == [
        ["bash", str(tmp_path / "lab/scripts/stage-rhel-iso.sh")]
    ]


def test_ensure_local_boxes_dvd_stage_failure_exits(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_resolved(tmp_path, "nodes:\n  rdqm-a1: {box: rhel/9.6-x86_64, dvd: /pool/rhel.iso}\n")
    monkeypatch.setattr(cli.box, "build_boxes", lambda names, *, force: None)
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=1)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    with pytest.raises(typer.Exit):
        _real_ensure_local_boxes(["rdqm-a1"])
