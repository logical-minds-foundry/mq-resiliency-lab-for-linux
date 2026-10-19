"""Guard: no top-level ``ansible_*`` fact reference under ``ansible/`` (#136).

ansible-core 2.24 removes ``INJECT_FACTS_AS_VARS``: gathered facts stop being injected
as top-level ``ansible_<fact>`` variables and are reachable only as
``ansible_facts['<fact>']`` (no ``ansible_`` prefix). The lab sets
``inject_facts_as_vars = False`` in ``ansible/ansible.cfg`` now, so a top-level fact
reference is already undefined at runtime. Several such sites pick which files load
(``tasks/os-vars.yml``, the ``install-<os_family>.yml`` includes), so a miss fails
provisioning outright rather than warning.

What this pins:

- **Allowlist, not denylist.** Every ``ansible_[a-z0-9_]+`` name in a ``*.yml``,
  ``*.yaml`` or ``*.j2`` file under ``ansible/`` must be ``ansible_facts`` itself or one
  of Ansible's documented connection or magic variables (``ALLOWED`` /
  ``ALLOWED_PREFIXES``). A fact name nobody uses today still fails, because it is not on
  the list.
- **Comments are not code.** YAML is parsed, so ``#`` comments are dropped and only the
  keys and values Ansible evaluates are scanned. Jinja ``{# ... #}`` comments are dropped
  from YAML strings and templates alike. A ``#`` line in a template body is rendered
  output, so it is scanned.
- ``ansible.cfg`` sets ``inject_facts_as_vars = False`` under ``[defaults]``.
"""

from __future__ import annotations

import configparser
import re
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"

NAME = re.compile(r"\bansible_[a-z0-9_]+")
JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)

# Connection variables and magic variables (Ansible "Special Variables"), plus the
# facts namespace itself. None of these is a gathered fact.
ALLOWED = frozenset(
    {
        "ansible_facts",
        "ansible_host",
        "ansible_user",
        "ansible_port",
        "ansible_connection",
        "ansible_python_interpreter",
        "ansible_play_hosts",
        "ansible_play_hosts_all",
        "ansible_limit",
        "ansible_failed_result",
        "ansible_failed_task",
        "ansible_managed",
    }
)
ALLOWED_PREFIXES = ("ansible_ssh_",)

SUFFIXES = (".yml", ".yaml", ".j2")


def _strings(node: Any) -> Iterator[str]:
    """Yield every string key and string value in a parsed YAML document."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(key)
            yield from _strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)


def _names(text: str) -> set[str]:
    return set(NAME.findall(JINJA_COMMENT.sub("", text)))


def _disallowed(names: set[str]) -> set[str]:
    return {n for n in names if n not in ALLOWED and not n.startswith(ALLOWED_PREFIXES)}


def _scan_file(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".j2":
        return _disallowed(_names(text))
    found: set[str] = set()
    for doc in yaml.safe_load_all(text):
        for s in _strings(doc):
            found |= _names(s)
    return _disallowed(found)


def _scan_tree(root: Path) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in SUFFIXES:
            bad = _scan_file(path)
            if bad:
                hits[str(path.relative_to(root))] = sorted(bad)
    return hits


def test_ansible_tree_has_no_top_level_fact_reference() -> None:
    hits = _scan_tree(ANSIBLE)
    assert not hits, (
        f"top-level fact references (use ansible_facts['<fact>'], no ansible_ prefix; #136): {hits}"
    )


def test_scan_covers_the_tree() -> None:
    files = [p for p in ANSIBLE.rglob("*") if p.is_file() and p.suffix in SUFFIXES]
    assert len(files) > 100
    assert any(p.suffix == ".j2" for p in files)


def test_unknown_future_fact_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "main.yml").write_text(
        textwrap.dedent(
            """\
            - name: size the heap
              ansible.builtin.set_fact:
                heap_mb: "{{ ansible_memtotal_mb // 4 }}"
            """
        ),
        encoding="utf-8",
    )
    assert _scan_tree(tmp_path) == {"main.yml": ["ansible_memtotal_mb"]}


def test_fact_in_template_body_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "a.conf.j2").write_text(
        "{# ansible_kernel in a jinja comment is ignored #}\n"
        "# rendered line: {{ ansible_kernel }}\n",
        encoding="utf-8",
    )
    assert _scan_tree(tmp_path) == {"a.conf.j2": ["ansible_kernel"]}


def test_fact_in_yaml_comment_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "main.yml").write_text(
        textwrap.dedent(
            """\
            # Picks the tarball by ansible_architecture (now ansible_facts['architecture']).
            - name: pick the arch  # was ansible_architecture
              ansible.builtin.set_fact:
                arch: "{{ 'arm64' if ansible_facts['architecture'] == 'aarch64' else 'x64' }}"
                msg: "{# ansible_os_family #}{{ ansible_host }} {{ ansible_ssh_user }}"
            """
        ),
        encoding="utf-8",
    )
    assert _scan_tree(tmp_path) == {}


def test_ansible_cfg_disables_fact_injection() -> None:
    cfg = configparser.ConfigParser()
    cfg.read(ANSIBLE / "ansible.cfg", encoding="utf-8")
    assert cfg.get("defaults", "inject_facts_as_vars") == "False"
