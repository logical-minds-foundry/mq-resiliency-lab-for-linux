#!/usr/bin/python3
"""Set the RDQM HA heartbeat timeout in corosync.conf, per IBM's MQ 10.0 procedure (#1515).

IBM "Changing the heartbeat timeout for HA RDQM"
(https://www.ibm.com/docs/en/SSYHRD_10.0.0/configure/heartbeat_timeout.html) says to edit
/etc/corosync/corosync.conf on each node of the HA group and, in the totem section:

* set ``token`` to the new timeout in ms (3000 to 120000);
* in each heartbeat link's ``interface`` block (``linknumber: 0``, plus ``linknumber: 1``
  with two links) set ``knet_ping_timeout`` = token / 2, ``knet_ping_interval`` =
  token / 20 and ``knet_pong_count`` = 2.

``rdqmadm -c`` owns the rest of the file, so this module edits only those keys, keeps every
other line as it is, and adds missing keys or interface blocks. The heartbeat links are the
``ring<N>_addr`` keys in the nodelist (RDQM writes one per heartbeat address); a file with
none has the single link 0.

It is idempotent: a second run on an edited file changes nothing. It refuses (fails) on a
file it cannot read safely: no totem section, more than one, or unbalanced braces. It does
not restart corosync; the rdqm-ha role does that, in Pacemaker maintenance mode, as IBM says.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
module: rdqm_corosync_heartbeat
short_description: Set the RDQM HA heartbeat timeout (corosync token and knet values)
description:
  - Edits corosync.conf as IBM's MQ 10.0 topic "Changing the heartbeat timeout for HA RDQM"
    instructs. Does not restart corosync.
options:
  token:
    description: Heartbeat timeout in milliseconds, 3000 to 120000.
    type: int
    required: true
  path:
    description: The corosync configuration file.
    type: path
    default: /etc/corosync/corosync.conf
"""

TOKEN_MIN = 3000
TOKEN_MAX = 120000
DEFAULT_INDENT = "    "

_OPEN = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*\{\s*$")
_CLOSE = re.compile(r"^\s*\}\s*$")
_KEY = re.compile(r"^(\s*)([A-Za-z0-9_.-]+)\s*:\s*(.*?)\s*$")
_RING = re.compile(r"^ring(\d+)_addr$")


class CorosyncConfError(ValueError):
    """corosync.conf cannot be edited safely."""


@dataclass
class Section:
    """One ``name { ... }`` block: its line span and its direct ``key: value`` lines."""

    name: str
    path: tuple[str, ...]
    start: int
    end: int = -1
    keys: dict[str, tuple[int, str]] = field(default_factory=dict)
    children: list[Section] = field(default_factory=list)


def heartbeat_values(token: int) -> dict[str, str]:
    """The interface values IBM derives from ``token``."""
    return {
        "knet_ping_timeout": str(token // 2),
        "knet_ping_interval": str(token // 20),
        "knet_pong_count": "2",
    }


def parse(lines: list[str]) -> list[Section]:
    """Every section in the file, outermost first, with balanced braces."""
    sections: list[Section] = []
    stack: list[Section] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        opened = _OPEN.match(line)
        if opened:
            parent_path = stack[-1].path if stack else ()
            section = Section(opened.group(1), (*parent_path, opened.group(1)), index)
            if stack:
                stack[-1].children.append(section)
            stack.append(section)
            sections.append(section)
            continue
        if _CLOSE.match(line):
            if not stack:
                raise CorosyncConfError(f"line {index + 1}: '}}' closes no section")
            stack.pop().end = index
            continue
        key = _KEY.match(line)
        if key and stack:
            stack[-1].keys.setdefault(key.group(2), (index, key.group(3)))
    if stack:
        raise CorosyncConfError(f"section '{stack[-1].name}' is never closed")
    return sections


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _child_indent(lines: list[str], section: Section) -> str:
    """The indent of the section's first inner line, or one step past its own."""
    if section.end > section.start + 1:
        for line in lines[section.start + 1 : section.end]:
            if line.strip():
                return _indent_of(line)
    return _indent_of(lines[section.start]) + DEFAULT_INDENT


def _set_key(lines: list[str], section: Section, key: str, value: str) -> bool:
    """Set ``key: value`` directly inside ``section``. True if a line changed."""
    if key in section.keys:
        index, current = section.keys[key]
        if current == value:
            return False
        indent = _indent_of(lines[index])
        lines[index] = f"{indent}{key}: {value}"
        return True
    # Append at the end of the section, so added keys read in IBM's order.
    lines.insert(section.end, f"{_child_indent(lines, section)}{key}: {value}")
    return True


def heartbeat_links(sections: list[Section]) -> list[int]:
    """The link numbers the nodelist names (``ring<N>_addr``); link 0 when it names none."""
    links: set[int] = set()
    for section in sections:
        if section.path == ("nodelist", "node"):
            for key in section.keys:
                ring = _RING.match(key)
                if ring:
                    links.add(int(ring.group(1)))
    return sorted(links) or [0]


def _totem(sections: list[Section]) -> Section:
    totems = [s for s in sections if s.path == ("totem",)]
    if len(totems) != 1:
        raise CorosyncConfError(f"expected one totem section, found {len(totems)}")
    return totems[0]


def _interface(totem: Section, link: int) -> Section | None:
    for child in totem.children:
        if child.name != "interface":
            continue
        # corosync's linknumber defaults to 0 when an interface block omits it.
        number = child.keys.get("linknumber", (0, "0"))[1]
        if number == str(link):
            return child
    return None


def _next_edit(lines: list[str], token: int) -> bool:
    """Make the first missing edit in place. False when the file already matches."""
    sections = parse(lines)
    totem = _totem(sections)
    if _set_key(lines, totem, "token", str(token)):
        return True
    for link in heartbeat_links(sections):
        interface = _interface(totem, link)
        if interface is None:
            inner = _child_indent(lines, totem)
            lines[totem.end : totem.end] = [
                f"{inner}interface {{",
                f"{inner}{DEFAULT_INDENT}linknumber: {link}",
                f"{inner}}}",
            ]
            return True
        for key, value in heartbeat_values(token).items():
            if _set_key(lines, interface, key, value):
                return True
    return False


def apply_heartbeat(text: str, token: int) -> str:
    """``text`` with IBM's heartbeat settings for ``token``. Raises on an unsafe file."""
    if not TOKEN_MIN <= token <= TOKEN_MAX:
        raise CorosyncConfError(
            f"token {token} ms is outside IBM's range {TOKEN_MIN}-{TOKEN_MAX} ms"
        )
    lines = text.splitlines()
    # Each pass makes one edit: the token, then per link at most an interface block and
    # its three keys. One more pass confirms nothing is left.
    for _ in range(2 + 4 * len(heartbeat_links(parse(lines)))):
        if not _next_edit(lines, token):
            return "\n".join(lines) + "\n"
    raise CorosyncConfError("the edit did not converge")  # pragma: no cover - defensive


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "token": {"type": "int", "required": True},
            "path": {"type": "path", "default": "/etc/corosync/corosync.conf"},
        },
        supports_check_mode=True,
    )
    path = Path(module.params["path"])
    token = module.params["token"]
    try:
        before = path.read_text(encoding="utf-8")
        after = apply_heartbeat(before, token)
        links = heartbeat_links(parse(after.splitlines()))
    except (OSError, CorosyncConfError) as exc:
        module.fail_json(msg=f"{path}: {exc}")
        return
    changed = after != before
    if changed and not module.check_mode:
        module.atomic_move(_write_temp(module, path, after), str(path))
    module.exit_json(
        changed=changed,
        token=token,
        links=links,
        **heartbeat_values(token),
        diff={"before": before, "after": after, "before_header": str(path),
              "after_header": str(path)},
    )


def _write_temp(module: AnsibleModule, path: Path, text: str) -> str:
    """Write ``text`` to a temp file beside ``path`` (so atomic_move keeps mode/owner)."""
    temp = path.with_name(f".{path.name}.rdqm-heartbeat.tmp")
    temp.write_text(text, encoding="utf-8")
    module.add_cleanup_file(str(temp))
    return str(temp)


if __name__ == "__main__":
    main()
