"""Template-render guard (#774, epic .github#104): every Ansible `.j2` must parse.

`vrg-validate` lints the *text* of Jinja templates (shellcheck on `*.sh.j2`,
ansible-lint, etc.) but never asks Jinja to **render** them. A whole class of
defect therefore passes CI green and only surfaces at deploy time (or, worse, at
a cold-rebuild acceptance gate — the slowest, most expensive place to catch it):

- malformed or unbalanced `{{ ... }}` / `{% ... %}` expressions,
- invalid characters inside an expression,
- unclosed blocks.

The concrete bite (epic .github#122, task #772 / deploy #763): `run.sh.j2`
carried a shellcheck-disable *comment* containing a literal `{{ … }}` with a
Unicode ellipsis. Jinja parses the double-brace as an expression and the ellipsis
is an invalid char, so rendering blew up (`unexpected char '…'`) on every node —
yet `vrg-validate` was green because it never rendered the template.

This guard closes that gap at the cheapest possible place: a Jinja **parse** of
every Ansible template. Parsing needs no variable values and resolves no filters
(Jinja resolves those at render time), so it produces **zero false positives**
from Ansible-specific filters/lookups/facts while still catching every
syntax/malformed-expression defect of the class above — exactly the failure mode
#774 was filed for.

Scope note (deliberately repo-local): the *ideal* home is an upstream
`template-render` check in `vrg-validate` itself (vergil-tooling), since the gap
is generic to any Vergil-managed repo with Jinja templates. A full render under
`StrictUndefined` — which would additionally catch undefined-variable and typo
bugs — needs each template's representative variable context (role defaults +
group_vars + host_vars + facts), which is a per-template fixture surface better
owned upstream. This guard is the robust, false-positive-free repo-local floor.
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ANSIBLE_ROOT = REPO_ROOT / "ansible"


def _ansible_jinja_env() -> jinja2.Environment:
    """A Jinja environment whose whitespace/delimiter settings mirror Ansible's.

    Ansible uses the default Jinja delimiters (`{{ }}`, `{% %}`, `{# #}`) with
    `trim_blocks` on. Parsing is delimiter/whitespace-driven, so matching these
    keeps the parse faithful to what Ansible does at deploy time. `parse()` never
    resolves filters or variables, so no Ansible plugin registration is required.
    """
    return jinja2.Environment(
        trim_blocks=True,
        lstrip_blocks=False,
        keep_trailing_newline=True,
        autoescape=False,  # noqa: S701 - config/shell/service templates, not HTML
    )


def _templates() -> list[Path]:
    return sorted(ANSIBLE_ROOT.rglob("*.j2"))


def test_ansible_templates_are_discovered() -> None:
    """Guard the guard: a discovery/glob regression must not silently pass by
    finding zero templates. The repo has ~33 Ansible `.j2` files."""
    templates = _templates()
    assert len(templates) >= 20, (
        f"expected to discover the Ansible .j2 templates under {ANSIBLE_ROOT}, "
        f"found only {len(templates)} — has the template layout moved?"
    )


@pytest.mark.parametrize("template", _templates(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_ansible_template_parses(template: Path) -> None:
    """Every Ansible `.j2` template must parse as valid Jinja (#774)."""
    env = _ansible_jinja_env()
    source = template.read_text(encoding="utf-8")
    try:
        env.parse(source)
    except jinja2.TemplateSyntaxError as exc:
        rel = template.relative_to(REPO_ROOT)
        pytest.fail(
            f"Jinja template fails to parse and would break at deploy/render time: "
            f"{rel}: {exc.message} (line {exc.lineno})"
        )


def test_parse_guard_catches_malformed_expression() -> None:
    """Regression fixture for the exact #774 bite: a literal `{{ … }}` with a
    Unicode ellipsis (as appeared in a shellcheck-disable comment) is a Jinja
    parse error. This asserts the guard above actually trips on that class —
    a guard that never fails is worthless."""
    env = _ansible_jinja_env()
    with pytest.raises(jinja2.TemplateSyntaxError):
        env.parse("# shellcheck disable {{ … }}\n")
