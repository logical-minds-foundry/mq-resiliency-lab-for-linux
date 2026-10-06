"""Guard the runtime-install / component-install roles (epic .github#294 T5, spec §5.7).

Structural tests over the role YAML (the live proof is the V1/V2 cold rebuilds): the
runtime precondition runs before any change, each install is a release built at its final
path (venvs are not relocatable, #1372) that goes live by an atomic symlink flip only after
an in-place selfcheck, is re-checked through the live path (failure flips back), guests
install offline from hashes with the venv's own pip
(never uv, never PyPI), sdist builds use gcc derived from the pinned runtime's sysconfig
(spike correction C2), and the pinned interpreter is never Ansible's.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from mqlab import component

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
RUNTIME = ANSIBLE / "roles" / "runtime-install"
COMPONENT = ANSIBLE / "roles" / "component-install"
PLAY = ANSIBLE / "component-install.yml"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _flat(tasks: list[dict[str, Any]], inherited: dict[str, Any] | None = None) -> list[dict]:
    """Tasks in order, blocks expanded (block, then rescue, then always); a block's
    become/when is inherited. Rescue tasks are tagged ``_rescue`` with the block's name."""
    out: list[dict[str, Any]] = []
    for task in tasks:
        if "block" in task:
            keep = {k: task[k] for k in ("become", "when") if k in task}
            out += _flat(task["block"], {**(inherited or {}), **keep})
            rescue = {**(inherited or {}), **keep, "_rescue": task.get("name", "")}
            out += _flat(task.get("rescue", []), rescue)
            out += _flat(task.get("always", []), {**(inherited or {}), **keep})
        else:
            out.append({**(inherited or {}), **task})
    return out


def _tasks(role: Path) -> list[dict[str, Any]]:
    tasks = _load(role / "tasks" / "main.yml")
    assert isinstance(tasks, list)
    return _flat(tasks)


def _defaults(role: Path) -> dict[str, Any]:
    data = _load(role / "defaults" / "main.yml")
    assert isinstance(data, dict)
    return data


def _index(tasks: list[dict[str, Any]], needle: str) -> int:
    """The single task whose name contains ``needle``."""
    found = [i for i, t in enumerate(tasks) if needle in t.get("name", "")]
    assert len(found) == 1, f"expected one task named like {needle!r}, got {found}"
    return found[0]


def _shell(task: dict[str, Any]) -> str:
    script = task.get("ansible.builtin.shell")
    assert isinstance(script, str), f"{task.get('name')}: not a shell task"
    return script.replace("\\\n", " ")  # join line continuations


def _shells(tasks: list[dict[str, Any]]) -> list[str]:
    return [_shell(t) for t in tasks if "ansible.builtin.shell" in t]


# --- component-install -------------------------------------------------------------------


def test_component_install_asserts_runtime_pin_first():  # Review Focus 5
    tasks = _tasks(COMPONENT)
    stat, slurp, check = tasks[0], tasks[1], tasks[2]
    pin = "{{ component_runtime_prefix }}/cpython-{{ runtime_python.minor }}/PIN"
    assert stat["ansible.builtin.stat"]["path"] == pin
    assert slurp["ansible.builtin.slurp"]["src"] == pin
    that = check["ansible.builtin.assert"]["that"]
    assert any("runtime_python.token" in cond for cond in that)
    assert "mqlab box build <box>" in check["ansible.builtin.assert"]["fail_msg"]
    first_change = next(i for i, t in enumerate(tasks) if t.get("become"))
    assert first_change > 2, "nothing may change the guest before the runtime precondition"
    assert not any(t.get("become") for t in tasks[:first_change])


def test_component_install_copies_with_builtin_only():
    # ansible.posix is not guaranteed in the bake's ANSIBLE_COLLECTIONS_PATH.
    for role in (RUNTIME, COMPONENT):
        for task in _tasks(role):
            modules = [k for k in task if "." in k]
            assert all(m.startswith("ansible.builtin.") for m in modules), (role.name, modules)


def test_component_install_swaps_only_after_selfcheck():  # Review Focus 3
    """Build in place, selfcheck in place, THEN flip; a failed build/selfcheck removes the
    release and fails without touching the live venv (#1372)."""
    tasks = _tasks(COMPONENT)
    build = _index(tasks, "Build the release venv")
    selfcheck = _index(tasks, "Selfcheck the release in place")
    remove = _index(tasks, "Remove the failed release")
    flip = _index(tasks, "Flip venv to the new release")
    assert build < selfcheck < remove < flip
    check = tasks[selfcheck]
    assert check["ansible.builtin.command"]["argv"] == [
        "{{ _c_release }}/venv/bin/{{ component_name }}-selfcheck"
    ]
    assert "failed_when" not in check and "ignore_errors" not in check
    # The rescue removes the failed release, then fails the play (never flips).
    assert tasks[remove]["_rescue"].startswith("Build and selfcheck the release")
    assert tasks[remove]["ansible.builtin.file"] == {"path": "{{ _c_release }}", "state": "absent"}
    fail = tasks[remove + 1]
    assert fail["_rescue"] == tasks[remove]["_rescue"] and "ansible.builtin.fail" in fail
    # Nothing before the flip touches the live venv / previous links.
    for task in tasks[:flip]:
        if "ansible.builtin.shell" in task:
            assert not re.search(r"\b(venv\.tmp|previous)\b|mv -T", _shell(task)), task["name"]


def test_component_install_builds_each_release_at_its_final_path():  # #1372
    """Venvs are not relocatable (absolute shebangs): build at the final, unique release
    path and never move it. A venv.new -> venv rename is exactly the V1 #1357 defect."""
    tasks = _tasks(COMPONENT)
    name = tasks[_index(tasks, "Name this release")]["ansible.builtin.set_fact"]
    assert name["_c_release_id"].startswith("{{ _c_src | basename }}-{{ now(utc=true)")
    path = tasks[_index(tasks, "Release path")]["ansible.builtin.set_fact"]
    assert path["_c_release"] == "{{ _c_root }}/releases/{{ _c_release_id }}"
    script = _shell(tasks[_index(tasks, "Build the release venv")])
    assert '"{{ _c_runtime }}" -m venv "$rel/venv"' in script
    every_shell = "\n".join(_shells(tasks))
    assert "-m venv" in script and every_shell.count("-m venv") == 1
    assert not re.search(r"mv\s+(\S*/)?venv\.new", every_shell), "never rename a built venv"
    assert not re.search(r"mv\s+[\"']?\$?\w*rel", every_shell), "never move a release"


def test_component_install_flips_venv_atomically_and_records_previous():  # #1372
    tasks = _tasks(COMPONENT)
    script = _shell(tasks[_index(tasks, "Flip venv to the new release")])
    assert 'new="releases/{{ _c_release_id }}/venv"' in script  # relative, inside the root
    assert 'ln -s "$new" venv.tmp' in script and "mv -T venv.tmp venv" in script
    assert 'prev="$(readlink venv)"' in script
    assert "mv -T previous.tmp previous" in script
    # Legacy (pre-#1372) layouts are cleared: a real venv dir, venv.new, venv.prev.
    assert "elif [ -e venv ]; then" in script and "rm -rf venv.new venv.prev" in script


def test_component_install_proves_the_live_path_and_flips_back_on_failure():  # #1372
    """The in-place selfcheck cannot see a broken live path (the V1 #1357 gap): after the
    flip, selfcheck through venv/bin; on failure flip back to previous and fail."""
    tasks = _tasks(COMPONENT)
    flip = _index(tasks, "Flip venv to the new release")
    live = _index(tasks, "Selfcheck through venv/bin")
    back = _index(tasks, "Flip back to the previous release")
    assert flip < live < back
    assert tasks[live]["ansible.builtin.command"]["argv"] == [
        "{{ _c_root }}/venv/bin/{{ component_name }}-selfcheck"
    ]
    assert "failed_when" not in tasks[live] and "ignore_errors" not in tasks[live]
    rescue = _shell(tasks[back])
    assert tasks[back]["_rescue"].startswith("Selfcheck through the live path")
    assert 'ln -s "$(readlink previous)" venv.tmp' in rescue and "mv -T venv.tmp venv" in rescue
    assert "rm -f venv" in rescue  # no previous: take the broken release offline
    fail = tasks[back + 1]
    assert fail["_rescue"] == tasks[back]["_rescue"] and "ansible.builtin.fail" in fail


def test_component_install_prunes_to_live_and_previous():  # #1372
    tasks = _tasks(COMPONENT)
    prune = tasks[_index(tasks, "Prune old releases")]
    script = _shell(prune)
    assert 'keep_live="$(basename "$(dirname "$(readlink venv)")")"' in script
    assert 'keep_prev="$(basename "$(dirname "$(readlink previous)")")"' in script
    assert 'rm -rf "releases/$id"' in script
    assert _index(tasks, "Selfcheck through venv/bin") < _index(tasks, "Prune old releases")


def test_component_install_never_uses_uv_or_pypi():
    text = "\n".join(_shells(_tasks(COMPONENT)))
    assert not re.search(r"\buv ", text)
    assert "pypi" not in text.lower()
    installs = [line for line in text.splitlines() if re.search(r"pip\"? install", line)]
    assert len(installs) == 3  # build reqs, deps, the component wheel
    assert all("--no-index" in line for line in installs)
    hashed = [line for line in installs if "-r " in line]
    assert len(hashed) == 2
    assert all("--require-hashes" in line and "--find-links" in line for line in hashed)
    wheel = next(line for line in installs if "*.whl" in line)
    assert "--no-deps" in wheel


def test_component_install_sets_gcc_compiler_before_requirements_install():  # C2
    script = _shell(_tasks(COMPONENT)[_index(_tasks(COMPONENT), "Build the release venv")])
    deps = script.index('-r "$stage/requirements.txt"')
    exported = script.index("export CC LDSHARED")
    assert exported < deps
    assert 'CC="$(gcc_for CC)"' in script[:exported]
    assert 'LDSHARED="$(gcc_for LDSHARED)"' in script[:exported]
    # Derived from the venv interpreter's OWN sysconfig, only the leading clang swapped.
    helper = script[script.index("gcc_for() {") : exported]
    assert '"$py" -c' in helper and "sysconfig.get_config_var" in helper
    assert 're.sub(r"^clang(?=\\s|$)", "gcc", v)' in helper
    assert 'py="$rel/venv/bin/python"' in script
    # The build requirements (wheels) come first, then the --no-build-isolation deps.
    assert script.index('-r "$stage/build-requirements.txt"') < deps
    assert "--no-build-isolation" in script[deps - 200 : deps]


def test_component_install_restarts_only_on_a_live_lab():
    tasks = _tasks(COMPONENT)
    assert _defaults(COMPONENT)["component_start_units"] is False  # a bake leaves units inert
    restart = tasks[_index(tasks, "Restart the enabled ones")]
    assert "component_start_units | bool" in restart["when"]
    assert restart["ansible.builtin.systemd"]["state"] == "restarted"
    assert "failed_when" not in restart and "ignore_errors" not in restart
    units = tasks[_index(tasks, "Install the component's static units")]
    assert units["ansible.builtin.copy"]["dest"] == "/usr/lib/systemd/system/{{ item }}"


def test_component_install_records_installed_json():
    tasks = _tasks(COMPONENT)
    record = tasks[_index(tasks, "Record INSTALLED.json")]
    assert record["ansible.builtin.copy"]["dest"] == "{{ _c_root }}/INSTALLED.json"
    assert _index(tasks, "Prune old releases") < _index(tasks, "Record INSTALLED.json")
    assert "'release': _c_release_id" in record["ansible.builtin.copy"]["content"]
    assert "runtime_installed" in record["ansible.builtin.copy"]["content"]


def test_install_layout_matches_mqlab():
    defaults = _defaults(COMPONENT)
    assert defaults["component_install_prefix"] == component.INSTALL_PREFIX
    assert defaults["component_runtime_prefix"] == _defaults(RUNTIME)["runtime_install_prefix"]


def test_install_shells_are_strict_and_never_pipe_into_head():
    # A multi-line producer piped into `head` dies of SIGPIPE under pipefail (#1359).
    for role in (RUNTIME, COMPONENT):
        for script in _shells(_tasks(role)):
            assert script.lstrip().startswith("set -euo pipefail"), role.name
            assert not re.search(r"\|\s*head\b", script), role.name


def test_component_install_play_never_swaps_the_runtime():
    # The dev loop never touches a running guest's interpreter: a mismatch fails the
    # precondition and names a rebake, so the play runs component-install ONLY.
    (play,) = _load(PLAY)
    assert play["hosts"] == "all"
    assert play["roles"] == ["component-install"]
    # ...on exactly the guests named with --limit, never fleet-wide or fewer than asked.
    (guard,) = play["pre_tasks"]
    that = guard["ansible.builtin.assert"]["that"]
    assert "ansible_limit is defined" in that
    assert any("difference(ansible_play_hosts_all)" in cond for cond in that)


# --- runtime-install ---------------------------------------------------------------------


def test_runtime_install_verifies_sha_before_unpack():
    tasks = _tasks(RUNTIME)
    verify = _index(tasks, "Verify its sha256")
    unpack = _index(tasks, "Unpack beside the live tree")
    assert _index(tasks, "Copy the pinned tarball") < verify < unpack
    check = tasks[verify]
    assert check["ansible.builtin.stat"]["checksum_algorithm"] == "sha256"
    assert "runtime_python.sha256[_rt_arch]" in check["failed_when"]
    script = _shell(tasks[unpack])
    assert script.index("tar -xzf") < script.index('mv "$root.new" "$root"')
    # The unpacked interpreter must report the pinned version before it goes live.
    assert script.index("runtime_python.version") < script.index('mv "$root.new" "$root"')


def test_runtime_install_is_idempotent_on_a_matching_pin():
    tasks = _tasks(RUNTIME)
    for name in ("Copy the pinned tarball", "Verify its sha256", "Unpack beside"):
        task = tasks[_index(tasks, name)]
        assert "runtime_python.token" in task["when"]
        assert task["become"] is True


def test_runtime_install_never_sets_ansible_python_interpreter():
    # The pinned interpreter is never Ansible's (it stays /usr/bin/python3).
    for path in (*RUNTIME.rglob("*.yml"), *COMPONENT.rglob("*.yml"), PLAY):
        assert "ansible_python_interpreter" not in yaml.safe_dump(_load(path)), path
