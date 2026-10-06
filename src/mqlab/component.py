"""Guest components: build, stage and report (epic .github#294 spec §5.6).

``mqlab component build <name>`` is the ONLY producer of an installable artifact. It
refuses uncommitted component changes, a stale ``uv.lock``, or a failing test, and
runs the tests on the pinned guest runtime (``runtime.python``) in a build-owned
venv it asserts was made from that interpreter. Then it stages, under
``build/cache/components/<name>/<version>+<tree>/``:

- the component's pure wheel (``uv build --wheel``, a PEP 517 build from source);
- ``requirements.txt``: runtime deps for ALL extras, hash-pinned (``uv export``);
- ``build-requirements.txt``: the optional ``sdist-build`` group, hash-pinned;
- ``deps/``: every artifact those name, fetched from ``uv.lock``'s URL+hash, so a guest
  install never consults an index;
- ``systemd/``: the component's static units;
- ``BUILD.json``: what was built, from which commit, on which runtime.

``<tree>`` is the git tree hash of ``components/<name>`` at HEAD: an artifact is keyed
by SOURCE, so an edited-but-unbuilt component can never pass for a built one.

Install (spec §5.7) has ONE path, the ``component-install`` Ansible role: the box bakes
run it, and so does ``mqlab component install`` against a running lab. This module only
resolves the artifact and writes the role variables (:func:`install_vars`).
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mqlab import runtime
from mqlab.paths import cache, components_dir, inventory_path, repo_root, work
from mqlab.runner import Command
from mqlab.versions import load_catalog

if TYPE_CHECKING:
    from collections.abc import Callable

    from mqlab.runner import CommandRunner
    from mqlab.versions import RuntimePin

SDIST_BUILD_GROUP = "sdist-build"
INSTALL_PREFIX = "/opt/logical-minds-foundry"
_EXPORT = ["uv", "export", "--frozen", "--no-emit-project", "--format", "requirements-txt"]
_REQ_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==")
_WHEEL_PY = re.compile(r"^(py3|cp314)$")


class ComponentError(RuntimeError):
    """A component cannot be built, found or reported; the message names the fix."""


@dataclass(frozen=True)
class Artifact:
    """One staged, installable build of a component."""

    name: str
    version: str
    tree: str
    path: Path
    build: dict[str, Any]


# --- the component tree ---------------------------------------------------------------


def known_components() -> list[str]:
    """Every ``components/<name>/`` that is a uv project (has a pyproject.toml)."""
    root = components_dir()
    if not root.is_dir():
        return []
    return sorted(p.parent.name for p in root.glob("*/pyproject.toml"))


def component_dir(name: str) -> Path:
    """``components/<name>`` (unknown name -> ComponentError listing the known ones)."""
    known = known_components()
    if name not in known:
        listed = ", ".join(known) or "none yet"
        raise ComponentError(f"unknown component {name!r} (known: {listed})")
    return components_dir() / name


def _pyproject(name: str) -> dict[str, Any]:
    data: dict[str, Any] = tomllib.loads((component_dir(name) / "pyproject.toml").read_text())
    return data


def version(name: str) -> str:
    """The component's ``[project].version``."""
    value = _pyproject(name).get("project", {}).get("version")
    if not isinstance(value, str) or not value:
        raise ComponentError(f"components/{name}/pyproject.toml has no [project].version")
    return value


def _run(runner: CommandRunner, command: Command, on_line: Callable[[str], None]) -> int:
    on_line(f"$ {command.display()}")
    return runner.run(command, on_line)


def _capture(runner: CommandRunner, command: Command) -> tuple[int, list[str]]:
    lines: list[str] = []
    return runner.run(command, lines.append), lines


def _git(runner: CommandRunner, *args: str) -> tuple[int, list[str]]:
    return _capture(runner, Command(["git", *args], cwd=repo_root()))


def tree_hash(name: str, runner: CommandRunner) -> str:
    """The git tree hash of ``components/<name>`` at HEAD (its source identity)."""
    rc, out = _git(runner, "rev-parse", f"HEAD:components/{name}")
    if rc != 0 or not out:
        raise ComponentError(
            f"components/{name} is not committed at HEAD ({' '.join(out) or 'no output'}) — "
            "commit it, then build"
        )
    return out[-1].strip()


def dirty_paths(name: str, runner: CommandRunner) -> list[str]:
    """Uncommitted (or untracked) paths under ``components/<name>``."""
    rc, out = _git(runner, "status", "--porcelain", "--", f"components/{name}")
    if rc != 0:
        raise ComponentError(f"git status failed for components/{name}: {' '.join(out)}")
    return [line[3:] for line in out if line.strip()]


def _refuse_dirty(name: str, runner: CommandRunner) -> None:
    dirty = dirty_paths(name, runner)
    if dirty:
        raise ComponentError(
            f"components/{name} has uncommitted changes ({', '.join(dirty)}) — an artifact "
            f"must be a commit: commit them, then run mqlab component build {name}"
        )


# --- artifacts ------------------------------------------------------------------------


def artifacts_dir(name: str) -> Path:
    return cache("components", name)


def artifact_dir(name: str, version: str, tree: str) -> Path:
    return artifacts_dir(name) / f"{version}+{tree}"


def test_venv_dir(name: str) -> Path:
    """The build-owned test venv (never the developer's components/<name>/.venv)."""
    return work("components", name, "venv")


def _load(name: str, path: Path) -> Artifact | None:
    build_json = path / "BUILD.json"
    if not build_json.is_file():
        return None
    build: dict[str, Any] = json.loads(build_json.read_text())
    return Artifact(name, build["version"], build["tree"], path, build)


def find_artifact(name: str, tree: str) -> Artifact | None:
    """The complete artifact built from source tree ``tree``, if any."""
    root = artifacts_dir(name)
    if not root.is_dir():
        return None
    for path in sorted(root.glob(f"*+{tree}")):
        found = _load(name, path)
        if found is not None:
            return found
    return None


def current(name: str) -> Artifact | None:
    """The artifact ``CURRENT`` points at (the latest build or reuse), if any."""
    pointer = artifacts_dir(name) / "CURRENT"
    if not pointer.is_file():
        return None
    return _load(name, artifacts_dir(name) / pointer.read_text().strip())


def _set_current(artifact: Artifact) -> None:
    (artifacts_dir(artifact.name) / "CURRENT").write_text(artifact.path.name + "\n")


# --- dependency staging from uv.lock --------------------------------------------------


def _normalize(project: str) -> str:
    return re.sub(r"[-_.]+", "-", project).lower()


def wheel_installable(filename: str) -> bool:
    """Whether a CPython 3.14 Linux guest (x86_64 or aarch64) could install this wheel."""
    stem = filename.removesuffix(".whl")
    py_tags, abi_tag, plat_tag = stem.split("-")[-3:]
    pys = py_tags.split(".")
    py_ok = any(_WHEEL_PY.match(p) for p in pys) or (
        abi_tag == "abi3" and any(p.startswith("cp3") for p in pys)
    )
    plats = plat_tag.split(".")
    plat_ok = any(
        p == "any" or (p.startswith("manylinux") and p.endswith(("_x86_64", "_aarch64")))
        for p in plats
    )
    return py_ok and plat_ok


def _wanted(*requirement_files: Path) -> set[str]:
    names: set[str] = set()
    for path in requirement_files:
        if path.is_file():
            for line in path.read_text().splitlines():
                match = _REQ_NAME.match(line)
                if match:
                    names.add(_normalize(match.group(1)))
    return names


def stage_deps(
    lock: Path, wanted: set[str], deps: Path, fetch: Callable[[str, Path], None]
) -> list[str]:
    """Fetch every installable artifact of ``wanted`` from ``uv.lock``, hash-verified."""
    deps.mkdir(parents=True, exist_ok=True)
    packages = {_normalize(p["name"]): p for p in tomllib.loads(lock.read_text())["package"]}
    missing = sorted(wanted - set(packages))
    if missing:
        raise ComponentError(f"{lock}: exported but not locked: {', '.join(missing)}")
    staged: list[str] = []
    for project in sorted(wanted):
        pkg = packages[project]
        candidates = ([pkg["sdist"]] if "sdist" in pkg else []) + [
            w for w in pkg.get("wheels", []) if wheel_installable(w["url"].rsplit("/", 1)[1])
        ]
        if not candidates:
            raise ComponentError(
                f"{project}=={pkg['version']}: uv.lock has no sdist and no wheel a CPython "
                "3.14 Linux guest can install"
            )
        for art in candidates:
            filename = art["url"].rsplit("/", 1)[1]
            dest = deps / filename
            fetch(art["url"], dest)
            algo, _, expected = art["hash"].partition(":")
            actual = runtime.sha256_file(dest)
            if algo != "sha256" or actual != expected:
                dest.unlink()
                raise ComponentError(f"{filename}: sha256 {actual} != uv.lock {art['hash']}")
            staged.append(filename)
    return staged


# --- the build --------------------------------------------------------------------------


def _step(
    runner: CommandRunner, command: Command, on_line: Callable[[str], None], failure: str
) -> None:
    if _run(runner, command, on_line) != 0:
        raise ComponentError(failure)


def _assert_pinned_venv(venv: Path, interpreter: Path, pin: RuntimePin) -> None:
    """The test venv was made from the pinned interpreter (its token-keyed dir).

    uv records only the minor in ``version_info``, so the exact build is proven by
    ``home``: the interpreter dir is keyed by the pin token and arch.
    """
    cfg_path = venv / "pyvenv.cfg"
    if not cfg_path.is_file():
        raise ComponentError(f"test venv {venv} has no pyvenv.cfg — it was not created")
    pairs = (line.partition("=") for line in cfg_path.read_text().splitlines())
    cfg = {k.strip(): v.strip() for k, _, v in pairs if k.strip()}
    home = cfg.get("home", "")
    want = interpreter.parent
    found_version = cfg.get("version_info") or cfg.get("version") or ""
    if not home or Path(home).resolve() != want.resolve():
        raise ComponentError(
            f"test venv {venv} was made from {home or 'nothing'}, not the pinned runtime "
            f"{want} ({pin.token}) — refusing to test on an unpinned interpreter"
        )
    if found_version.split(".")[:2] != pin.minor.split("."):
        raise ComponentError(
            f"test venv {venv} reports Python {found_version or '?'}, not {pin.minor}"
        )


def build(
    name: str,
    *,
    runner: CommandRunner,
    on_line: Callable[[str], None],
    pin: RuntimePin | None = None,
    fetch: Callable[[str, Path], None] = runtime.fetch_url,
) -> Artifact:
    """Test on the pinned runtime and stage an installable artifact (spec §5.6)."""
    comp = component_dir(name)
    _refuse_dirty(name, runner)
    tree = tree_hash(name, runner)
    ver = version(name)
    pin = pin if pin is not None else load_catalog().runtime
    interpreter = runtime.ensure_interpreter(pin, fetch=fetch)
    on_line(f"{name} {ver} (tree {tree[:12]}) on {pin.token}: {interpreter}")

    _step(
        runner,
        Command(["uv", "lock", "--check"], cwd=comp),
        on_line,
        f"components/{name}/uv.lock is stale — run `uv --directory components/{name} lock`, "
        f"commit it, then mqlab component build {name}",
    )
    venv = test_venv_dir(name)
    shutil.rmtree(venv, ignore_errors=True)
    env = {"UV_PROJECT_ENVIRONMENT": str(venv)}
    _step(
        runner,
        Command(["uv", "venv", "--python", str(interpreter), str(venv)], cwd=comp, env=env),
        on_line,
        f"{name}: could not create the test venv {venv} from {interpreter}",
    )
    _assert_pinned_venv(venv, interpreter, pin)
    _step(
        runner,
        Command(
            ["uv", "run", "--frozen", "--python", str(interpreter), "pytest"], cwd=comp, env=env
        ),
        on_line,
        f"{name}: tests failed on the pinned runtime {pin.token} — nothing was staged",
    )
    _assert_pinned_venv(venv, interpreter, pin)  # `uv run` must not have swapped it

    final = artifact_dir(name, ver, tree)
    stage = final.with_name(final.name + ".partial")
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    _step(
        runner,
        Command(["uv", "build", "--wheel", "--out-dir", str(stage)], cwd=comp),
        on_line,
        f"{name}: uv build --wheel failed",
    )
    requirements = stage / "requirements.txt"
    _step(
        runner,
        Command([*_EXPORT, "--no-dev", "--all-extras", "-o", str(requirements)], cwd=comp),
        on_line,
        f"{name}: uv export of the runtime requirements failed",
    )
    build_requirements = stage / "build-requirements.txt"
    if SDIST_BUILD_GROUP in _pyproject(name).get("dependency-groups", {}):
        _step(
            runner,
            Command(
                [*_EXPORT, "--only-group", SDIST_BUILD_GROUP, "-o", str(build_requirements)],
                cwd=comp,
            ),
            on_line,
            f"{name}: uv export of the {SDIST_BUILD_GROUP} group failed",
        )
    wanted = _wanted(requirements, build_requirements)
    for filename in stage_deps(comp / "uv.lock", wanted, stage / "deps", fetch):
        on_line(f"staged deps/{filename}")
    units = comp / "systemd"
    if units.is_dir():
        shutil.copytree(units, stage / "systemd")

    _rc, head = _git(runner, "rev-parse", "HEAD")
    build_info = {
        "name": name,
        "version": ver,
        "tree": tree,
        "commit": head[-1].strip() if head else "",
        "runtime": pin.token,
        "tests": "passed",
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (stage / "BUILD.json").write_text(json.dumps(build_info, indent=2) + "\n")
    shutil.rmtree(final, ignore_errors=True)
    stage.replace(final)
    artifact = Artifact(name, ver, tree, final, build_info)
    _set_current(artifact)
    on_line(f"built {final}")
    return artifact


def ensure_built(
    name: str,
    *,
    runner: CommandRunner,
    on_line: Callable[[str], None],
    pin: RuntimePin | None = None,
    fetch: Callable[[str, Path], None] = runtime.fetch_url,
) -> Artifact:
    """The artifact for ``name`` at HEAD: reused when already built, else built now."""
    _refuse_dirty(name, runner)
    found = find_artifact(name, tree_hash(name, runner))
    if found is not None:
        _set_current(found)
        on_line(f"{name}: reusing {found.path}")
        return found
    return build(name, runner=runner, on_line=on_line, pin=pin, fetch=fetch)


# --- install (spec §5.7) ----------------------------------------------------------------


def install_vars_path() -> Path:
    """Where :func:`install_vars` writes the role variables (local work/ bucket)."""
    return work("components", "install-vars.json")


def install_vars(
    artifacts: list[Artifact],
    arch: str | None = None,
    *,
    pin: RuntimePin | None = None,
    fetch: Callable[[str, Path], None] = runtime.fetch_url,
) -> Path:
    """Write the runtime-install/component-install role variables; return the file.

    ``runtime_python`` describes the pin. With ``arch`` (a bake), the pinned tarball for
    that arch is ensured and sha256-verified in the cache, and named for runtime-install;
    without it (``mqlab component install`` on a running lab, which never touches the
    interpreter) no tarball is offered, so runtime-install cannot run from these vars.
    ``component_artifacts`` maps each component to its staged artifact directory.
    """
    pin = pin if pin is not None else load_catalog().runtime
    tarballs: dict[str, str] = {}
    sha256: dict[str, str] = {}
    if arch is not None:
        runtime.ensure_tarball(pin, arch, fetch=fetch)
        tarballs[arch] = pin.tarball(arch)
        sha256[arch] = pin.sha256[arch]
    data = {
        "runtime_python": {
            "version": pin.version,
            "pbs_release": pin.pbs_release,
            "minor": pin.minor,
            "token": pin.token,
            "tarball_dir": str(runtime.tarball_dir()),
            "tarballs": tarballs,
            "sha256": sha256,
        },
        "component_artifacts": {a.name: str(a.path) for a in artifacts},
    }
    path = install_vars_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return path


def resolve_artifact(
    name: str,
    wanted: str | None,
    *,
    runner: CommandRunner,
    on_line: Callable[[str], None],
    pin: RuntimePin | None = None,
    fetch: Callable[[str, Path], None] = runtime.fetch_url,
) -> Artifact:
    """The artifact to install: HEAD's (built now if missing), or an explicit staged one.

    ``wanted`` is an artifact directory name (``<version>+<tree>``) or a bare version
    that names exactly one staged artifact. It is never built: an explicit version must
    already be staged.
    """
    if wanted is None:
        return ensure_built(name, runner=runner, on_line=on_line, pin=pin, fetch=fetch)
    component_dir(name)
    loaded = (_load(name, p) for p in sorted(artifacts_dir(name).glob("*+*")))
    staged = [a for a in loaded if a is not None]
    matches = [a for a in staged if wanted in (a.path.name, a.version)]
    if len(matches) != 1:
        listed = ", ".join(a.path.name for a in staged) or "none"
        problem = "matches several" if matches else "is not a staged artifact"
        raise ComponentError(
            f"{name} --version {wanted} {problem} (staged: {listed}) — pass one of the "
            f"staged <version>+<tree> names, or omit --version to install HEAD "
            f"(mqlab component build {name})"
        )
    return matches[0]


def install_command(name: str, hosts: list[str], vars_file: Path) -> Command:
    """The ``ansible-playbook component-install.yml`` run for ``mqlab component install``."""
    return Command(
        [
            "ansible-playbook",
            "component-install.yml",
            "-i",
            str(inventory_path()),
            "--limit",
            ",".join(hosts),
            "-e",
            f"component_name={name}",
            "-e",
            "component_start_units=true",
            "-e",
            f"@{vars_file}",
        ],
        cwd=repo_root() / "ansible",
    )


def install(
    name: str,
    hosts: list[str],
    *,
    runner: CommandRunner,
    on_line: Callable[[str], None],
    wanted: str | None = None,
    pin: RuntimePin | None = None,
    fetch: Callable[[str, Path], None] = runtime.fetch_url,
) -> Artifact:
    """Install a built component onto running hosts with the role the bake uses."""
    artifact = resolve_artifact(name, wanted, runner=runner, on_line=on_line, pin=pin, fetch=fetch)
    vars_file = install_vars([artifact], pin=pin, fetch=fetch)
    on_line(f"installing {name} {artifact.path.name} on {', '.join(hosts)}")
    rc = _run(runner, install_command(name, hosts, vars_file), on_line)
    if rc != 0:
        raise ComponentError(
            f"ansible-playbook component-install.yml exited {rc} installing {name} on "
            f"{', '.join(hosts)} (see its output above)"
        )
    return artifact


# --- status -----------------------------------------------------------------------------


def installed_path(name: str) -> str:
    return f"{INSTALL_PREFIX}/{name}/INSTALLED.json"


def read_installed(
    name: str, hosts: list[str], runner: CommandRunner
) -> dict[str, dict[str, Any] | str]:
    """Each host's ``INSTALLED.json`` for ``name`` (a string explains a missing one)."""
    command = Command(
        [
            "ansible",
            ",".join(hosts),
            "-i",
            str(inventory_path()),
            "-m",
            "ansible.builtin.slurp",
            "-a",
            f"src={installed_path(name)}",
        ],
        cwd=repo_root() / "ansible",
        env={"ANSIBLE_LOAD_CALLBACK_PLUGINS": "1", "ANSIBLE_STDOUT_CALLBACK": "json"},
    )
    _rc, lines = _capture(runner, command)  # non-zero whenever any host lacks the file
    text = "\n".join(lines)
    # The repo's ansible.cfg enables timing callbacks, which print a timestamp line BEFORE
    # the json callback's document and a TASKS/PLAYBOOK RECAP AFTER it (#1374): decode
    # exactly one JSON document from the first "{" and ignore whatever follows.
    start = text.find("{")
    try:
        if start < 0:
            raise ValueError("no '{' in the output")
        report, _end = json.JSONDecoder().raw_decode(text, start)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        raise ComponentError(
            f"ansible returned no JSON reading {installed_path(name)} ({exc}): {text}"
        ) from None
    results: dict[str, dict[str, Any] | str] = {}
    for task in report.get("plays", [{}])[0].get("tasks", []):
        for host, result in task.get("hosts", {}).items():
            if result.get("unreachable"):
                results[host] = "unreachable"
            elif result.get("failed") or "content" not in result:
                results[host] = "not installed"
            else:
                results[host] = json.loads(base64.b64decode(result["content"]))
    for host in hosts:
        results.setdefault(host, "no result")
    return results


def render_status(names: list[str], runner: CommandRunner, hosts: list[str] | None = None) -> str:
    """Built artifacts per component, and with ``hosts`` what each host has installed."""
    rows = ["COMPONENT  CURRENT  TREE  RUNTIME  HEAD"]
    for name in names:
        art = current(name)
        head = tree_hash(name, runner)
        if art is None:
            rows.append(f"{name}  (not built)  -  -  {head[:12]}")
            continue
        fresh = "up to date" if art.tree == head else f"STALE (HEAD {head[:12]})"
        rows.append(f"{name}  {art.version}  {art.tree[:12]}  {art.build['runtime']}  {fresh}")
        for host, got in sorted(read_installed(name, hosts, runner).items()) if hosts else []:
            if isinstance(got, str):
                rows.append(f"  {host}: {got}")
                continue
            match = "matches CURRENT" if got.get("tree") == art.tree else "DIFFERS from CURRENT"
            rows.append(
                f"  {host}: {got.get('version')} tree {str(got.get('tree'))[:12]} "
                f"runtime {got.get('runtime_installed', '?')}  {match}"
            )
    return "\n".join(rows)
