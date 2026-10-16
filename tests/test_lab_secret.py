"""lab/scripts/lab-secret.sh mints only registered secret names (#1570).

The script generates-and-persists a secret on first use, so any name it accepts
becomes a real file in the shared state bucket. Before #1570 it accepted any
``[a-z0-9_-]`` string: ``lab-secret.sh --help`` and ``lab-secret.sh get <name>``
minted junk files named ``--help`` and ``get``, and a caller typo would silently
mint a *different* secret from the one the lab was provisioned with.

The contract pinned here:

- option-like arguments (``-*``, including ``-h``/``--help``) and a wrong argument
  count print usage and exit non-zero, writing nothing;
- a name not in the ``lab/secret-names`` registry fails loudly, writing nothing;
- every name any caller in the repo uses is registered, and every registered name
  has a caller — so a new caller (or a typo, or a renamed stack/PKI entity) cannot
  drift from the registry unnoticed.

Every run here executes a *copy* of the script in a tmp tree (``lab/scripts/`` +
``lab/secret-names``), never the in-repo script, so no test can touch a real
``build/`` bucket. The PATH carries a stub ``mqlab`` that answers
``build path state`` with the sandbox's own state dir, so the sandbox stays
contained if the script ever resolves its secrets dir through ``mqlab`` (#1569).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from mqlab import cli
from mqlab.stacks import lab_stacks

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "lab" / "scripts" / "lab-secret.sh"
REGISTRY = REPO / "lab" / "secret-names"
PKI_PREFIX = "pki-keyrpwd-"

# Trees that hold *callers* of lab-secret.sh (docs/ only describes them; tests/
# exercise them). Text files only; the script and the registry are excluded.
_CALLER_TREES = ("ansible", "lab", "src", "tools", "scripts", "components")
_MENTION_RE = re.compile(r"lab-secret\.sh|pki_secret_script")
# Invocation forms whose secret name is parsed out of the line:
#   ansible: command: "<...>/lab-secret.sh <name>" | command: "{{ pki_secret_script }} <name>"
#   shell:   $("<...>/lab-secret.sh" <name>)
_INVOCATION_RES = (
    re.compile(
        r"^\s*(?:ansible\.builtin\.command|command|cmd):\s*\"[^\"]*"
        r"(?:lab-secret\.sh|\{\{\s*pki_secret_script\s*}})\s+([^\"]+?)\s*\"\s*$"
    ),
    re.compile(r"\$\(\s*\"?[^\s\"]*lab-secret\.sh\"?\s+([^\s)]+)\s*\)"),
)
# A Python caller passing a literal name to the provision seam.
_PY_LITERAL_RE = re.compile(r"_source_secret\(\s*\w+\s*,\s*[\"']([^\"']+)[\"']")
# The one templated form allowed: the per-entity keystore password, lowercased.
_PKI_TEMPLATE_RE = re.compile(r"^pki-keyrpwd-\{\{\s*[\w.]+\s*\|\s*lower\s*}}$")
# Prose: comments and ansible task names mention the script without calling it.
_PROSE_RE = re.compile(r"^\s*(?:#|- name:)")
# Every other non-invocation mention, by (file, substring). A mention that is
# neither a parsed call, prose, nor listed here fails the scan — so a caller written
# in a form the parser does not know cannot slip past the registry check.
_NON_CALL_MENTIONS = {
    # The Python seam: names come from each stack's `secrets:` (checked against the
    # topology in test_every_stack_secret_is_registered).
    ("src/mqlab/cli.py", 'lab_script("lab-secret.sh")), name]'),
    ("src/mqlab/cli.py", 'error(f"lab-secret.sh {name} failed'),
    # The script path the pki-distribute command line expands.
    ("ansible/roles/pki-distribute/defaults/main.yml", "pki_secret_script: "),
    # Prose inside the role's fail message.
    ("ansible/roles/opensearch-dashboards/tasks/configure.yml", "secret seam (lab/scripts/"),
}


def registry_names() -> list[str]:
    """The registered names: non-blank, non-comment lines of lab/secret-names."""
    lines = (line.strip() for line in REGISTRY.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


@dataclass
class Sandbox:
    root: Path
    script: Path
    env: dict[str, str]

    @property
    def secrets(self) -> Path:
        return self.root / "build" / "state" / "secrets"

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            ["bash", str(self.script), *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            env=self.env,
            cwd=self.root,
        )

    def wrote_nothing(self) -> bool:
        return not (self.root / "build").exists()


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    scripts = tmp_path / "lab" / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "lab-secret.sh"
    shutil.copy2(SCRIPT, script)
    shutil.copy2(REGISTRY, tmp_path / "lab" / "secret-names")
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    stub = stub_bin / "mqlab"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'if [ "$*" = "build path state" ]; then echo "{tmp_path}/build/state"; exit 0; fi\n'
        'echo "stub mqlab: unexpected args: $*" >&2; exit 97\n'
    )
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}"}
    return Sandbox(root=tmp_path, script=script, env=env)


# --- option-like / usage ----------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["--help"],
        ["-h"],
        ["-x"],
        ["--"],
        ["-"],
        ["--mqweb_admin_password"],
        [],
        ["get", "mqweb_admin_password"],
        ["mqweb_admin_password", "extra"],
    ],
)
def test_option_like_or_wrong_arity_prints_usage_and_writes_nothing(sandbox, args):
    result = sandbox.run(*args)
    assert result.returncode == 2
    assert "usage: lab-secret.sh <name>" in result.stderr
    assert result.stdout == ""
    assert sandbox.wrote_nothing()


# --- unknown / malformed names ----------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "get",  # the stray file the #1499 spike left behind
        "mqweb_admin_pasword",  # a caller typo must not mint a fresh secret
        "pki-keyrpwd-nosuchqm",  # the pki family is the registered CNs only
        "pki-keyrpwd-",
        "pki-keyrpwd-PCMKAPP",  # callers lowercase the CN
        "bad/name",
        "",
    ],
)
def test_unregistered_or_malformed_name_fails_loudly_and_writes_nothing(sandbox, name):
    result = sandbox.run(name)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "lab-secret.sh:" in result.stderr
    assert sandbox.wrote_nothing()


def test_unregistered_name_error_points_at_the_registry(sandbox):
    result = sandbox.run("mqweb_admin_pasword")
    assert "unknown secret name 'mqweb_admin_pasword'" in result.stderr
    assert "lab/secret-names" in result.stderr


def test_missing_registry_fails_loudly_and_writes_nothing(sandbox):
    (sandbox.root / "lab" / "secret-names").unlink()
    result = sandbox.run("mqweb_admin_password")
    assert result.returncode == 1
    assert "registry" in result.stderr
    assert sandbox.wrote_nothing()


# --- registered names still work ----------------------------------------------------


def test_every_registered_name_is_minted_once_and_reused(sandbox):
    for name in registry_names():
        first = sandbox.run(name)
        assert first.returncode == 0, (name, first.stderr)
        value = first.stdout.strip()
        assert re.fullmatch(r"[0-9a-f]{32}", value), (name, value)
        assert (sandbox.secrets / name).read_text().strip() == value
        again = sandbox.run(name)
        assert again.returncode == 0
        assert again.stdout.strip() == value  # idempotent: same value forever after
    assert sorted(p.name for p in sandbox.secrets.iterdir()) == sorted(registry_names())


# --- registry <-> callers coverage ---------------------------------------------------


def test_registry_entries_are_well_formed_and_unique():
    names = registry_names()
    assert names, "lab/secret-names registers no names"
    assert len(names) == len(set(names)), "duplicate entry in lab/secret-names"
    for name in names:
        assert re.fullmatch(r"[a-z0-9_][a-z0-9_-]*", name), name


@dataclass
class CallerScan:
    literals: dict[str, list[str]]  # name -> caller locations
    pki_templates: list[str]  # caller locations using pki-keyrpwd-{{ <cn> | lower }}
    unclassified: list[str]  # mentions neither a parsed call, prose, nor listed


def _caller_files() -> list[Path]:
    return [
        path
        for tree in _CALLER_TREES
        for path in sorted((REPO / tree).rglob("*"))
        if path.is_file() and path not in (SCRIPT, REGISTRY)
    ]


def _scan_callers() -> CallerScan:
    scan = CallerScan(literals={}, pki_templates=[], unclassified=[])
    for path in _caller_files():
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue  # binary asset — cannot call a shell script
        rel = str(path.relative_to(REPO))
        for line in text.splitlines():
            names = _PY_LITERAL_RE.findall(line)
            if _MENTION_RE.search(line):
                parsed = [m.group(1) for r in _INVOCATION_RES if (m := r.search(line))]
                names += parsed
                listed = any(rel == f and sub in line for f, sub in _NON_CALL_MENTIONS)
                if not (parsed or listed or _PROSE_RE.match(line)):
                    scan.unclassified.append(f"{rel}: {line.strip()}")
            for name in names:
                if _PKI_TEMPLATE_RE.match(name):
                    scan.pki_templates.append(rel)
                else:
                    scan.literals.setdefault(name, []).append(rel)
    return scan


@pytest.fixture
def real_topology_root(monkeypatch, tmp_path: Path) -> Path:
    # The real committed topology, rendered into tmp (never the repo's build/).
    (tmp_path / "lab").mkdir()
    shutil.copy2(REPO / "lab" / "topology.yaml", tmp_path / "lab" / "topology.yaml")
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    return tmp_path


def test_caller_scan_finds_the_known_callers():
    # Guards the scan itself: a regex that silently stopped matching would make the
    # coverage test below vacuous.
    scan = _scan_callers()
    assert "lab/scripts/dr-provision.sh" in scan.literals["pcmk_hacluster_password"]
    assert (
        "ansible/roles/opensearch-dashboards/tasks/configure.yml"
        in scan.literals["opensearch_admin_password"]
    )
    assert set(scan.pki_templates) >= {
        "ansible/roles/lab-pki/tasks/entity.yml",
        "ansible/roles/mq-nativeha/tasks/tls.yml",
        "ansible/roles/pki-distribute/tasks/main.yml",
    }


def test_every_mention_of_the_script_is_a_parsed_call_or_classified():
    # Drift guard for the scan: a caller in a form the parser does not recognise
    # would otherwise escape the registry check. Teach _INVOCATION_RES the new form,
    # or list a non-calling mention in _NON_CALL_MENTIONS.
    assert _scan_callers().unclassified == []


def test_every_literal_caller_name_is_registered():
    registered = set(registry_names())
    unregistered = {
        name: where for name, where in _scan_callers().literals.items() if name not in registered
    }
    assert not unregistered, (
        f"lab-secret.sh callers use names missing from lab/secret-names "
        f"(or a templated name other than pki-keyrpwd-{{{{ <cn> | lower }}}}): {unregistered}"
    )


def test_every_stack_secret_is_registered(real_topology_root):
    # src/mqlab/cli.py sources each stack's `secrets:` through lab-secret.sh.
    registered = set(registry_names())
    stack_secrets = {s for stack in lab_stacks().values() for s in stack.secrets}
    assert stack_secrets, "topology declares no stack secrets — scan is vacuous"
    assert stack_secrets <= registered, stack_secrets - registered


def test_pki_family_matches_the_personal_pki_entities_exactly(real_topology_root):
    # lab-pki/entity.yml mints pki-keyrpwd-<cn|lower> for every *personal* entity
    # (trust_only entities have no keystore); mq-nativeha tls and pki-distribute
    # read it back by the same CN. Exact equality: a new/renamed stack or entity
    # must be registered, and a retired one must be dropped.
    entities = json.loads(cli._render_pki_entities().read_text())
    expected = {f"{PKI_PREFIX}{e['cn'].lower()}" for e in entities if e["kind"] == "personal"}
    registered = {n for n in registry_names() if n.startswith(PKI_PREFIX)}
    assert registered == expected


def test_every_fixed_registered_name_has_a_caller(real_topology_root):
    # No dead registrations: each non-pki name is used by a scanned caller or a
    # stack's `secrets:` list.
    used = set(_scan_callers().literals)
    used |= {s for stack in lab_stacks().values() for s in stack.secrets}
    fixed = {n for n in registry_names() if not n.startswith(PKI_PREFIX)}
    assert fixed <= used, fixed - used
