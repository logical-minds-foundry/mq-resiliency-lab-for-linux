"""The baked-box fleet model + read-only decision surface (epic .github#91, T1).

`mqlab` owns the baked-image layer as a first-class fleet. This module carries
the fleet definition (DERIVED from `cli._LOCAL_BOX_BUILDERS`, never a second
literal list) and the read-only `box status` decision surface: for each box it
shells the existing shell builder's `--dry-run` — the single source of truth for
the bake/staleness decision — parses the emitted decision line, and renders a
table. It never re-derives the manifest-hash or age logic; it reads the
builder's own decision and reports it. All subprocess seams are monkeypatchable
so tests never touch real git/fs/virsh/vagrant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mqlab import cli
from mqlab.hostfacts import probe
from mqlab.paths import repo_root, state
from mqlab.platforms import build_domain_virt
from mqlab.runner import Command, SubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class BoxSpec:
    """One local-built box: its builder script, durable cache artifact, and
    whether it carries a manifest-hash (False only for the base OS box, which is
    built once from the DVD and has no bake-recipe hash to compare)."""

    name: str
    builder: str  # builder script path, relative to repo root
    cache_artifact: str  # durable filename under build/state/boxes/
    has_manifest_hash: bool


# The base OS box is built once from the credentialed RHEL DVD (build-box.sh);
# the fat boxes are provision-then-snapshot bakes (build-fatbox.sh) whose cache
# is keyed `<box>.box` beside a `<box>.manifest-hash`. The base box's cache
# filename mirrors build-box.sh's `rhel-9.6-x86_64-libvirt.box`.
_BASE_BOX = "rhel/9.6-x86_64"
_BASE_ARTIFACT = "rhel-9.6-x86_64-libvirt.box"

# Actions the builders' --dry-run may report. Ordered so the longer FORCE-BUILD
# is matched before its BUILD substring.
_ACTIONS = ("FORCE-BUILD", "STALE", "BUILD", "REUSE")


def _build_fleet() -> dict[str, BoxSpec]:
    """The five-box fleet, DERIVED from cli._LOCAL_BOX_BUILDERS (one source)."""
    fleet: dict[str, BoxSpec] = {}
    for name, builder in cli._LOCAL_BOX_BUILDERS.items():
        if name == _BASE_BOX:
            fleet[name] = BoxSpec(
                name=name, builder=builder, cache_artifact=_BASE_ARTIFACT, has_manifest_hash=False
            )
        else:
            fleet[name] = BoxSpec(
                name=name, builder=builder, cache_artifact=f"{name}.box", has_manifest_hash=True
            )
    return fleet


FLEET: dict[str, BoxSpec] = _build_fleet()


@dataclass(frozen=True)
class BoxDecision:
    """The rendered per-box status: cache/age/hash/registration + the builder's
    REUSE/BUILD/STALE/FORCE-BUILD decision."""

    name: str
    cached: bool
    age_days: int | None
    hash_match: bool | None  # None for the base box, or when not evaluated
    registered: bool
    action: str


# --------------------------------------------------------------------------- #
# Subprocess seams (monkeypatched in tests)                                   #
# --------------------------------------------------------------------------- #
def _capture(cmd: Command) -> str:
    """Run a command, returning its merged stdout+stderr as one string."""
    lines: list[str] = []
    SubprocessRunner().run(cmd, lines.append)
    return "\n".join(lines)


def _run_builder_dry_run(name: str) -> str:
    """Shell the box's builder with --dry-run and return its decision output.

    Mirrors cli._box_build_steps: fat boxes are box-parameterized (`--box`); the
    base-OS builder is not. Virtualization flags come from the single authority,
    build_domain_virt(probe())."""
    spec = FLEET[name]
    domain_type, cpu_mode = build_domain_virt(probe())
    argv = ["bash", str(repo_root() / spec.builder)]
    if spec.builder.endswith("build-fatbox.sh"):
        argv += ["--box", name]
    argv += ["--domain-type", domain_type, "--cpu-mode", cpu_mode, "--dry-run"]
    cmd = Command(argv, cwd=repo_root() / "lab", env=cli._vagrant_env())
    return _capture(cmd)


def _boxes_cache_dir() -> Path:
    """The durable box cache dir — build/state/boxes (shared live-lab state)."""
    return state("boxes")


def _cache_present(name: str) -> bool:
    """Whether the box's durable cache artifact exists."""
    return (_boxes_cache_dir() / FLEET[name].cache_artifact).is_file()


def _registered_boxes() -> dict[str, str]:
    """`vagrant box list` parsed to {box_name: info} (via cli.parse_box_list)."""
    cmd = Command(["vagrant", "box", "list"], cwd=repo_root() / "lab", env=cli._vagrant_env())
    return cli.parse_box_list(_capture(cmd))


# --------------------------------------------------------------------------- #
# Decision-line parsing (the builder is the single source of truth)           #
# --------------------------------------------------------------------------- #
def _parse_action(text: str) -> str:
    """Extract the REUSE/BUILD/STALE/FORCE-BUILD action from builder output."""
    for token in _ACTIONS:
        if token in text:
            return token
    raise ValueError(f"mqlab box: could not parse a build decision from: {text!r}")


def _parse_age_days(text: str) -> int | None:
    """The cache age the builder reported (`Nd`), if any; else None. The builder
    prints an age only when it is decision-relevant (a staleness NOTICE / STALE),
    so a fresh cache legitimately has no reported age."""
    match = re.search(r"(\d+)d\b", text)
    return int(match.group(1)) if match else None


def _derive_hash_match(spec: BoxSpec, action: str, *, cached: bool) -> bool | None:
    """The manifest-hash verdict encoded in the builder's own action — never
    re-computed here. REUSE/STALE mean the stored hash matched; a BUILD over a
    present cache means it drifted; a BUILD with no cache (or a FORCE-BUILD)
    evaluated no hash."""
    if not spec.has_manifest_hash:
        return None
    if action in ("REUSE", "STALE"):
        return True
    if action == "BUILD":
        return False if cached else None
    return None  # FORCE-BUILD: the hash is not evaluated


def box_decision(name: str) -> BoxDecision:
    """The full status for one box: shell its builder --dry-run, parse the
    decision, and cross-check cache presence + vagrant registration."""
    spec = FLEET[name]
    text = _run_builder_dry_run(name)
    action = _parse_action(text)
    cached = _cache_present(name)
    return BoxDecision(
        name=name,
        cached=cached,
        age_days=_parse_age_days(text),
        hash_match=_derive_hash_match(spec, action, cached=cached),
        registered=name in _registered_boxes(),
        action=action,
    )


# --------------------------------------------------------------------------- #
# Rendering                                                                   #
# --------------------------------------------------------------------------- #
def _fmt_hash(spec: BoxSpec, hash_match: bool | None) -> str:
    if not spec.has_manifest_hash:
        return "N/A"
    if hash_match is None:
        return "-"
    return "match" if hash_match else "mismatch"


def _fmt_row(d: BoxDecision) -> str:
    return (
        f"{d.name:<18} "
        f"{('yes' if d.cached else 'no'):<7} "
        f"{(f'{d.age_days}d' if d.age_days is not None else '-'):<6} "
        f"{_fmt_hash(FLEET[d.name], d.hash_match):<9} "
        f"{('yes' if d.registered else 'no'):<11} "
        f"{d.action}"
    )


def render_status(names: list[str]) -> str:
    """Render the fleet status table for the given box names."""
    header = f"{'BOX':<18} {'CACHED':<7} {'AGE':<6} {'HASH':<9} {'REGISTERED':<11} DECISION"
    rows = [_fmt_row(box_decision(name)) for name in names]
    return "\n".join([header, *rows])
