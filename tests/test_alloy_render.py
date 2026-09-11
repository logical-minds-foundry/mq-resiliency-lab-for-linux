"""Render guard for the alloy role's config.alloy.j2 (#1068, epic .github#198).

`test_templates_render.py` only parses every `.j2` for Jinja syntax — it can't catch
a rendered value that is invalid in Alloy's River config language. #1029 added a
journal-drop `regex = "(alloy|data-prepper)\\.service"`, but `\\.` is an INVALID
River double-quoted-string escape, so Alloy refused the whole config and crash-looped
fleet-wide — no logs reached Loki or OpenSearch, silently. `vrg-validate` stayed
green because nothing loads the rendered River into Alloy.

This guard renders `config.alloy.j2` (both fan-out branches) and asserts every
double-quoted string uses only valid River escapes. Backtick raw strings — the fix —
are exempt, since River does no escape processing inside them.
"""

from __future__ import annotations

from pathlib import Path

import jinja2

TEMPLATE = (
    Path(__file__).resolve().parent.parent
    / "ansible"
    / "roles"
    / "alloy"
    / "templates"
    / "config.alloy.j2"
)

# River double-quoted-string escapes: single-char, plus \xNN \uNNNN \UNNNNNNNN and
# octal \NNN. Anything else after a backslash is an "unknown escape sequence".
_SINGLE_CHAR_ESCAPES = set("abfnrtv\\\"'")
_HEX_UNICODE = set("xuU")

_CONTEXT = {
    "inventory_hostname": "obs",
    "loki_push_url": "http://10.50.0.2:3100/loki/api/v1/push",
    "alloy_tail_mqweb": True,
    "alloy_fanout_opensearch": True,
    "opensearch_dataprepper_endpoint": "10.50.0.4:21892",
}


def _render(**overrides: object) -> str:
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)  # noqa: S701
    env.filters["bool"] = lambda v: (
        v if isinstance(v, bool) else str(v).strip().lower() in {"true", "1", "yes", "on"}
    )
    return env.from_string(TEMPLATE.read_text(encoding="utf-8")).render({**_CONTEXT, **overrides})


def _is_valid_river_escape(ch: str) -> bool:
    """A char that legally follows a backslash in a River double-quoted string."""
    return ch in _SINGLE_CHAR_ESCAPES or ch in _HEX_UNICODE or ch.isdigit()


def _invalid_escapes(text: str) -> list[str]:
    """Backslash-escapes River would reject, found in double-quoted strings only.

    A small state-aware scan so it is not fooled by `//` line comments (which may
    show escape *examples*) or by `//` inside a string (the Loki URL), and so backtick
    raw strings — where `\\.` is legal — are correctly exempt.
    """
    bad: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":  # River line comment → skip to EOL
            while i < n and text[i] != "\n":
                i += 1
        elif c == "`":  # raw string → no escape processing, skip to closing backtick
            i += 1
            while i < n and text[i] != "`":
                i += 1
            i += 1
        elif c == '"':  # double-quoted string → validate each escape
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    if not _is_valid_river_escape(text[i + 1]):
                        bad.append("\\" + text[i + 1])
                    i += 2
                else:
                    i += 1
            i += 1
        else:
            i += 1
    return bad


def test_alloy_config_valid_river_escapes_fanout_on():
    assert _invalid_escapes(_render()) == []


def test_alloy_config_valid_river_escapes_fanout_off():
    assert _invalid_escapes(_render(alloy_fanout_opensearch=False, alloy_tail_mqweb=False)) == []


def test_guard_detects_the_1029_regression():
    # The pre-fix line — a double-quoted `\.` — must be flagged, or the guard is inert.
    assert _invalid_escapes(r'regex = "(alloy|data-prepper)\.service"') == ["\\."]
