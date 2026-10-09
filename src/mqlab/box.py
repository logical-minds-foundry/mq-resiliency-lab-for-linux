"""The baked-box fleet model + read-only decision surface (epic .github#91, T1).

`mqlab` owns the baked-image layer as a first-class fleet. The fleet is DERIVED
from the OS version catalog (lab/versions.yaml, via mqlab.versions): every box the
catalog can generate for this host (``<role>-<os><major>``), plus one RHEL base box
(``rhel/<major>-x86_64``) per catalog RHEL major (epic .github#280). Nothing here
writes a box name or OS version by hand.

The shell builders are dumb: this module passes them every input as a flag
(builder_args). The read-only `box status` decision surface shells each builder's
`--dry-run` — the single source of truth for the bake/staleness decision — parses
the emitted decision line, and renders a table. It never re-derives the
manifest-hash or age logic. All subprocess seams are monkeypatchable so tests never
touch real git/fs/virsh/vagrant.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
import yaml

from mqlab import cli, component, mqexporter, venvsync
from mqlab.hostfacts import HostFacts, probe
from mqlab.manifest import DEFAULT_MQ_VERSION
from mqlab.orchestrator import CommandStep, StepFailedError, run_steps
from mqlab.paths import cache, repo_root, state
from mqlab.platforms import box_build_arch, box_build_domain_virt, ensure_resolved
from mqlab.retired_boxes import RETIRED_BASE_ARTIFACTS, RETIRED_BOX_NAMES
from mqlab.runner import Command, SubprocessRunner
from mqlab.runtime import RuntimePinError
from mqlab.versions import (
    HOST_ARCHES,
    SHARED_ROLES,
    BoxEntry,
    Catalog,
    VersionError,
    host_can_run,
    load_catalog,
    stack_roles,
)

if TYPE_CHECKING:
    from mqlab.versions import BuildFile, OsEntry

# The two dumb builders (repo-relative). A base OS box is built from install media;
# a fat box is a provision-then-snapshot bake on top of a base box.
_FATBOX_BUILDER = "lab/boxes/build-fatbox.sh"
_RHEL_BASE_BUILDER = "lab/boxes/rhel/build-box.sh"


@dataclass(frozen=True)
class BoxSpec:
    """One local-built box: its builder script, build/guest arch, durable cache
    artifact, whether it carries a manifest-hash (False only for a base OS box, which
    is built once from the DVD and has no bake-recipe hash to compare), and the catalog
    inputs its builder is invoked with."""

    name: str
    builder: str  # builder script path, relative to repo root
    arch: str  # build/guest arch (#103 D1): RHEL x86_64; Ubuntu tracks the host
    cache_artifact: str  # durable filename under build/state/boxes/
    has_manifest_hash: bool
    os: OsEntry  # the catalog OS entry the box is built on
    role: str | None = None  # the box role; None for a base OS box
    bake_stem: str | None = None  # ansible/bake-<stem>.yml; None for a base OS box
    mq_bearing: bool = False
    components: tuple[str, ...] = ()  # guest components the box bakes (epic .github#294)
    runtime_pin: str = ""  # the catalog's runtime.python token; "" for a base OS box

    @property
    def manifest_hash_artifact(self) -> str:
        """The manifest-hash filename beside the cache artifact — arch-suffixed to
        match `<box>-<arch>.box` (#103 D4). Only meaningful for fat boxes
        (has_manifest_hash True); the base box carries none."""
        return f"{self.name}-{self.arch}.manifest-hash"

    @property
    def components_record_artifact(self) -> str:
        """What a component-baking fat box baked, recorded beside its cache artifact by
        build-fatbox.sh (epic .github#294 spec §5.8). MUST match its COMPONENTS_FILE."""
        return f"{self.name}-{self.arch}.components.json"


def os_pin(entry: OsEntry) -> str:
    """The OS pin folded into a fat box's manifest hash: ``<base_box>@<pin>``.

    The pin is the RHEL point release or the Ubuntu cloud-image box version, so a
    re-pin (9.x -> 9.y, a new cloud image) flips the hash and forces a rebake
    (spec §4.7). An entry with neither floats, and says so (``@none``)."""
    return f"{entry.base_box}@{entry.point or entry.base_box_version or 'none'}"


def base_cache_artifact(base_box: str) -> str:
    """The durable cache filename of a base OS box: its name with '/' -> '-' plus
    '.box' (``rhel/<N>-x86_64`` -> ``rhel-<N>-x86_64.box``). MUST match the cache path
    lab/boxes/rhel/build-box.sh writes."""
    return base_box.replace("/", "-") + ".box"


# --------------------------------------------------------------------------- #
# Fleet derivation                                                            #
# --------------------------------------------------------------------------- #
def _load_topology() -> dict[str, Any]:
    """The source topology (lab/topology.yaml)."""
    data: dict[str, Any] = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return data


def _topology_stack_roles(catalog: Catalog) -> dict[str, set[str]]:
    """stack -> the stack-OS box roles its nodes declare (`box: <role>`, epic
    .github#280), via versions.stack_roles — which fails loudly on an unknown role, so a
    topology typo can never silently drop a box from the fleet."""
    return stack_roles(_load_topology(), catalog)


def _fat_spec(entry: BoxEntry, facts: HostFacts, runtime_pin: str) -> BoxSpec:
    arch = box_build_arch({"arch": entry.os.arch_pin}, facts)
    return BoxSpec(
        name=entry.name,
        builder=_FATBOX_BUILDER,
        arch=arch,
        cache_artifact=f"{entry.name}-{arch}.box",
        has_manifest_hash=True,
        os=entry.os,
        role=entry.role,
        bake_stem=entry.bake_stem,
        mq_bearing=entry.mq_bearing,
        components=entry.components,
        runtime_pin=runtime_pin,
    )


def _base_spec(entry: OsEntry, facts: HostFacts) -> BoxSpec:
    return BoxSpec(
        name=entry.base_box,
        builder=_RHEL_BASE_BUILDER,
        arch=box_build_arch({"arch": entry.arch_pin}, facts),
        cache_artifact=base_cache_artifact(entry.base_box),
        has_manifest_hash=False,
        os=entry,
    )


def _build_fleet(
    facts: HostFacts | None = None,
    catalog: Catalog | None = None,
    stack_roles: dict[str, set[str]] | None = None,
) -> dict[str, BoxSpec]:
    """The fleet, DERIVED from the catalog (epic .github#280).

    Every box Catalog.all_boxes generates for this host (the infra boxes on the infra
    OS, then each stack's roles on each supported OS the host can run), plus one RHEL
    base box per catalog RHEL major the host can run. Each fat box's build/guest arch
    comes from the single authority platforms.box_build_arch (#103 D1) — RHEL is
    x86_64-pinned, an un-pinned Ubuntu box tracks the host — and its cache artifact is
    arch-suffixed `<box>-<arch>.box` (D4). facts/catalog/stack_roles are injected for
    testing; they default to the live host, lab/versions.yaml and the topology."""
    facts = facts or probe()
    catalog = catalog if catalog is not None else load_catalog()
    roles = stack_roles if stack_roles is not None else _topology_stack_roles(catalog)
    fleet: dict[str, BoxSpec] = {}
    for ref, entry in sorted(catalog.oses.items()):
        if ref.family == "rhel" and host_can_run(entry, facts):
            fleet[entry.base_box] = _base_spec(entry, facts)
    for box_entry in catalog.all_boxes(facts, roles):
        fleet[box_entry.name] = _fat_spec(box_entry, facts, catalog.runtime.token)
    return fleet


FLEET: dict[str, BoxSpec] = _build_fleet()


def boxes_for_build(
    build: BuildFile,
    *,
    facts: HostFacts,
    catalog: Catalog | None = None,
    stack_roles: dict[str, set[str]] | None = None,
) -> list[str]:
    """The boxes a build file needs, for every stack (`mqlab box build --config`).

    The shared boxes (each on its Catalog.shared_os), then, for every stack whose OS
    family the build file covers (a build file naming ``os: rhel:9`` covers the RHEL
    stacks; one with no ``os`` covers every stack at its default), that stack's roles
    on the OS Catalog.stack_os resolves
    — so an unsupported or host-incompatible request fails loudly there. A stack left
    at its default is skipped only when this host cannot run that default (RHEL on
    aarch64), since nothing asked for it."""
    catalog = catalog if catalog is not None else load_catalog()
    roles = stack_roles if stack_roles is not None else _topology_stack_roles(catalog)
    names = [catalog.box(role, catalog.shared_os(role)).name for role in SHARED_ROLES]
    for stack, stack_box_roles in roles.items():
        if stack not in catalog.stacks:
            raise VersionError(
                f"unknown stack {stack!r} (known: {', '.join(sorted(catalog.stacks))}) — "
                "add it under stacks: in lab/versions.yaml"
            )
        default = catalog.stacks[stack]["default"]
        if build.os is None:
            if not host_can_run(catalog.oses[default], facts):
                continue
        elif build.os.family != default.family:
            continue
        ref = catalog.stack_os(stack, build, facts)  # raises (named fix) on a bad request
        names += [catalog.box(role, ref).name for role in sorted(stack_box_roles)]
    return list(dict.fromkeys(names))


# --------------------------------------------------------------------------- #
# Builder invocation (the builders are dumb: every input is a flag)           #
# --------------------------------------------------------------------------- #
def builder_args(spec: BoxSpec, facts: HostFacts) -> list[str]:
    """The builder flags for one box, from its catalog inputs (epic .github#280).

    The domain virt is guest-arch-aware — box_build_domain_virt(spec.arch): native KVM
    when the box's build arch is the host's, TCG otherwise (#731/#732)."""
    domain_type, cpu_mode = box_build_domain_virt(spec.arch, facts)
    virt = ["--domain-type", domain_type, "--cpu-mode", cpu_mode]
    entry = spec.os
    if spec.role is None:  # a RHEL base OS box, built from the DVD
        if entry.point is None or entry.iso is None:  # load_catalog requires both for rhel
            raise VersionError(
                f"base box {spec.name}: os.{entry.ref.family}.{entry.ref.major} needs point "
                "and iso — fix lab/versions.yaml"
            )
        return [
            "--major",
            str(entry.ref.major),
            "--point",
            entry.point,
            "--iso",
            entry.iso,
            *virt,
        ]
    return [
        "--box",
        spec.name,
        "--arch",
        spec.arch,
        "--base-kind",
        entry.ref.family,
        "--base-box",
        entry.base_box,
        "--base-box-version",
        entry.base_box_version or "none",
        "--bake",
        str(spec.bake_stem),
        "--dvd",
        entry.iso or "none",
        "--os-pin",
        os_pin(entry),
        "--mq-bearing",
        "1" if spec.mq_bearing else "0",
        *virt,
        *_component_args(spec),
    ]


def _component_tree(name: str) -> str:
    """A baked component's source identity: its git tree hash at HEAD (seam)."""
    return component.tree_hash(name, SubprocessRunner())


def _component_args(spec: BoxSpec) -> list[str]:
    """The runtime pin and each baked component at its git tree hash (spec §5.8).

    The builder folds both into the manifest hash, which keys on SOURCE: a committed
    component change flips it even before the component is rebuilt. A box that bakes
    components is also handed the install-vars file build_boxes writes before a bake."""
    try:
        baked = ",".join(f"{name}@{_component_tree(name)}" for name in spec.components)
    except component.ComponentError as exc:
        typer.echo(f"mqlab box: {spec.name}: {exc}", err=True)
        raise typer.Exit(code=1) from None
    args = ["--runtime-pin", spec.runtime_pin, "--components", baked]
    if spec.components:
        args += ["--install-vars", str(component.install_vars_path())]
    return args


def _builder_argv(spec: BoxSpec, facts: HostFacts) -> list[str]:
    return ["bash", str(repo_root() / spec.builder), *builder_args(spec, facts)]


def _build_steps(plan: list[tuple[str, bool]], facts: HostFacts) -> list[CommandStep]:
    """One builder CommandStep per (box, force) in plan order; force appends
    `--rebuild-box` so the builder overwrites its cache (`box rebuild`, #91).

    DEPRECATED, not removed (#103 D11): an emulated cross-arch box build (a box that
    pins an arch other than the host's, e.g. RHEL on Apple Silicon) is refused. The
    catalog fleet already omits such boxes on that host (Catalog.all_boxes skips them);
    this guard keeps the refusal explicit should one be named anyway."""
    steps: list[CommandStep] = []
    for name, force in plan:
        spec = FLEET[name]
        if spec.os.arch_pin is not None and spec.os.arch_pin != facts.arch:
            raise StepFailedError(
                f"box {name} pins arch {spec.os.arch_pin} but this host is {facts.arch}: "
                f"emulated cross-arch box builds are disabled (#103 D11). "
                f"Build it on the x86 host.",
                2,
            )
        argv = _builder_argv(spec, facts)
        if force:
            argv.append("--rebuild-box")
        steps.append(CommandStep(f"box {name}", Command(argv)))  # noqa: S607
    return steps


def _build_plan(names: list[str], *, force: bool) -> list[tuple[str, bool]]:
    """(box, force) in build order: the locally-built base box of each named fat box
    that is about to BAKE comes FIRST (build-fatbox.sh requires it registered), then
    the named boxes.

    The base is pulled in only when its fat box will actually bake — a forced rebuild,
    or a builder decision other than REUSE — exactly when build-fatbox.sh used to build
    it itself. A fat box REUSEd from cache never needs its base, so a bootstrap after a
    VM rebuild does not re-register a base box it will not use. A base box pulled in as
    a dependency is ensured, never forced: `box rebuild` of a fat box must not trigger a
    45-90 minute base rebuild; name the base box to force it."""

    def bakes(name: str) -> bool:
        return force or box_decision(name).action != "REUSE"

    deps = [
        FLEET[n].os.base_box
        for n in dict.fromkeys(names)
        if FLEET[n].role is not None and FLEET[n].os.base_box in FLEET and bakes(n)
    ]
    plan = [(base, False) for base in dict.fromkeys(deps) if base not in names]
    return plan + [(name, force) for name in dict.fromkeys(names)]


def _ensure_component_artifacts(plan: list[tuple[str, bool]], facts: HostFacts) -> None:
    """Build what a bake needs before the builder runs (epic .github#294 spec §5.8).

    For every planned box that bakes guest components and will actually BAKE (forced, or
    a builder decision other than REUSE), ensure each component's artifact for its
    current tree hash exists — `mqlab component build` runs here, with its full test
    gate, when it does not — then write the install-vars file the builder hands the
    bake (the runtime tarball for this host's arch plus the staged artifacts). A box
    REUSEd from cache needs neither. Uncommitted component changes refuse, naming the
    fix, exactly as `mqlab component build` does."""
    names = list(
        dict.fromkeys(
            name
            for box_name, force in plan
            if FLEET[box_name].components and (force or box_decision(box_name).action != "REUSE")
            for name in FLEET[box_name].components
        )
    )
    if not names:
        return
    runner = SubprocessRunner()
    try:
        artifacts = [
            component.ensure_built(name, runner=runner, on_line=typer.echo) for name in names
        ]
        component.install_vars(artifacts, facts.arch)
    except (component.ComponentError, RuntimePinError, VersionError) as exc:
        typer.echo(f"mqlab box build: {exc}", err=True)
        raise typer.Exit(code=1) from None


# --------------------------------------------------------------------------- #
# Subprocess seams (monkeypatched in tests)                                   #
# --------------------------------------------------------------------------- #
def _capture(cmd: Command) -> str:
    """Run a command, returning its merged stdout+stderr as one string."""
    lines: list[str] = []
    SubprocessRunner().run(cmd, lines.append)
    return "\n".join(lines)


def _run_builder_dry_run(name: str) -> str:
    """Shell the box's builder with --dry-run and return its decision output, invoked
    with exactly the flags a real build uses (builder_args)."""
    argv = [*_builder_argv(FLEET[name], probe()), "--dry-run"]
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


# Actions the builders' --dry-run may report. Ordered so the longer FORCE-BUILD
# is matched before its BUILD substring.
_ACTIONS = ("FORCE-BUILD", "STALE", "BUILD", "REUSE")


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


def baked_components(name: str) -> str:
    """What the cached box baked, from build-fatbox.sh's ``<box>-<arch>.components.json``
    (epic .github#294 spec §5.8): ``<name>@<tree12>,… (<runtime pin>)``, or ``-`` when
    the box carries no record (a base box, an uncached box, or one baking no component).
    A record that is not valid JSON of that shape fails loud, naming the fix."""
    spec = FLEET[name]
    path = _boxes_cache_dir() / spec.components_record_artifact
    if not spec.has_manifest_hash or not path.is_file():
        return "-"
    try:
        record = json.loads(path.read_text())
        baked = ",".join(f"{c}@{str(t)[:12]}" for c, t in sorted(record["components"].items()))
        return f"{baked} ({record['runtime']})"
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(
            f"mqlab box: {path} is not a components record ({exc!r}) — rebake the box: "
            f"mqlab box rebuild {name}"
        ) from exc


def _fmt_row(d: BoxDecision) -> str:
    return (
        f"{d.name:<22} "
        f"{FLEET[d.name].arch:<8} "
        f"{('yes' if d.cached else 'no'):<7} "
        f"{(f'{d.age_days}d' if d.age_days is not None else '-'):<6} "
        f"{_fmt_hash(FLEET[d.name], d.hash_match):<9} "
        f"{('yes' if d.registered else 'no'):<11} "
        f"{d.action:<12} "
        f"{baked_components(d.name)}"
    )


def render_status(names: list[str]) -> str:
    """Render the fleet status table for the given box names."""
    header = (
        f"{'BOX':<22} {'ARCH':<8} {'CACHED':<7} {'AGE':<6} {'HASH':<9} {'REGISTERED':<11} "
        f"{'DECISION':<12} COMPONENTS"
    )
    rows = [_fmt_row(box_decision(name)) for name in names]
    return "\n".join([header, *rows])


# --------------------------------------------------------------------------- #
# Per-host cache migration (#103 D5, T4; the box rename, epic .github#280)     #
# --------------------------------------------------------------------------- #
def migrate_box_cache(
    boxes_dir: Path | None = None, *, dry_run: bool = False
) -> list[tuple[str, str]]:
    """Rename legacy box cache entries to their current names, in place.

    Two one-time migrations:

    - Pre-#103 the cache keyed fat boxes purely by name (`<box>.box` +
      `<box>.manifest-hash`); the arch-native scheme keys them `<box>-<arch>.box`
      (#103 D4). Every legacy fat box was x86_64-pinned, so each maps to its `-x86_64`
      form.
    - The `<role>-<os><major>` rename (epic .github#280) retired the RHEL base box's
      old name. A base box carries no manifest hash, so its cached image is still good:
      rename it to the new base box's artifact rather than rebuild it from the DVD
      (RETIRED_BASE_ARTIFACTS). The retired fat boxes' caches are dead and are deleted
      by `mqlab box gc` instead (clean_retired_cache).

    Idempotent: a no-op once renamed, and it never clobbers an existing target (a mixed
    old/new cache is left for the operator to resolve). Returns the (src, dst) pairs it
    renamed (or, under dry_run, would rename). Defaults to the durable box cache dir;
    boxes_dir is injected for testing."""
    cache_dir = boxes_dir if boxes_dir is not None else _boxes_cache_dir()
    moves: list[tuple[str, str]] = []
    for spec in FLEET.values():
        if spec.has_manifest_hash:
            moves += [
                (f"{spec.name}.box", f"{spec.name}-x86_64.box"),
                (f"{spec.name}.manifest-hash", f"{spec.name}-x86_64.manifest-hash"),
            ]
    for legacy_artifact, new_box in RETIRED_BASE_ARTIFACTS.values():
        moves.append((legacy_artifact, base_cache_artifact(new_box)))
    renamed: list[tuple[str, str]] = []
    for legacy, new in moves:
        src = cache_dir / legacy
        dst = cache_dir / new
        if src.is_file() and not dst.exists():
            renamed.append((str(src), str(dst)))
            if not dry_run:
                src.rename(dst)
    return renamed


# --------------------------------------------------------------------------- #
# RHEL DVD verify-and-guide (epic .github#91, T4)                              #
# --------------------------------------------------------------------------- #
# Pinned RHEL DVD SHA-256 checksums, keyed by RHEL point release (the catalog's
# os.rhel.<major>.point). Each value is the checksum Red Hat publishes beside the DVD
# on the Customer Portal download page (Downloads -> Red Hat Enterprise Linux -> the
# "x86_64 DVD ISO" row's SHA-256). Pinning a release turns on integrity verification
# of the operator-supplied ISO before the expensive base-box BUILD.
#
# Every release is DELIBERATELY LEFT UNPINNED: the real Red Hat checksum is NOT
# fabricated here. The operator pastes it in from Red Hat's published value. Until a
# release is pinned, verify_rhel_dvd emits a loud NOTICE and PROCEEDS (integrity
# unverified) — the lab built the RHEL box with no SHA check before this preflight
# existed, and hard-blocking on unpinned would regress that working cold rebuild.
RHEL_DVD_SHA256: dict[str, str] = {
    # "<point>": "<paste Red Hat's published SHA-256 of that release's x86_64 DVD here>",
}

# Red Hat's authenticated RHEL download page (operator-supplied; no credential
# handling ever enters the tool — we only check the ISO and guide).
_RHEL_DVD_URL = "https://access.redhat.com/downloads/content/rhel"


def _rhel_dvd_path(iso: str) -> Path:
    """Canonical path to the operator-supplied RHEL DVD ISO named ``iso`` (the catalog's
    os.rhel.<major>.iso).

    Honors MQLAB_RHEL_ISO then RHEL_ISO exactly like lab/scripts/stage-rhel-iso.sh;
    otherwise defaults to the shared state/ bucket at state(iso)."""
    override = os.environ.get("MQLAB_RHEL_ISO") or os.environ.get("RHEL_ISO")
    if override:
        return Path(override)
    return state(iso)


def _sha256_file(path: Path) -> str:
    """The file's SHA-256, streamed in 1 MiB chunks (the DVD is ~12.7 GB)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_rhel_dvd(entry: OsEntry) -> None:
    """Preflight the RHEL DVD for catalog entry ``entry`` before the base-box BUILD
    (fail-loud). The release is the entry's point; the file is its iso.

    - MISSING file          -> typer.Exit(2) with actionable guidance (version,
                               Red Hat download URL, destination path).
    - pinned but MISMATCHED -> typer.Exit(2), fail-loud (corrupt / wrong ISO).
    - UNPINNED version      -> loud NOTICE, then return (integrity unverified).
    - pinned and MATCHED    -> return.
    """
    if entry.point is None or entry.iso is None:  # load_catalog requires both for rhel
        raise VersionError(
            f"os.{entry.ref.family}.{entry.ref.major} needs point and iso to verify its "
            "DVD — fix lab/versions.yaml"
        )
    version = entry.point
    path = _rhel_dvd_path(entry.iso)
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

    Only a RHEL base box (build-box.sh) attaches the ISO for its install — the fat
    boxes bake from the already-built base box. And only a real build needs it: a
    REUSE of a cached base box attaches no ISO. A forced rebuild always rebuilds, so it
    always needs the DVD (and short-circuits the extra dry-run)."""
    if FLEET[name].role is not None:
        return False
    if force:
        return True
    return box_decision(name).action != "REUSE"


# --------------------------------------------------------------------------- #
# Mutating verbs — the shared build/rebuild core (epic .github#91, T2)         #
# --------------------------------------------------------------------------- #
def build_boxes(names: list[str], *, force: bool) -> None:
    """Ensure (or force-rebake) each named box, fail-loud.

    Issues each box's builder as one CommandStep (_build_steps, with the flags
    builder_args derives from the catalog), local base boxes first (_build_plan);
    force appends `--rebuild-box` for the named boxes. A non-force build is cheap over
    a valid cache: the builder itself makes the REUSE-vs-BUILD decision, so this never
    re-derives that logic. Shared by `box build`/`box rebuild` and by bootstrap's
    `_ensure_local_boxes`, so both drive one path. Raises typer.Exit with the failing
    step's exit code on any builder non-zero (StepFailedError).
    """
    # Sync the dev venv to uv.lock before the bake spawns venv-dependent tools
    # (build-fatbox.sh -> ansible-playbook); a stale venv otherwise dies deep in
    # the subprocess with `command not found` (exit 127) (#776).
    venvsync.ensure_venv_current()
    # The builder's final `vagrant box add` loads the lab Vagrantfile, which guards
    # on build/work/lab/topology.resolved.yaml (#276). Render it up front so a
    # standalone `box build` doesn't die at box registration on a fresh checkout
    # (#737); idempotent when bootstrap already rendered it.
    ensure_resolved()
    plan = _build_plan(names, force=force)
    # Build (once) + cache the prebuilt mq_prometheus binary before any box that bakes
    # it in (#1065): the Go-container build replaces the in-guest cgo build that
    # overflowed the fatbox guest. Cache-hit is a no-op; the bake copies the artifact.
    if mqexporter.needs_exporter_binary(FLEET[name].role for name, _ in plan):
        mqexporter.ensure_mq_exporter_binary(cache(), DEFAULT_MQ_VERSION)
    for name, box_force in plan:
        if _rhel_base_needs_dvd(name, force=box_force):
            verify_rhel_dvd(FLEET[name].os)
    facts = probe()
    steps = _build_steps(plan, facts)  # refuses a cross-arch box before anything is built
    _ensure_component_artifacts(plan, facts)
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
    # A successful bake just imported (or re-imported) box base images; reclaim any
    # now-orphaned older ones so re-bakes don't accumulate on the root disk (#759).
    result = gc_orphaned_images_best_effort()
    if result and result.deleted:
        typer.echo(gc_summary(result), err=True)


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
            for record in (spec.manifest_hash_artifact, spec.components_record_artifact):
                path = cache_dir / record
                if path.is_file():
                    path.unlink()
                    removed.append(str(path))
        _vagrant_box_remove(name)
        removed.append(f"vagrant box '{name}'")
    return removed


# --------------------------------------------------------------------------- #
# Retired box names — the one-time rename migration (epic .github#280)          #
# --------------------------------------------------------------------------- #
def clean_retired(*, dry_run: bool = False) -> list[str]:
    """Deregister every still-registered retired box name from Vagrant; return the
    names removed (or, under dry_run, that would be).

    A retired name can never be built or booted again (bootstrap's box_meta reconcile
    forgets any guest still pinned to one), so its registration is pure dead weight."""
    registered = _registered_boxes()
    retired = [name for name in RETIRED_BOX_NAMES if name in registered]
    if not dry_run:
        for name in retired:
            _vagrant_box_remove(name)
    return retired


def clean_retired_cache(boxes_dir: Path | None = None, *, dry_run: bool = False) -> list[str]:
    """Delete the retired fat boxes' dead cache artifacts; return the paths removed.

    Their manifest hash names the box, so they can never be REUSEd under a new name.
    The retired RHEL base box's cache is NOT touched here: it is still a good image,
    and `mqlab build migrate` renames it (migrate_box_cache)."""
    cache_dir = boxes_dir if boxes_dir is not None else _boxes_cache_dir()
    removed: list[str] = []
    for name in RETIRED_BOX_NAMES:
        if name in RETIRED_BASE_ARTIFACTS:
            continue
        # Both cache schemes: pre-#103 <name>.box and the arch-suffixed <name>-<arch>.box.
        for stem in (name, *(f"{name}-{arch}" for arch in HOST_ARCHES)):
            for suffix in (".box", ".manifest-hash"):
                artifact = cache_dir / f"{stem}{suffix}"
                if artifact.is_file():
                    removed.append(str(artifact))
                    if not dry_run:
                        artifact.unlink()
    return removed


def _retired_volume_stems() -> frozenset[str]:
    """The libvirt base-volume stems of the retired names — vagrant-libvirt escapes a
    '/' in the box name as -VAGRANTSLASH- (see _current_box_volumes)."""
    return frozenset(name.replace("/", "-VAGRANTSLASH-") for name in RETIRED_BOX_NAMES)


# --------------------------------------------------------------------------- #
# Box base-image GC — reclaim orphaned libvirt base images (#759)             #
# --------------------------------------------------------------------------- #
# Every `vagrant box add --force` re-bake makes libvirt import a fresh base image
# `<stem>_vagrant_box_image_0_<ts>_box.img` into the default pool and orphans the
# previous one (~3-4 GiB each). Nothing GCs them, so they accumulate until the
# (ephemeral) root disk fills and libvirt pauses VMs with I/O errors.
#
# Which image is CURRENT (#1248): for an unversioned box, vagrant-libvirt names the
# volume after the registered box.img's mtime — `0_<File.mtime(box.img).to_i>`
# (vagrant-libvirt 0.12.2, action/handle_box_image.rb, get_volume_name). So for a
# registered box this GC keeps exactly the volume(s) its registered box.img resolves
# to, and deletes every other image of that stem: a stale one from before a rebake
# goes even when the rebaked box has not been uploaded yet. A stem with no
# registered box falls back to keeping the NEWEST image. One hard safety gate
# applies to both: an image still referenced as a <backingStore> by a live overlay
# volume is never deleted (removing the file would corrupt that VM's disk).
_VIRSH = ["virsh", "-c", "qemu:///system"]
_DEFAULT_POOL = "default"
# A vagrant-libvirt box base image: "<stem>_vagrant_box_image_0_<import-ts>_box.img".
# The `_0_<digits>_` segment is what distinguishes a re-bakeable fat/base box (whose
# import timestamp changes every bake) from a versioned cloud image, which is left alone.
_BOX_IMAGE_RE = re.compile(r"^(?P<stem>.+)_vagrant_box_image_0_(?P<ts>\d+)_box\.img$")


@dataclass(frozen=True)
class GcResult:
    """What a box-image GC pass did (or, under dry_run, would do)."""

    deleted: list[str]  # base-image volume names removed
    freed_bytes: int  # sum of their libvirt allocations
    kept: list[str]  # the current image(s) per stem (registered box's, else newest)
    skipped_in_use: list[str]  # stale images protected — a live overlay backs onto them
    dry_run: bool
    # volumes listed by vol-list but deleted before vol-dumpxml read them (#1336)
    vanished: list[str] = field(default_factory=list)


def _vagrant_boxes_dir() -> Path:
    """Vagrant's registered-box store: `$VAGRANT_HOME/boxes` (default ~/.vagrant.d)."""
    home = os.environ.get("VAGRANT_HOME") or str(Path.home() / ".vagrant.d")
    return Path(home) / "boxes"


def _current_box_volumes(boxes_dir: Path | None = None) -> dict[str, set[str]]:
    """Box stem -> the base-volume name(s) each registered unversioned box resolves to.

    Vagrant stores a box under `<name>/<version>/[<arch>/]libvirt/box.img`, with a
    '/' in the name escaped as -VAGRANTSLASH- — the same escaping vagrant-libvirt
    uses for the volume stem. Only version `0` (an unversioned local `box add`) gets
    an mtime-keyed volume name; a versioned box (the cloud base image) is skipped,
    as `_BOX_IMAGE_RE` already leaves its volumes alone."""
    root = boxes_dir if boxes_dir is not None else _vagrant_boxes_dir()
    current: dict[str, set[str]] = {}
    if not root.is_dir():
        return current
    for box_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        version_dir = box_dir / "0"
        if not version_dir.is_dir():
            continue
        for img in sorted(version_dir.rglob("box.img")):
            if img.parent.name != "libvirt":
                continue
            name = f"{box_dir.name}_vagrant_box_image_0_{int(img.stat().st_mtime)}_box.img"
            current.setdefault(box_dir.name, set()).add(name)
    return current


def _virsh_out(args: list[str]) -> str:
    """Run a read-only virsh command and return its output, FAIL-LOUD on non-zero.

    Unlike `_capture`, this never masks a virsh failure as an empty result — a
    broken `vol-list` must raise, not silently make GC a no-op (#759)."""
    code, out = _virsh_run(args)
    if code != 0:
        raise RuntimeError(f"virsh {' '.join(args)} failed (rc={code}): {out}")
    return out


def _virsh_run(args: list[str]) -> tuple[int, str]:
    """Run a virsh command; return (exit-code, joined output) for the caller to judge."""
    lines: list[str] = []
    code = SubprocessRunner().run(Command([*_VIRSH, *args]), lines.append)
    return code, "\n".join(lines)


# virsh's wording when a named volume is not in the pool: "Storage volume not found"
# (VIR_ERR_NO_STORAGE_VOL) and libvirt's "no storage vol with matching name/path".
_VOLUME_VANISHED_MARKERS = ("storage volume not found", "no storage vol with matching")


class VolumeVanishedError(RuntimeError):
    """A pool volume listed by `vol-list` was gone by the time it was read (#1336)."""


def _is_volume_vanished(output: str) -> bool:
    """True iff virsh's error output says the named volume does not exist (and only that)."""
    low = output.lower()
    return any(marker in low for marker in _VOLUME_VANISHED_MARKERS)


def _pool_volume_names(pool: str = _DEFAULT_POOL) -> list[str]:
    """Volume names in the pool, from `virsh vol-list --pool <pool>` (Name/Path table)."""
    names: list[str] = []
    for raw in _virsh_out(["vol-list", "--pool", pool]).splitlines():
        line = raw.strip()
        if not line or line.startswith("Name") or set(line) <= {"-"}:
            continue
        names.append(line.split()[0])
    return names


def _vol_detail(name: str, pool: str = _DEFAULT_POOL) -> tuple[int, str | None]:
    """(allocation-bytes, backing-image-basename-or-None) from the volume XML.

    Reads the exact `<allocation unit='bytes'>` and any `<backingStore><path>` —
    the backing path is what marks a base image as in-use by an overlay. Raises
    `VolumeVanishedError` if the volume no longer exists; any other virsh failure
    raises a plain RuntimeError (fail-loud)."""
    args = ["vol-dumpxml", name, "--pool", pool]
    code, xml = _virsh_run(args)
    if code != 0:
        msg = f"virsh {' '.join(args)} failed (rc={code}): {xml}"
        if _is_volume_vanished(xml):
            raise VolumeVanishedError(msg)
        raise RuntimeError(msg)
    alloc_match = re.search(r"<allocation unit='bytes'>(\d+)</allocation>", xml)
    alloc = int(alloc_match.group(1)) if alloc_match else 0
    backing_match = re.search(r"<backingStore>.*?<path>([^<]+)</path>", xml, re.DOTALL)
    backing = Path(backing_match.group(1).strip()).name if backing_match else None
    return alloc, backing


def _vol_delete(name: str, pool: str = _DEFAULT_POOL) -> None:
    """Delete a pool volume: idempotent on already-absent, FAIL-LOUD otherwise."""
    lines: list[str] = []
    code = SubprocessRunner().run(
        Command([*_VIRSH, "vol-delete", name, "--pool", pool]), lines.append
    )
    if code == 0:
        return
    out = "\n".join(lines)
    if "not found" in out.lower() or "no storage vol" in out.lower():
        return  # already gone — idempotent
    raise RuntimeError(f"virsh vol-delete {name} failed (rc={code}): {out}")


def gc_orphaned_images(*, dry_run: bool = False, pool: str = _DEFAULT_POOL) -> GcResult:
    """Reclaim orphaned vagrant box base images from the libvirt pool (#759, #1248).

    Per box stem, keeps the image(s) the registered box resolves to (or, with no
    registered box, the newest image; or, for a retired box name, none) and deletes the
    rest, EXCEPT any still
    referenced as a backing store by a live overlay volume. Idempotent and safe to
    run repeatedly; `dry_run` reports without deleting."""
    current = _current_box_volumes()
    retired = _retired_volume_stems()
    alloc: dict[str, int] = {}
    in_use: set[str] = set()
    names: list[str] = []
    vanished: list[str] = []
    for name in _pool_volume_names(pool):
        try:
            alloc[name], backing = _vol_detail(name, pool)
        except VolumeVanishedError:
            # Deleted between vol-list and vol-dumpxml (e.g. a build domain's console
            # log): already gone, so nothing to reclaim or protect — skip it (#1336).
            vanished.append(name)
            continue
        names.append(name)
        if backing:
            in_use.add(backing)

    by_stem: dict[str, list[tuple[int, str]]] = {}
    for name in names:
        match = _BOX_IMAGE_RE.match(name)
        if match:
            by_stem.setdefault(match["stem"], []).append((int(match["ts"]), name))

    deleted: list[str] = []
    kept: list[str] = []
    skipped: list[str] = []
    freed = 0
    for stem, images in sorted(by_stem.items()):
        images.sort()  # ascending by timestamp
        if stem in retired:
            # A retired box name (epic .github#280) is never booted or rebuilt again, so
            # every one of its images is dead — none is kept (the in-use guard still holds).
            keep: set[str] = set()
        elif stem in current:
            # Registered: keep exactly what the registered box.img resolves to. None of
            # them may exist yet (a rebake not yet uploaded) — then every image is stale.
            keep = {name for _ts, name in images if name in current[stem]}
        else:
            keep = {images[-1][1]}  # unregistered: the newest is the best guess — keep it
        for _ts, name in images:
            if name in keep:
                kept.append(name)
                continue
            if name in in_use:
                skipped.append(name)
                continue
            if not dry_run:
                _vol_delete(name, pool)
            deleted.append(name)
            freed += alloc.get(name, 0)
    return GcResult(
        deleted=deleted,
        freed_bytes=freed,
        kept=kept,
        skipped_in_use=skipped,
        dry_run=dry_run,
        vanished=vanished,
    )


def gc_orphaned_images_best_effort() -> GcResult | None:
    """GC for the automatic hooks (after a bake, after a teardown): cleanup must
    never break the primary operation, but a failure is REPORTED to stderr — never
    silently swallowed (#759). Returns the result, or None if GC failed."""
    try:
        return gc_orphaned_images()
    except Exception as exc:  # cleanup must not fail the primary op — surfaced, not hidden
        typer.echo(f"NOTICE: box base-image GC failed (non-fatal): {exc}", err=True)
        return None


def _human_bytes(num: int) -> str:
    """Human-readable size, e.g. 3345661952 -> '3.1 GiB'."""
    size = float(num)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def gc_summary(result: GcResult) -> str:
    """Render a box-image GC result for the CLI."""
    if not result.deleted and not result.skipped_in_use:
        return "box gc: no orphaned box base images — nothing to reclaim"
    verb = "would delete" if result.dry_run else "deleted"
    lines = [
        f"box gc: {verb} {len(result.deleted)} orphaned box base image(s), "
        f"{_human_bytes(result.freed_bytes)} reclaimed"
    ]
    lines += [f"  - {name}" for name in result.deleted]
    lines += [f"  ~ {name} (kept — backing a live VM disk)" for name in result.skipped_in_use]
    return "\n".join(lines)
