"""Every bring-up path ensures its baked boxes before `vagrant up` (#1008).

A cold lab has the box images baked in the state bucket but none registered with
Vagrant, so a bare `vagrant up` fails with "Box ... could not be found". bootstrap's
vms phase ran the box ensure; `mqlab commons up` did not. The fix is one shared helper
(cli._ensure_boxes) that every bring-up path calls — and a structural tripwire here so a
future `vagrant up` path cannot silently skip it.
"""

from __future__ import annotations

import ast
import io
from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.phases import PHASES, VMS
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests import test_cli_commons as com
from tests.fakes import RecordingRunner

_SRC = Path(cli.__file__).parent


class _NoPause:
    def wait(self) -> None:
        return None


class _EventRunner(RecordingRunner):
    """Exit 0 for every command, logging each argv into a shared event list so the
    test can order step-runner commands against the inline box ensure."""

    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    def run(self, command, on_line) -> int:  # noqa: ANN001, ARG002
        self.recorded.append(command)
        self.events.append(" ".join(command.argv))
        return 0


def _deps(runner: RecordingRunner) -> cli.Deps:
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("box-ensure", "20261012T000000Z")),
        pauser=_NoPause(),
    )


# --------------------------------------------------------------------------- #
# the shared helper
# --------------------------------------------------------------------------- #
def test_ensure_boxes_reconciles_then_ensures(monkeypatch):
    calls: list[tuple[str, list[str], bool | None]] = []
    monkeypatch.setattr(
        cli, "_reconcile_box_meta", lambda g, *, step: calls.append(("reconcile", g, step))
    )
    monkeypatch.setattr(cli, "_ensure_local_boxes", lambda g: calls.append(("ensure", g, None)))
    cli._ensure_boxes(["obs", "mon-probe"], step=True)
    assert calls == [
        ("reconcile", ["obs", "mon-probe"], True),
        ("ensure", ["obs", "mon-probe"], None),
    ]


def test_ensure_boxes_times_each_step_under_its_label(monkeypatch):
    labels: list[str] = []

    def timed(label, fn) -> None:  # noqa: ANN001
        labels.append(label)
        fn()

    cli._ensure_boxes(["obs"], step=False, timed=timed)
    assert labels == ["reconcile box meta", "box ensure"]


# --------------------------------------------------------------------------- #
# commons up: boxes ensured for every commons member, before any `vagrant up`
# --------------------------------------------------------------------------- #
def test_commons_prereqs_ensure_boxes_for_every_commons_member_first(monkeypatch, tmp_path):
    com._seed(monkeypatch, tmp_path)
    order: list[str] = []
    guests_seen: list[list[str]] = []

    def ensure(guests: list[str]) -> None:
        guests_seen.append(guests)
        order.append("boxes")

    monkeypatch.setattr(cli, "_ensure_local_boxes", ensure)
    monkeypatch.setattr(cli, "ensure_mq_tarballs_for_boxes", lambda *a, **k: order.append("mq"))
    monkeypatch.setattr(cli, "_execute", lambda verb, steps, **k: order.append(verb))

    cli._ensure_prereqs_for_commons()

    assert guests_seen == [["obs", "mon-probe", "svc-sim", "app-client"]]
    assert order == ["boxes", "mq", "prerequisites"]


def test_commons_up_ensures_boxes_before_any_vagrant_up(monkeypatch, tmp_path):
    """End-to-end through the verb, with the real _ensure_prereqs_for_commons: the box
    ensure runs before the first `vagrant up` the commons-up steps issue."""
    com._seed(monkeypatch, tmp_path)
    events: list[str] = []
    monkeypatch.setattr(cli, "_ensure_local_boxes", lambda g: events.append("box ensure"))
    monkeypatch.setattr(cli, "ensure_mq_tarballs_for_boxes", lambda *a, **k: None)
    runner = _EventRunner(events)
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "up"])

    assert result.exit_code == 0, result.output
    ups = [i for i, e in enumerate(events) if e.startswith("vagrant up")]
    assert ups, events
    assert events.index("box ensure") < ups[0]


def test_bootstrap_vms_phase_declares_the_box_ensure():
    """The vms phase (the bootstrap `vagrant up`) declares `boxes`, which
    _ensure_prereqs_for_stack dispatches to the same _ensure_boxes."""
    vms = next(p for p in PHASES if p.name == VMS)
    assert "boxes" in vms.ensure


# --------------------------------------------------------------------------- #
# structural tripwire: every `vagrant up` call site is a known, box-ensured path
# --------------------------------------------------------------------------- #
# Each function that builds a `vagrant up` argv, mapped to the bring-up path whose
# prereq stage runs _ensure_boxes before it. A new `vagrant up` site fails this test
# until it is wired through the box ensure AND registered here.
_BOX_ENSURED_VAGRANT_UP_SITES = {
    ("phases.py", "_vms_build_steps"): "bootstrap vms phase (Phase.ensure 'boxes')",
    ("cli.py", "_obs_up_steps"): "mqlab commons up (_ensure_prereqs_for_commons)",
    ("cli.py", "_commons_up_steps"): "mqlab commons up (_ensure_prereqs_for_commons)",
}


def _is_vagrant_up(node: ast.AST) -> bool:
    """A `Command(["vagrant", "up", ...], ...)` — a step that actually ISSUES the boot
    (an argv comparison such as perfrun's `argv[:2] == ["vagrant", "up"]` is not)."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        return False
    if node.func.id != "Command" or not node.args or not isinstance(node.args[0], ast.List):
        return False
    head = [e.value for e in node.args[0].elts[:2] if isinstance(e, ast.Constant)]
    return head == ["vagrant", "up"]


def _vagrant_up_sites() -> set[tuple[str, str]]:
    sites: set[tuple[str, str]] = set()
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and any(_is_vagrant_up(n) for n in ast.walk(fn)):
                sites.add((path.relative_to(_SRC).as_posix(), fn.name))
    return sites


def test_vagrant_up_detector_ignores_argv_comparisons():
    assert not _is_vagrant_up(ast.parse('argv[:2] == ["vagrant", "up"]', mode="eval").body)
    assert not _is_vagrant_up(ast.parse('Command(["vagrant", "ssh", g])', mode="eval").body)
    assert _is_vagrant_up(ast.parse('Command(["vagrant", "up", g], cwd=x)', mode="eval").body)


def test_every_vagrant_up_site_is_box_ensured():
    assert _vagrant_up_sites() == set(_BOX_ENSURED_VAGRANT_UP_SITES)


def test_commons_up_step_builders_only_reached_from_commons_up():
    """The commons `vagrant up` builders are reached only via `commons up`, whose prereq
    stage runs the box ensure — no other verb can issue their `vagrant up` unguarded."""
    tree = ast.parse((_SRC / "cli.py").read_text())
    builders = {"_obs_up_steps", "_commons_up_steps"}
    callers: dict[str, set[str]] = {b: set() for b in builders}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in builders
            ):
                callers[node.func.id].add(fn.name)
    assert callers == {"_obs_up_steps": {"_commons_up_steps"}, "_commons_up_steps": {"commons_up"}}
