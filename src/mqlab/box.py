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

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import typer

from mqlab import cli
from mqlab.hostfacts import probe
from mqlab.orchestrator import StepFailedError, run_steps
from mqlab.paths import repo_root, state
from mqlab.platforms import build_domain_virt
from mqlab.runner import Command, SubprocessRunner


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
# The RHEL version the base box bakes from ("9.6"), parsed from _BASE_BOX so the
# DVD-verify locus and the fleet name stay in lockstep.
_RHEL_VERSION = _BASE_BOX.split("/", 1)[1].split("-", 1)[0]

# Actions the builders' --dry-run may report. Ordered so the longer FORCE-BUILD
# is matched before its BUILD substring.
_ACTIONS = ("FORCE-BUILD", "STALE", "BUILD", "REUSE")


def _build_fleet() -> dict[str, BoxSpec]:
    """The six-box fleet, DERIVED from cli._LOCAL_BOX_BUILDERS (one source)."""
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


# --------------------------------------------------------------------------- #
# RHEL DVD verify-and-guide (epic .github#91, T4)                              #
# --------------------------------------------------------------------------- #
# Pinned RHEL DVD SHA-256 checksums, keyed by RHEL version. Each value is the
# checksum Red Hat publishes beside the DVD on the Customer Portal download page
# (Downloads -> Red Hat Enterprise Linux -> the "x86_64 DVD ISO" row's SHA-256).
# Pinning a version turns on integrity verification of the operator-supplied ISO
# before the expensive base-box BUILD.
#
# 9.6 is DELIBERATELY LEFT UNPINNED: the real Red Hat checksum is NOT fabricated
# here. The operator pastes it in from Red Hat's published value. Until a version
# is pinned, verify_rhel_dvd emits a loud NOTICE and PROCEEDS (integrity
# unverified) — the lab built the RHEL box with no SHA check before this
# preflight existed, and hard-blocking on unpinned would regress that working
# cold rebuild.
RHEL_DVD_SHA256: dict[str, str] = {
    # "9.6": "<paste Red Hat's published rhel-9.6-x86_64-dvd.iso SHA-256 here>",
}

# Red Hat's authenticated RHEL download page (operator-supplied; no credential
# handling ever enters the tool — we only check the ISO and guide).
_RHEL_DVD_URL = "https://access.redhat.com/downloads/content/rhel"


def _rhel_dvd_path() -> Path:
    """Canonical path to the operator-supplied RHEL DVD ISO.

    Honors MQLAB_RHEL_ISO then RHEL_ISO exactly like lab/scripts/stage-rhel-iso.sh;
    otherwise defaults to the shared state/ bucket at
    state("rhel-9.6-x86_64-dvd.iso")."""
    override = os.environ.get("MQLAB_RHEL_ISO") or os.environ.get("RHEL_ISO")
    if override:
        return Path(override)
    return state("rhel-9.6-x86_64-dvd.iso")


def _sha256_file(path: Path) -> str:
    """The file's SHA-256, streamed in 1 MiB chunks (the DVD is ~12.7 GB)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_rhel_dvd(version: str) -> None:
    """Preflight the RHEL DVD before the base-box BUILD (fail-loud).

    - MISSING file          -> typer.Exit(2) with actionable guidance (version,
                               Red Hat download URL, destination path).
    - pinned but MISMATCHED -> typer.Exit(2), fail-loud (corrupt / wrong ISO).
    - UNPINNED version      -> loud NOTICE, then return (integrity unverified).
    - pinned and MATCHED    -> return.
    """
    path = _rhel_dvd_path()
    if not path.is_file():
        typer.echo(
            f"mqlab box: RHEL {version} DVD ISO not found at {path}.\n"
            f"  The RHEL DVD is operator-supplied — Red Hat gates it behind auth.\n"
            f"  Download the '{version} x86_64 DVD ISO' from {_RHEL_DVD_URL}\n"
            f"  and place it at {path} (or point MQLAB_RHEL_ISO / RHEL_ISO at it).",
            err=True,
        )
        raise typer.Exit(code=2)

    pinned = RHEL_DVD_SHA256.get(version)
    if not pinned:
        typer.echo(
            f"NOTICE: RHEL {version} DVD checksum not pinned in RHEL_DVD_SHA256 — "
            f"integrity not verified; pin it to enable verification.",
            err=True,
        )
        return

    actual = _sha256_file(path)
    if actual != pinned:
        typer.echo(
            f"mqlab box: RHEL {version} DVD ISO at {path} failed SHA-256 verification.\n"
            f"  expected {pinned}\n"
            f"  actual   {actual}\n"
            f"  The ISO is corrupt or the wrong file. Re-download the "
            f"'{version} x86_64 DVD ISO' from {_RHEL_DVD_URL}.",
            err=True,
        )
        raise typer.Exit(code=2)


def _rhel_base_needs_dvd(name: str, *, force: bool) -> bool:
    """Whether building `name` will consume the RHEL DVD.

    Only the RHEL base box (build-box.sh) attaches the ISO — the fat boxes bake
    from the already-built base box. And only a real build needs it: a REUSE of a
    cached base box attaches no ISO. A forced rebuild always rebuilds, so it
    always needs the DVD (and short-circuits the extra dry-run)."""
    if name != _BASE_BOX:
        return False
    if force:
        return True
    return box_decision(name).action != "REUSE"


# --------------------------------------------------------------------------- #
# Mutating verbs — the shared build/rebuild core (epic .github#91, T2)         #
# --------------------------------------------------------------------------- #
def build_boxes(names: list[str], *, force: bool) -> None:
    """Ensure (or force-rebake) each named box, fail-loud.

    For every name, issue that box's builder as one CommandStep — reusing
    cli._box_build_steps (the single source of truth for the builder argv),
    which appends `--rebuild-box` when force. A non-force build is cheap over a
    valid cache: the builder itself makes the REUSE-vs-BUILD decision, so this
    never re-derives that logic. Shared by `box build`/`box rebuild` and by
    bootstrap's `_ensure_local_boxes`, so both drive one path. Raises typer.Exit
    with the failing step's exit code on any builder non-zero (StepFailedError).
    """
    for name in names:
        if _rhel_base_needs_dvd(name, force=force):
            verify_rhel_dvd(_RHEL_VERSION)
    needed = {name: FLEET[name].builder for name in names}
    steps = cli._box_build_steps(needed, {}, probe(), force=force)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = cli.build_deps("box-build", timestamp)
    try:
        run_steps(
            steps,
            runner=deps.runner,
            renderer=deps.renderer,
            transcript=deps.transcript,
            step_mode=False,
            pauser=deps.pauser,
        )
    except StepFailedError as exc:
        raise typer.Exit(code=exc.exit_code) from exc
    finally:
        deps.transcript.close()


# --------------------------------------------------------------------------- #
# Mutating verb — the clean core (epic .github#91, T3)                          #
# --------------------------------------------------------------------------- #
def _vagrant_box_remove(name: str) -> None:
    """Deregister a box from Vagrant (`vagrant box remove <name>`).

    An already-absent registration is the desired end state, so a "not
    installed" non-zero is swallowed (clean is idempotent); any OTHER non-zero
    surfaces fail-loud — the tool's own output plus its exit code."""
    cmd = Command(
        ["vagrant", "box", "remove", name],
        cwd=repo_root() / "lab",
        env=cli._vagrant_env(),
    )
    lines: list[str] = []
    code = SubprocessRunner().run(cmd, lines.append)
    if code == 0:
        return
    output = "\n".join(lines)
    if "not installed" in output.lower():
        return
    typer.echo(output, err=True)
    raise typer.Exit(code=code)


def clean_boxes(names: list[str]) -> list[str]:
    """Make each named box PRISTINE and return what was removed, for the caller
    to echo.

    Per box: unlink its durable cache artifact under build/state/boxes and (when
    it carries one) its `<name>.manifest-hash`, then deregister it from Vagrant.
    An absent file is silently skipped (idempotent) — only what actually existed
    is reported. The next `box build` re-bakes from scratch; this is the
    destructive/expensive verb the `--all` guard protects."""
    cache_dir = _boxes_cache_dir()
    removed: list[str] = []
    for name in names:
        spec = FLEET[name]
        artifact = cache_dir / spec.cache_artifact
        if artifact.is_file():
            artifact.unlink()
            removed.append(str(artifact))
        if spec.has_manifest_hash:
            manifest = cache_dir / f"{name}.manifest-hash"
            if manifest.is_file():
                manifest.unlink()
                removed.append(str(manifest))
        _vagrant_box_remove(name)
        removed.append(f"vagrant box '{name}'")
    return removed
