"""mqwebuser.xml has exactly one writer: the mqweb role's registry template (#1598).

The re-provision check of #1504 found mqweb restarting on every node on every
re-provision. Two tasks wrote mqwebuser.xml: the registry `template`, then
`setmqweb properties -k httpHost`, which re-serializes the whole XML (whitespace and
attribute order). Each later run's template render then differed from the file on
disk, reported `changed`, and fired the TLS restart, even though the end state was
byte-identical.

The template already carries `<variable name="httpHost" value="*"/>` (the
mqwebuser.xml form of that setmqweb call) and is rendered before mqweb's first start
(#741), so the setmqweb call was removed. These guards keep any second writer out:
no `setmqweb` call anywhere in the Ansible tree, and no task other than the registry
template that targets mqwebuser.xml (template, copy, lineinfile, blockinfile, replace,
xml, or a shell/command that names the file).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
ANSIBLE = REPO_ROOT / "ansible"
MQWEB_TASKS = ANSIBLE / "roles" / "mqweb" / "tasks" / "main.yml"
TEMPLATE = ANSIBLE / "roles" / "mqweb" / "templates" / "mqwebuser.xml.j2"
REGISTRY_TASK = "mqweb basic registry"
TARGET = "mqwebuser.xml"

# Modules that write a file's content. Each is matched by its short name, so the FQCN
# (ansible.builtin.*, community.general.xml) and the bare form are both covered.
_FILE_WRITERS = {"template", "copy", "lineinfile", "blockinfile", "replace", "xml"}
_COMMANDS = {"command", "shell", "raw", "script"}


def _yaml_files() -> list[Path]:
    files = sorted(p for ext in ("*.yml", "*.yaml") for p in ANSIBLE.rglob(ext))
    assert files, f"no Ansible YAML found under {ANSIBLE}"
    return files


def _dicts(node: object) -> Iterator[dict]:
    """Every mapping in a parsed YAML document: tasks, including those nested in
    blocks, plays' tasks/handlers, and role task files alike."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _dicts(v)


def _module_calls(path: Path) -> Iterator[tuple[dict, str, object]]:
    """(task, module short name, module args) for each file-writer / command module."""
    for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        for task in _dicts(doc):
            for key, args in task.items():
                short = str(key).rsplit(".", 1)[-1]
                if short in _FILE_WRITERS | _COMMANDS:
                    yield task, short, args


def _text(args: object) -> str:
    if isinstance(args, dict):
        return " ".join(_text(v) for v in args.values())
    if isinstance(args, list):
        return " ".join(_text(v) for v in args)
    return str(args)


def test_no_setmqweb_call_in_ansible() -> None:
    """setmqweb rewrites mqwebuser.xml, so no task may run it (#1598)."""
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: {task.get('name', '<unnamed>')!r}"
        for path in _yaml_files()
        for task, short, args in _module_calls(path)
        if short in _COMMANDS and "setmqweb" in _text(args)
    ]
    assert not offenders, (
        "setmqweb re-serializes mqwebuser.xml, so the registry template reports changed and "
        "restarts mqweb on every re-provision (#1598). Set the property in "
        f"mqwebuser.xml.j2 instead. Offending tasks: {offenders}"
    )


def test_registry_template_is_the_only_mqwebuser_xml_writer() -> None:
    """Exactly one task in the Ansible tree writes mqwebuser.xml: the mqweb role's
    registry template (#1598)."""
    writers = [
        (path, task, short)
        for path in _yaml_files()
        for task, short, args in _module_calls(path)
        if TARGET in _text(args)
    ]
    described = [
        f"{p.relative_to(REPO_ROOT)}: {t.get('name', '<unnamed>')!r} ({s})" for p, t, s in writers
    ]
    assert len(writers) == 1, (
        f"mqwebuser.xml must have exactly one writer, the registry template (#1598); "
        f"found {described}"
    )
    path, task, short = writers[0]
    assert path == MQWEB_TASKS and short == "template", described
    assert REGISTRY_TASK in task.get("name", ""), described
    assert task["ansible.builtin.template"]["src"] == "mqwebuser.xml.j2"


def test_template_sets_http_host_before_mqweb_starts() -> None:
    """With setmqweb gone, the template must bind mqweb to all interfaces itself, and
    render before mqweb's first start, since Liberty reads httpHost at startup (#741)."""
    assert '<variable name="httpHost" value="*"/>' in TEMPLATE.read_text(encoding="utf-8")
    tasks = yaml.safe_load(MQWEB_TASKS.read_text(encoding="utf-8"))
    names = [t.get("name", "") for t in tasks]
    registry = next(i for i, n in enumerate(names) if REGISTRY_TASK in n)
    start = next(i for i, n in enumerate(names) if "start mqweb without blocking" in n)
    assert registry < start, "the registry template must render before mqweb starts (#741)"
