"""Box-builder wiring for guest components (epic .github#294 T5, spec §5.8).

No catalog role bakes a component yet (T7/T9 add them), so these tests give a fleet box a
component with dataclasses.replace and drive box.py with every git/builder seam stubbed;
the shell half exercises only build-fatbox.sh's side-effect-free arg validation and
--dry-run, plus the sourced _components.sh helpers.
"""

from __future__ import annotations

import dataclasses
import io
import json
import os
import subprocess
from pathlib import Path

import pytest
import typer
from rich.console import Console

from mqlab import box, cli, component
from mqlab.hostfacts import X86_64, HostFacts
from mqlab.render import Renderer
from mqlab.runtime import RuntimePinError
from mqlab.transcript import Transcript, transcript_path
from mqlab.versions import VersionError
from tests.boxfleet import FAKE_TREE, fatbox_args
from tests.fakes import RecordingRunner

_REPO = Path(__file__).resolve().parents[1]
_BOXES = _REPO / "lab" / "boxes"
_FATBOX = _BOXES / "build-fatbox.sh"
_FACTS = HostFacts(arch=X86_64, kvm=True, distro_family="apt", in_vergil=True)
OBS = "mq-resiliency-observability"
TREE = "f" * 40
PIN = box.FLEET["pcmk-ubuntu24"].runtime_pin


class _NoPause:
    def wait(self) -> None:
        return None


def _fake_deps() -> cli.Deps:
    return cli.Deps(
        runner=RecordingRunner(results=[]),
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("box-build", "20261006T000000Z")),
        pauser=_NoPause(),
    )


@pytest.fixture
def baking(monkeypatch: pytest.MonkeyPatch) -> box.BoxSpec:
    """pcmk-ubuntu24 baking the observability component, its tree hash stubbed."""
    spec = dataclasses.replace(box.FLEET["pcmk-ubuntu24"], components=(OBS,))
    monkeypatch.setattr(box, "FLEET", {**box.FLEET, spec.name: spec})
    monkeypatch.setattr(box, "_component_tree", lambda name: TREE)
    return spec


def _decide(action: str):
    return lambda name: box.BoxDecision(
        name=name, cached=True, age_days=None, hash_match=True, registered=True, action=action
    )


def _stub_build(monkeypatch: pytest.MonkeyPatch, events: list, action: str) -> None:
    monkeypatch.setattr(box, "probe", lambda: _FACTS)
    monkeypatch.setattr(box, "ensure_resolved", lambda: None)
    monkeypatch.setattr(box, "box_decision", _decide(action))
    monkeypatch.setattr(box.cli, "build_deps", lambda verb, ts: _fake_deps())
    monkeypatch.setattr(
        box, "run_steps", lambda steps, **kw: events.append(("run", [s.label for s in steps]))
    )

    def ensure_built(name, *, runner, on_line):
        events.append(("ensure_built", name))
        return component.Artifact(name, "0.1.0", TREE, Path(f"/c/{name}"), {})

    def install_vars(artifacts, arch=None):
        events.append(("install_vars", [a.name for a in artifacts], arch))
        return Path("/w/install-vars.json")

    monkeypatch.setattr(box.component, "ensure_built", ensure_built)
    monkeypatch.setattr(box.component, "install_vars", install_vars)


# --- box.py: builder args ----------------------------------------------------------------


def test_builder_args_carry_components_and_pin(baking):
    argv = box.builder_args(baking, _FACTS)
    assert argv[argv.index("--runtime-pin") + 1] == PIN
    assert argv[argv.index("--components") + 1] == f"{OBS}@{TREE}"
    assert argv[argv.index("--install-vars") + 1] == str(component.install_vars_path())


def test_builder_args_for_a_box_baking_nothing_pass_an_empty_list():
    argv = box.builder_args(box.FLEET["obs-ubuntu24"], _FACTS)
    assert argv[argv.index("--runtime-pin") + 1] == PIN
    assert argv[argv.index("--components") + 1] == ""
    assert "--install-vars" not in argv


def test_runtime_pin_comes_from_the_catalog():
    assert box.load_catalog().runtime.token == PIN
    assert box.FLEET["rhel/9-x86_64"].runtime_pin == ""  # a base box bakes no runtime


def test_builder_args_uncommitted_component_exits_1(baking, monkeypatch, capsys):
    def boom(name):
        raise component.ComponentError(f"components/{name} is not committed at HEAD")

    monkeypatch.setattr(box, "_component_tree", boom)
    with pytest.raises(typer.Exit) as exc:
        box.builder_args(baking, _FACTS)
    assert exc.value.exit_code == 1
    assert f"mqlab box: pcmk-ubuntu24: components/{OBS} is not committed" in capsys.readouterr().err


def test_component_tree_is_the_git_tree_hash(monkeypatch):
    seen: list = []

    def tree_hash(name, runner):
        seen.append(name)
        return TREE

    monkeypatch.setattr(box.component, "tree_hash", tree_hash)
    assert box._component_tree(OBS) == TREE
    assert seen == [OBS]


# --- box.py: build what a bake needs first -------------------------------------------------


def test_box_build_builds_missing_component_artifact(baking, monkeypatch):  # Review Focus 2
    events: list = []
    _stub_build(monkeypatch, events, "BUILD")
    box.build_boxes(["pcmk-ubuntu24"], force=False)
    assert events == [
        ("ensure_built", OBS),
        ("install_vars", [OBS], X86_64),
        ("run", ["box pcmk-ubuntu24"]),
    ]


def test_box_build_forced_bake_ensures_components(baking, monkeypatch):
    events: list = []
    _stub_build(monkeypatch, events, "REUSE")
    box.build_boxes(["pcmk-ubuntu24"], force=True)
    assert events[0] == ("ensure_built", OBS)


def test_box_reuse_builds_no_component(baking, monkeypatch):
    events: list = []
    _stub_build(monkeypatch, events, "REUSE")
    box.build_boxes(["pcmk-ubuntu24"], force=False)
    assert events == [("run", ["box pcmk-ubuntu24"])]


def test_box_build_of_boxes_baking_nothing_builds_no_component(monkeypatch):
    events: list = []
    _stub_build(monkeypatch, events, "BUILD")
    box.build_boxes(["infra-ubuntu24"], force=False)
    assert events == [("run", ["box infra-ubuntu24"])]


@pytest.mark.parametrize(
    "error",
    [
        component.ComponentError("components/x has uncommitted changes"),
        RuntimePinError("sha mismatch"),
        VersionError("bad catalog"),
    ],
)
def test_box_build_component_failure_exits_1_before_baking(baking, monkeypatch, capsys, error):
    events: list = []
    _stub_build(monkeypatch, events, "BUILD")

    def boom(name, *, runner, on_line):
        raise error

    monkeypatch.setattr(box.component, "ensure_built", boom)
    with pytest.raises(typer.Exit) as exc:
        box.build_boxes(["pcmk-ubuntu24"], force=False)
    assert exc.value.exit_code == 1
    assert f"mqlab box build: {error}" in capsys.readouterr().err
    assert events == []  # nothing was baked


# --- box.py: what a box baked --------------------------------------------------------------


def _record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr(box, "_boxes_cache_dir", lambda: tmp_path)
    (tmp_path / box.FLEET["pcmk-ubuntu24"].components_record_artifact).write_text(text)


def test_box_status_shows_baked_components(monkeypatch, tmp_path):
    _record(
        tmp_path,
        monkeypatch,
        json.dumps({"runtime": PIN, "components": {OBS: TREE, "a-comp": "1" * 40}}),
    )
    monkeypatch.setattr(box, "box_decision", _decide("REUSE"))
    out = box.render_status(["pcmk-ubuntu24", "san-ubuntu24"]).splitlines()
    assert out[0].endswith("COMPONENTS")
    assert out[1].endswith(f"a-comp@{'1' * 12},{OBS}@{'f' * 12} ({PIN})")
    assert out[2].endswith(" -")  # no record: bakes no component (or is not cached)


def test_baked_components_of_a_base_box_is_a_dash(monkeypatch, tmp_path):
    monkeypatch.setattr(box, "_boxes_cache_dir", lambda: tmp_path)
    assert box.baked_components("rhel/9-x86_64") == "-"


@pytest.mark.parametrize("text", ["not json", "[]", '{"runtime": "x"}', '{"components": {}}'])
def test_baked_components_rejects_a_bad_record(monkeypatch, tmp_path, text):
    _record(tmp_path, monkeypatch, text)
    with pytest.raises(ValueError, match="mqlab box rebuild pcmk-ubuntu24"):
        box.baked_components("pcmk-ubuntu24")


def test_clean_removes_the_components_record(monkeypatch, tmp_path):
    _record(tmp_path, monkeypatch, "{}")
    monkeypatch.setattr(box, "_vagrant_box_remove", lambda name: None)
    record = tmp_path / "pcmk-ubuntu24-x86_64.components.json"
    assert str(record) in box.clean_boxes(["pcmk-ubuntu24"])
    assert not record.exists()


# --- build-fatbox.sh ---------------------------------------------------------------------


def _fatbox(*args: str, cache_dir: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "LAB_BOX_CACHE_DIR": str(cache_dir)}
    return subprocess.run(  # noqa: S603
        ["bash", str(_FATBOX), *args], capture_output=True, text=True, check=False, env=env
    )


def _with(args: list[str], flag: str, value: str) -> list[str]:
    out = list(args)
    out[out.index(flag) + 1] = value
    return out


def _without(args: list[str], flag: str) -> list[str]:
    i = args.index(flag)
    return args[:i] + args[i + 2 :]


@pytest.mark.parametrize("flag", ["--runtime-pin", "--components"])
def test_fatbox_requires_runtime_pin_and_components(flag, tmp_path):
    r = _fatbox(*_without(fatbox_args("pcmk-ubuntu24"), flag), "--dry-run", cache_dir=tmp_path)
    assert r.returncode == 2
    assert f"ERROR: {flag} is required" in r.stderr


def test_fatbox_requires_install_vars_with_components(tmp_path):
    args = _with(fatbox_args("obs-ubuntu24"), "--components", "demo@aaa")
    r = _fatbox(*args, "--dry-run", cache_dir=tmp_path)
    assert r.returncode == 2
    assert "--install-vars is required when --components is non-empty" in r.stderr


def test_fatbox_rejects_malformed_components(tmp_path):
    args = _with(fatbox_args("pcmk-ubuntu24"), "--components", "demo")
    r = _fatbox(*args, "--install-vars", "/x.json", "--dry-run", cache_dir=tmp_path)
    assert r.returncode == 2
    assert "--components entries are <name>@<tree hash>" in r.stderr


def test_fatbox_components_needs_a_value(tmp_path):
    r = _fatbox(*fatbox_args("pcmk-ubuntu24"), "--components", cache_dir=tmp_path)
    assert r.returncode == 2
    assert "--components needs a value" in r.stderr


def test_fatbox_dry_run_with_components_hashes_them(tmp_path):
    # A dry run (box status) never reads the install vars: only a real bake installs.
    base = [*fatbox_args("pcmk-ubuntu24"), "--install-vars", str(tmp_path / "absent.json")]
    cache = tmp_path / "boxes"
    a = _fatbox(*_with(base, "--components", "demo@aaa"), "--dry-run", cache_dir=cache)
    assert a.returncode == 0, a.stderr
    assert "decision:  BUILD" in a.stdout
    # Seed the cache at the demo@aaa hash: the same tree REUSEs, a new tree rebuilds.
    digest = subprocess.run(  # noqa: S603
        [
            "bash",
            str(_BOXES / "_manifest-hash.sh"),
            "pcmk-ubuntu24",
            "--bake-stem",
            "pcmk-ubuntu",
            "--mq-bearing",
            "0",
            "--os-pin",
            base[base.index("--os-pin") + 1],
            "--runtime-pin",
            PIN,
            "--components",
            "demo@aaa",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    cache.mkdir(exist_ok=True)
    (cache / "pcmk-ubuntu24-x86_64.box").write_text("box")
    (cache / "pcmk-ubuntu24-x86_64.manifest-hash").write_text(digest + "\n")
    same = _fatbox(*_with(base, "--components", "demo@aaa"), "--dry-run", cache_dir=cache)
    assert "decision:  REUSE" in same.stdout, same.stderr
    edited = _fatbox(*_with(base, "--components", "demo@bbb"), "--dry-run", cache_dir=cache)
    assert "decision:  BUILD" in edited.stdout, edited.stderr


def test_fatbox_args_pins_component_trees(monkeypatch):
    # The shared helper stands in FAKE_TREE for git, so builder argv stays git-free.
    spec = dataclasses.replace(box.FLEET["pcmk-ubuntu24"], components=(OBS,))
    monkeypatch.setattr(box, "_build_fleet", lambda facts: {spec.name: spec})
    argv = fatbox_args("pcmk-ubuntu24")
    assert argv[argv.index("--components") + 1] == f"{OBS}@{FAKE_TREE}"


def _fatbox_text() -> str:
    return _FATBOX.read_text()


def test_fatbox_hands_the_bake_the_install_vars_and_component_list():
    text = _fatbox_text()
    wiring = text.index('BAKE_EXTRA_VARS+=(-e "@$INSTALL_VARS"')
    assert 'test -f "$INSTALL_VARS"' in text[text.rindex("\n\nif", 0, wiring) : wiring]
    assert "baked_components" in text[wiring : text.index("\n", wiring)]
    # ...and both reach ansible-playbook (the extra vars are spliced into the bake run).
    assert wiring < text.index('ansible-playbook -i "$BUILD_INV"')


def test_box_build_records_baked_components():
    # The builder's post-bake step records what was baked, right after stamping the
    # manifest hash; a bake of no component removes any stale record.
    text = _fatbox_text()
    stamp = text.index('> "$HASH_FILE"   # stamp the manifest hash')
    record = text.index('components_record "$RUNTIME_PIN" "$COMPONENTS" > "$COMPONENTS_FILE.tmp"')
    assert stamp < record < text.index('box_register "$BOX" "$(box_reg_identity')
    assert 'rm -f "$COMPONENTS_FILE"' in text[record:]
    assert 'COMPONENTS_FILE="$CACHE_DIR/${BOX}-${ARCH}.components.json"' in text


def _helper(call: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", "-c", f'. "{_BOXES / "_components.sh"}" && {call}'],
        capture_output=True,
        text=True,
        check=False,
    )


def test_components_record_json():
    out = _helper(f'components_record "{PIN}" "{OBS}@{TREE},b@1"')
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"runtime": PIN, "components": {OBS: TREE, "b": "1"}}
    assert json.loads(_helper(f'components_record "{PIN}" ""').stdout) == {
        "runtime": PIN,
        "components": {},
    }


def test_components_names_json():
    assert json.loads(_helper(f'components_names_json "{OBS}@{TREE},b@1"').stdout) == [OBS, "b"]
    assert json.loads(_helper('components_names_json ""').stdout) == []


def test_components_check():
    assert _helper(f'components_check "{OBS}@{TREE}"').returncode == 0
    assert _helper('components_check ""').returncode == 0
    bad = _helper('components_check "a@1,B@2"')
    assert bad.returncode == 2
    assert "(got 'B@2')" in bad.stderr
