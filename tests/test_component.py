"""mqlab component build/status (epic .github#294 T4, spec §5.6, §6).

A fake runner stands in for git/uv/ansible: it records every Command and performs the
side effects the real tools would (pyvenv.cfg, a wheel, the uv exports), so the whole
build pipeline runs end to end with no network and no real interpreter.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from mqlab import component
from mqlab.component import ComponentError
from mqlab.versions import RuntimePin

if TYPE_CHECKING:
    from collections.abc import Callable

    from mqlab.runner import Command

TREE = "a" * 40
HEAD = "c" * 40
PIN = RuntimePin("3.14.8", "20261003", {"x86_64": "1" * 64, "aarch64": "2" * 64})

PYMQI_SDIST = b"pymqi sdist bytes"
SETUPTOOLS_WHL = b"setuptools wheel bytes"


def _sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


PYMQI_HASH = f"sha256:{_sha(PYMQI_SDIST)}"
SETUP_HASH = f"sha256:{_sha(SETUPTOOLS_WHL)}"

URLS = {
    "https://f.ex/pymqi-1.12.13.tar.gz": PYMQI_SDIST,
    "https://f.ex/setuptools-84.0.0-py3-none-any.whl": SETUPTOOLS_WHL,
}

UV_LOCK = f"""\
version = 1

[[package]]
name = "demo"
version = "0.1.0"
source = {{ editable = "." }}

[[package]]
name = "pymqi"
version = "1.12.13"
sdist = {{ url = "https://f.ex/pymqi-1.12.13.tar.gz", hash = "{PYMQI_HASH}" }}

[[package]]
name = "setuptools"
version = "84.0.0"
wheels = [
    {{ url = "https://f.ex/setuptools-84.0.0-py3-none-any.whl", hash = "{SETUP_HASH}" }},
    {{ url = "https://f.ex/setuptools-84.0.0-cp27-none-win32.whl", hash = "sha256:00" }},
]
"""

PYPROJECT = """\
[project]
name = "demo"
version = "0.1.0"

[project.optional-dependencies]
mqi = ["pymqi==1.12.13"]

[dependency-groups]
dev = ["pytest"]
sdist-build = ["setuptools"]
"""


@dataclass
class FakeTools:
    """git / uv / ansible stand-in: scripted exit codes, real-tool side effects."""

    interpreter: Path
    fail: dict[str, int] = field(default_factory=dict)  # argv-prefix key -> exit code
    dirty: list[str] = field(default_factory=list)
    tree: str = TREE
    head: list[str] = field(default_factory=lambda: [HEAD])
    venv_home: Path | None = None
    venv_version: str = "3.14"
    swap_home_on_test: Path | None = None
    ansible_out: list[str] = field(default_factory=list)
    recorded: list[Command] = field(default_factory=list)

    def _key(self, argv: list[str]) -> str:
        if argv[:2] == ["uv", "export"]:
            return "uv export sdist" if "--only-group" in argv else "uv export"
        return " ".join(argv[:2])

    def run(self, command: Command, on_line: Callable[[str], None]) -> int:
        self.recorded.append(command)
        argv = command.argv
        key = self._key(argv)
        if key in self.fail:
            on_line(f"{key} failed")
            return self.fail[key]
        if argv[:2] == ["git", "status"]:
            for path in self.dirty:
                on_line(f" M {path}")
        elif argv[:2] == ["git", "rev-parse"]:
            for line in [self.tree] if argv[2].startswith("HEAD:") else self.head:
                on_line(line)
        elif argv[:2] == ["uv", "venv"]:
            venv = Path(argv[-1])
            venv.mkdir(parents=True)
            home = self.venv_home if self.venv_home is not None else self.interpreter.parent
            (venv / "pyvenv.cfg").write_text(
                f"home = {home}\nimplementation = CPython\nversion_info = {self.venv_version}\n"
            )
        elif argv[:2] == ["uv", "run"] and self.swap_home_on_test is not None:
            assert command.env is not None
            cfg = Path(command.env["UV_PROJECT_ENVIRONMENT"]) / "pyvenv.cfg"
            cfg.write_text(f"home = {self.swap_home_on_test}\nversion_info = 3.14\n")
        elif argv[:2] == ["uv", "build"]:
            (Path(argv[-1]) / "demo-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
        elif argv[:2] == ["uv", "export"]:
            out = Path(argv[argv.index("-o") + 1])
            if "--only-group" in argv:
                out.write_text(f"setuptools==84.0.0 \\\n    --hash=sha256:{_sha(SETUPTOOLS_WHL)}\n")
            else:
                out.write_text(
                    f"# header\npymqi==1.12.13 \\\n    --hash=sha256:{_sha(PYMQI_SDIST)}\n"
                )
        elif argv[0] == "ansible":
            for line in self.ansible_out:
                on_line(line)
        return 0

    def argvs(self) -> list[list[str]]:
        return [c.argv for c in self.recorded]


def fetch(url: str, dest: Path) -> None:
    dest.write_bytes(URLS[url])


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repo with components/demo, build buckets under tmp, and a fake pinned runtime."""
    comp = tmp_path / "components" / "demo"
    (comp / "systemd").mkdir(parents=True)
    (comp / "pyproject.toml").write_text(PYPROJECT)
    (comp / "uv.lock").write_text(UV_LOCK)
    (comp / "systemd" / "demo.service").write_text("[Service]\n")
    interpreter = tmp_path / "rt" / "python" / "bin" / "python3.14"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("")
    monkeypatch.setattr(component, "components_dir", lambda: tmp_path / "components")
    monkeypatch.setattr(component, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(component, "cache", lambda *p: tmp_path.joinpath("build/cache", *p))
    monkeypatch.setattr(component, "work", lambda *p: tmp_path.joinpath("build/work", *p))
    monkeypatch.setattr(component, "inventory_path", lambda: tmp_path / "inventory.ini")
    monkeypatch.setattr(component.runtime, "ensure_interpreter", lambda pin, fetch: interpreter)
    return tmp_path


@pytest.fixture
def tools(lab: Path) -> FakeTools:
    return FakeTools(interpreter=lab / "rt" / "python" / "bin" / "python3.14")


def _build(tools: FakeTools, pin: RuntimePin | None = PIN) -> component.Artifact:
    lines: list[str] = []
    return component.build("demo", runner=tools, on_line=lines.append, pin=pin, fetch=fetch)


# --- the component tree ---------------------------------------------------------------


def test_known_components_lists_only_uv_projects(lab):
    (lab / "components" / "notaproject").mkdir()
    assert component.known_components() == ["demo"]


def test_known_components_without_a_components_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(component, "components_dir", lambda: tmp_path / "nope")
    assert component.known_components() == []


def test_unknown_component_lists_the_known_ones(lab):
    with pytest.raises(ComponentError, match=r"unknown component 'x' \(known: demo\)"):
        component.component_dir("x")


def test_unknown_component_when_there_are_none(tmp_path, monkeypatch):
    monkeypatch.setattr(component, "components_dir", lambda: tmp_path / "nope")
    with pytest.raises(ComponentError, match="none yet"):
        component.component_dir("x")


def test_version_reads_pyproject(lab):
    assert component.version("demo") == "0.1.0"


def test_version_missing(lab):
    (lab / "components/demo/pyproject.toml").write_text("[project]\nname = 'demo'\n")
    with pytest.raises(ComponentError, match=r"no \[project\].version"):
        component.version("demo")


def test_tree_hash_requires_a_commit(lab, tools):
    tools.fail["git rev-parse"] = 128
    with pytest.raises(ComponentError, match="components/demo is not committed at HEAD"):
        component.tree_hash("demo", tools)


def test_dirty_paths_git_failure(lab, tools):
    tools.fail["git status"] = 1
    with pytest.raises(ComponentError, match="git status failed"):
        component.dirty_paths("demo", tools)


def test_build_refuses_dirty_component(lab, tools):  # Review Focus 4
    tools.dirty = ["components/demo/src/x.py"]
    with pytest.raises(ComponentError) as exc:
        _build(tools)
    assert "components/demo/src/x.py" in str(exc.value)
    assert "mqlab component build demo" in str(exc.value)
    assert not any(a[0] == "uv" for a in tools.argvs())


# --- the build --------------------------------------------------------------------------


def test_build_runs_the_gate_in_order_and_stages_everything(lab, tools):
    art = _build(tools)
    venv = str(lab / "build/work/components/demo/venv")
    interp = str(tools.interpreter)
    uv = [a for a in tools.argvs() if a[0] == "uv"]
    assert uv[0] == ["uv", "lock", "--check"]
    assert uv[1] == ["uv", "venv", "--python", interp, venv]
    assert uv[2] == ["uv", "run", "--frozen", "--python", interp, "pytest"]
    assert uv[3][:3] == ["uv", "build", "--wheel"]
    assert uv[4][-4:-2] == ["--no-dev", "--all-extras"]
    assert uv[5][-4:-2] == ["--only-group", "sdist-build"]
    test_cmd = next(c for c in tools.recorded if c.argv[:2] == ["uv", "run"])
    assert test_cmd.env == {"UV_PROJECT_ENVIRONMENT": venv}
    assert test_cmd.cwd == lab / "components" / "demo"

    final = lab / "build/cache/components/demo" / f"0.1.0+{TREE}"
    assert art.path == final
    assert (art.name, art.version, art.tree) == ("demo", "0.1.0", TREE)
    assert sorted(p.name for p in final.iterdir()) == [
        "BUILD.json",
        "build-requirements.txt",
        "demo-0.1.0-py3-none-any.whl",
        "deps",
        "requirements.txt",
        "systemd",
    ]
    assert sorted(p.name for p in (final / "deps").iterdir()) == [
        "pymqi-1.12.13.tar.gz",
        "setuptools-84.0.0-py3-none-any.whl",  # the win32 wheel is not staged
    ]
    assert (final / "systemd" / "demo.service").is_file()
    build = json.loads((final / "BUILD.json").read_text())
    assert {k: build[k] for k in ("name", "version", "tree", "commit", "runtime", "tests")} == {
        "name": "demo",
        "version": "0.1.0",
        "tree": TREE,
        "commit": HEAD,
        "runtime": "3.14.8+20261003",
        "tests": "passed",
    }
    assert "built_at" in build
    assert (lab / "build/cache/components/demo/CURRENT").read_text() == f"0.1.0+{TREE}\n"
    assert not final.with_name(final.name + ".partial").exists()
    assert component.current("demo") == art


def test_build_without_sdist_group_units_or_deps(lab, tools):
    comp = lab / "components" / "demo"
    (comp / "pyproject.toml").write_text("[project]\nname = 'demo'\nversion = '0.2.0'\n")
    (comp / "systemd" / "demo.service").unlink()
    (comp / "systemd").rmdir()
    orig = tools.run

    def no_deps(command, on_line):
        rc = orig(command, on_line)
        if command.argv[:2] == ["uv", "export"]:
            Path(command.argv[command.argv.index("-o") + 1]).write_text("# no deps\n")
        return rc

    tools.run = no_deps  # type: ignore[method-assign]
    art = _build(tools)
    assert sorted(p.name for p in art.path.iterdir()) == [
        "BUILD.json",
        "demo-0.1.0-py3-none-any.whl",
        "deps",
        "requirements.txt",
    ]
    assert not any("--only-group" in a for a in tools.argvs())


def test_build_rebuild_replaces_an_existing_artifact(lab, tools):
    first = _build(tools)
    (first.path / "stale-marker").write_text("")
    second = _build(tools)
    assert second.path == first.path
    assert not (second.path / "stale-marker").exists()


def test_build_uses_the_catalog_pin_by_default(lab, tools, monkeypatch):
    class Catalog:
        runtime = PIN

    monkeypatch.setattr(component, "load_catalog", lambda: Catalog)
    assert _build(tools, pin=None).build["runtime"] == PIN.token


def test_build_records_an_empty_commit_when_head_is_unreadable(lab, tools):
    tools.head = []
    assert _build(tools).build["commit"] == ""


@pytest.mark.parametrize(
    ("step", "message"),
    [
        ("uv lock", "uv.lock is stale"),
        ("uv venv", "could not create the test venv"),
        ("uv run", "tests failed on the pinned runtime 3.14.8\\+20261003 — nothing was staged"),
        ("uv build", "uv build --wheel failed"),
        ("uv export", "uv export of the runtime requirements failed"),
        ("uv export sdist", "uv export of the sdist-build group failed"),
    ],
)
def test_build_stops_at_a_failing_step(lab, tools, step, message):
    tools.fail[step] = 1
    with pytest.raises(ComponentError, match=message):
        _build(tools)
    assert not (lab / "build/cache/components/demo" / f"0.1.0+{TREE}").exists()
    assert component.current("demo") is None


def test_build_refuses_a_test_venv_from_another_interpreter(lab, tools):
    tools.venv_home = Path("/usr/bin")
    with pytest.raises(ComponentError, match="was made from /usr/bin, not the pinned runtime"):
        _build(tools)
    assert not any(a[:2] == ["uv", "run"] for a in tools.argvs())  # never ran the tests


def test_build_refuses_a_test_venv_of_another_minor(lab, tools):
    tools.venv_version = "3.12"
    with pytest.raises(ComponentError, match="reports Python 3.12, not 3.14"):
        _build(tools)


def test_build_refuses_a_venv_swapped_during_the_tests(lab, tools):
    tools.swap_home_on_test = Path("/usr/local/bin")
    with pytest.raises(ComponentError, match="was made from /usr/local/bin"):
        _build(tools)


def test_assert_pinned_venv_without_pyvenv_cfg(tmp_path):
    with pytest.raises(ComponentError, match="has no pyvenv.cfg"):
        component._assert_pinned_venv(tmp_path, tmp_path / "python3.14", PIN)


def test_assert_pinned_venv_without_home(tmp_path):
    (tmp_path / "pyvenv.cfg").write_text("\nversion_info = 3.14\n")
    with pytest.raises(ComponentError, match="was made from nothing"):
        component._assert_pinned_venv(tmp_path, tmp_path / "bin" / "python3.14", PIN)


def test_assert_pinned_venv_accepts_cpython_version_key(tmp_path):
    (tmp_path / "pyvenv.cfg").write_text(f"home = {tmp_path / 'bin'}\nversion = 3.14.8\n")
    component._assert_pinned_venv(tmp_path, tmp_path / "bin" / "python3.14", PIN)


def test_assert_pinned_venv_with_no_version(tmp_path):
    (tmp_path / "pyvenv.cfg").write_text(f"home = {tmp_path / 'bin'}\n")
    with pytest.raises(ComponentError, match=r"reports Python \?, not 3.14"):
        component._assert_pinned_venv(tmp_path, tmp_path / "bin" / "python3.14", PIN)


# --- dependency staging -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "ok"),
    [
        ("setuptools-84.0.0-py3-none-any.whl", True),
        ("six-1.16.0-py2.py3-none-any.whl", True),
        ("x-1.0-cp314-cp314-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", True),
        ("x-1.0-cp314-cp314-manylinux_2_28_aarch64.whl", True),
        ("x-1.0-cp39-abi3-manylinux_2_17_x86_64.whl", True),
        ("x-1.0-1-cp314-cp314-manylinux_2_17_x86_64.whl", True),  # with a build tag
        ("x-1.0-cp313-cp313-manylinux_2_17_x86_64.whl", False),
        ("x-1.0-cp314-cp314-musllinux_1_2_x86_64.whl", False),
        ("x-1.0-cp314-cp314-win_amd64.whl", False),
        ("x-1.0-cp314-cp314-macosx_11_0_arm64.whl", False),
        ("x-1.0-py2-none-any.whl", False),
    ],
)
def test_wheel_installable(filename, ok):
    assert component.wheel_installable(filename) is ok


def test_wanted_reads_pins_and_skips_the_rest(tmp_path):
    req = tmp_path / "r.txt"
    req.write_text("# c\nFoo_Bar==1.0 \\\n    --hash=sha256:x\n    # via y\n")
    assert component._wanted(req, tmp_path / "absent.txt") == {"foo-bar"}


def _lock(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "uv.lock"
    path.write_text(text)
    return path


def test_stage_deps_exported_but_not_locked(tmp_path):
    lock = _lock(tmp_path, "version = 1\n[[package]]\nname = 'demo'\nversion = '0'\n")
    with pytest.raises(ComponentError, match="exported but not locked: pymqi"):
        component.stage_deps(lock, {"pymqi"}, tmp_path / "deps", fetch)


def test_stage_deps_no_installable_artifact(tmp_path):
    lock = _lock(
        tmp_path,
        "version = 1\n[[package]]\nname = 'x'\nversion = '1'\n"
        "wheels = [{ url = 'https://f/x-1-cp314-cp314-win_amd64.whl', hash = 'sha256:0' }]\n",
    )
    with pytest.raises(ComponentError, match="x==1: uv.lock has no sdist and no wheel"):
        component.stage_deps(lock, {"x"}, tmp_path / "deps", fetch)


def test_stage_deps_hash_mismatch_deletes_the_file(tmp_path):
    lock = _lock(
        tmp_path,
        "version = 1\n[[package]]\nname = 'pymqi'\nversion = '1.12.13'\n"
        "sdist = { url = 'https://f.ex/pymqi-1.12.13.tar.gz', hash = 'sha256:00' }\n",
    )
    with pytest.raises(
        ComponentError, match="pymqi-1.12.13.tar.gz: sha256 .* != uv.lock sha256:00"
    ):
        component.stage_deps(lock, {"pymqi"}, tmp_path / "deps", fetch)
    assert not (tmp_path / "deps" / "pymqi-1.12.13.tar.gz").exists()


def test_stage_deps_refuses_a_non_sha256_hash(tmp_path):
    digest = _sha(PYMQI_SDIST)
    lock = _lock(
        tmp_path,
        "version = 1\n[[package]]\nname = 'pymqi'\nversion = '1.12.13'\n"
        f"sdist = {{ url = 'https://f.ex/pymqi-1.12.13.tar.gz', hash = 'md5:{digest}' }}\n",
    )
    with pytest.raises(ComponentError, match="!= uv.lock md5:"):
        component.stage_deps(lock, {"pymqi"}, tmp_path / "deps", fetch)


# --- artifacts and reuse --------------------------------------------------------------------


def test_find_artifact_none_built(lab):
    assert component.find_artifact("demo", TREE) is None


def test_find_artifact_skips_an_incomplete_dir(lab, tools):
    art = _build(tools)
    (art.path.with_name(f"0.0.9+{TREE}")).mkdir()  # no BUILD.json: not an artifact
    assert component.find_artifact("demo", TREE) == art
    assert component.find_artifact("demo", "b" * 40) is None


def test_find_artifact_only_incomplete(lab):
    (lab / "build/cache/components/demo" / f"0.1.0+{TREE}").mkdir(parents=True)
    assert component.find_artifact("demo", TREE) is None


def test_current_none(lab):
    assert component.current("demo") is None


def test_ensure_built_reuses_a_matching_tree(lab, tools):
    art = _build(tools)
    (lab / "build/cache/components/demo/CURRENT").write_text("elsewhere\n")
    tools.recorded.clear()
    lines: list[str] = []
    assert component.ensure_built("demo", runner=tools, on_line=lines.append, pin=PIN) == art
    assert not any(a[0] == "uv" for a in tools.argvs())
    assert component.current("demo") == art  # CURRENT moved back to the reused artifact
    assert lines == [f"demo: reusing {art.path}"]


def test_ensure_built_builds_when_missing(lab, tools):
    art = component.ensure_built(
        "demo", runner=tools, on_line=lambda _l: None, pin=PIN, fetch=fetch
    )
    assert art.tree == TREE
    assert ["uv", "lock", "--check"] in tools.argvs()


def test_ensure_built_refuses_dirty(lab, tools):
    tools.dirty = ["components/demo/pyproject.toml"]
    with pytest.raises(ComponentError, match="uncommitted changes"):
        component.ensure_built("demo", runner=tools, on_line=lambda _l: None, pin=PIN)


# --- install (spec §5.7) ------------------------------------------------------------------


@pytest.fixture
def tarballs(lab, monkeypatch):
    """runtime.ensure_tarball recorded, the tarball cache under the lab's build/cache."""
    ensured: list[str] = []
    monkeypatch.setattr(
        component.runtime, "ensure_tarball", lambda pin, arch, fetch: ensured.append(arch)
    )
    monkeypatch.setattr(component.runtime, "tarball_dir", lambda: lab / "build/cache/runtime")
    return ensured


def _vars(path: Path) -> dict:
    data: dict = json.loads(path.read_text())
    return data


def test_install_vars_for_a_bake_name_the_verified_tarball(lab, tools, tarballs):
    art = _build(tools)
    path = component.install_vars([art], "aarch64", pin=PIN)
    assert path == lab / "build/work/components/install-vars.json"
    assert path == component.install_vars_path()
    assert tarballs == ["aarch64"]
    assert _vars(path) == {
        "runtime_python": {
            "version": "3.14.8",
            "pbs_release": "20261003",
            "minor": "3.14",
            "token": "3.14.8+20261003",
            "tarball_dir": str(lab / "build/cache/runtime"),
            "tarballs": {
                "aarch64": "cpython-3.14.8+20261003-aarch64-unknown-linux-gnu-install_only.tar.gz"
            },
            "sha256": {"aarch64": "2" * 64},
        },
        "component_artifacts": {"demo": str(art.path)},
    }


def test_install_vars_without_an_arch_offer_no_tarball(lab, tools, tarballs, monkeypatch):
    # `mqlab component install` never swaps the interpreter: no tarball is ensured.
    monkeypatch.setattr(component, "load_catalog", lambda: SimpleNamespace(runtime=PIN))
    art = _build(tools)
    data = _vars(component.install_vars([art]))
    assert tarballs == []
    assert data["runtime_python"]["tarballs"] == {} == data["runtime_python"]["sha256"]
    assert data["runtime_python"]["token"] == PIN.token


def test_resolve_artifact_defaults_to_heads(lab, tools):
    art = component.resolve_artifact(
        "demo", None, runner=tools, on_line=lambda _l: None, pin=PIN, fetch=fetch
    )
    assert art.tree == TREE


def test_resolve_artifact_by_version_or_dir_name(lab, tools):
    art = _build(tools)
    tools.recorded.clear()
    for wanted in ("0.1.0", f"0.1.0+{TREE}"):
        found = component.resolve_artifact("demo", wanted, runner=tools, on_line=print)
        assert found == art
    assert tools.recorded == []  # an explicit version is never built, nor checked for dirt


def test_resolve_artifact_unknown_version(lab, tools):
    _build(tools)
    with pytest.raises(ComponentError, match=rf"--version 9.9 is not a staged .*0.1.0\+{TREE}"):
        component.resolve_artifact("demo", "9.9", runner=tools, on_line=print)


def test_resolve_artifact_nothing_staged(lab, tools):
    with pytest.raises(ComponentError, match=r"staged: none"):
        component.resolve_artifact("demo", "0.1.0", runner=tools, on_line=print)


def test_resolve_artifact_ambiguous_version(lab, tools):
    _build(tools)
    tools.tree = "b" * 40
    _build(tools)
    with pytest.raises(ComponentError, match="matches several"):
        component.resolve_artifact("demo", "0.1.0", runner=tools, on_line=print)


def test_resolve_artifact_unknown_component(lab, tools):
    with pytest.raises(ComponentError, match="unknown component 'x'"):
        component.resolve_artifact("x", "0.1.0", runner=tools, on_line=print)


def test_install_runs_the_component_install_play(lab, tools, tarballs):
    lines: list[str] = []
    art = component.install(
        "demo", ["app-client", "svc-sim"], runner=tools, on_line=lines.append, pin=PIN,
        fetch=fetch,
    )  # fmt: skip
    play = tools.recorded[-1]
    assert play.argv == [
        "ansible-playbook",
        "component-install.yml",
        "-i",
        str(lab / "inventory.ini"),
        "--limit",
        "app-client,svc-sim",
        "-e",
        "component_name=demo",
        "-e",
        "component_start_units=true",
        "-e",
        f"@{lab / 'build/work/components/install-vars.json'}",
    ]
    assert play.cwd == lab / "ansible"
    assert _vars(component.install_vars_path())["component_artifacts"] == {"demo": str(art.path)}
    assert tarballs == []  # the running guest's runtime is never reinstalled
    assert f"installing demo {art.path.name} on app-client, svc-sim" in lines


def test_install_play_failure_raises(lab, tools, tarballs):
    tools.fail["ansible-playbook component-install.yml"] = 2
    with pytest.raises(ComponentError, match="component-install.yml exited 2 installing demo"):
        component.install(
            "demo", ["a1"], runner=tools, on_line=lambda _l: None, pin=PIN, fetch=fetch
        )


def test_install_not_built_and_dirty_names_commit_then_build(lab, tools):
    tools.dirty = ["components/demo/src/x.py"]
    with pytest.raises(ComponentError) as exc:
        component.install("demo", ["a1"], runner=tools, on_line=lambda _l: None, pin=PIN)
    message = str(exc.value)
    assert message.index("commit") < message.index("mqlab component build demo")
    assert not any(a[0] == "ansible-playbook" for a in tools.argvs())


# --- status -------------------------------------------------------------------------------


def _ansible_json(hosts: dict[str, dict]) -> list[str]:
    report = {"plays": [{"tasks": [{"hosts": hosts}]}]}
    return ["[WARNING]: noise before the json", *json.dumps(report, indent=1).splitlines()]


def _installed(tree: str) -> str:
    doc = {"version": "0.1.0", "tree": tree, "runtime_installed": "3.14.8+20261003"}
    return base64.b64encode(json.dumps(doc).encode()).decode()


def test_read_installed_parses_each_host(lab, tools):
    tools.ansible_out = _ansible_json(
        {
            "a1": {"content": _installed(TREE)},
            "a2": {"failed": True, "msg": "file not found"},
            "a3": {"unreachable": True},
        }
    )
    got = component.read_installed("demo", ["a1", "a2", "a3", "a4"], tools)
    assert got["a1"] == {"version": "0.1.0", "tree": TREE, "runtime_installed": "3.14.8+20261003"}
    assert (got["a2"], got["a3"], got["a4"]) == ("not installed", "unreachable", "no result")
    cmd = tools.recorded[-1]
    assert cmd.argv[:2] == ["ansible", "a1,a2,a3,a4"]
    assert "src=/opt/logical-minds-foundry/demo/INSTALLED.json" in cmd.argv
    assert cmd.env == {"ANSIBLE_LOAD_CALLBACK_PLUGINS": "1", "ANSIBLE_STDOUT_CALLBACK": "json"}
    assert cmd.cwd == lab / "ansible"


def test_read_installed_without_json(lab, tools):
    tools.ansible_out = ["ERROR! no inventory"]
    with pytest.raises(ComponentError, match="ansible returned no JSON"):
        component.read_installed("demo", ["a1"], tools)


def test_read_installed_with_no_plays(lab, tools):
    tools.ansible_out = ["{}"]
    assert component.read_installed("demo", ["a1"], tools) == {"a1": "no result"}


def test_render_status_not_built(lab, tools):
    assert component.render_status(["demo"], tools).splitlines()[1] == (
        f"demo  (not built)  -  -  {TREE[:12]}"
    )


def test_render_status_up_to_date_and_stale(lab, tools):
    _build(tools)
    assert "up to date" in component.render_status(["demo"], tools)
    tools.tree = "b" * 40
    assert f"STALE (HEAD {'b' * 12})" in component.render_status(["demo"], tools)


def test_render_status_with_hosts(lab, tools):
    _build(tools)
    tools.ansible_out = _ansible_json(
        {
            "a1": {"content": _installed(TREE)},
            "a2": {"content": _installed("d" * 40)},
            "a3": {"failed": True},
        }
    )
    out = component.render_status(["demo"], tools, ["a1", "a2", "a3"]).splitlines()
    assert out[2] == f"  a1: 0.1.0 tree {TREE[:12]} runtime 3.14.8+20261003  matches CURRENT"
    assert out[3].endswith("DIFFERS from CURRENT")
    assert out[4] == "  a3: not installed"
