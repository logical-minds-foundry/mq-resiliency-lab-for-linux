"""The OS version layer: catalog, build file and version resolver (epic .github#280).

Sits in FRONT of the host resolver (platforms.resolve): it owns lab/versions.yaml (the
only place OS version tokens are written by hand) and an optional build file, and
works out each stack's OS major and each box's generated ``<role>-<os><major>`` name.
The catalog also pins the ONE guest Python runtime (``runtime.python``) and names the
guest components each box role bakes (``roles.<role>.components``), epic .github#294.

Pure: no host probing, no subprocesses. Callers pass HostFacts. Everything fails
loudly with a VersionError whose message names the fix — there are no silent
defaults beyond the documented ones (an omitted build-file key means "the stack's
default"; an omitted ``mq_bearing`` means false).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from mqlab import instances
from mqlab.hostfacts import x86_64_v3_gap
from mqlab.paths import components_dir, versions_catalog_path

if TYPE_CHECKING:
    from pathlib import Path

    from mqlab.hostfacts import HostFacts

# Known OS families -> display name used in messages.
FAMILIES = {"ubuntu": "Ubuntu", "rhel": "RHEL"}
# Shared (commons) roles never take a stack's OS. They split by whether the box runs MQ
# (spec §2, correction .github#285):
# - INFRA_ROLES run no MQ (the infra/obs commons and the SAN targets' baked `san` box,
#   spec §4.7.1). They boot ``catalog.infra``, which is NOT support-gated.
# - INFRA_MQ_ROLES run MQ (svc-sim, app-client, mon-probe). They boot
#   ``catalog.infra_mq``, which is support-gated like a stack default.
INFRA_ROLES = ("infra", "obs", "san")
INFRA_MQ_ROLES = ("mq-client",)
SHARED_ROLES = INFRA_ROLES + INFRA_MQ_ROLES
# Host requirements the resolver knows how to gate (os.<family>.<major>.requires). An
# entry naming any other requirement is refused at load.
X86_64_V3 = "x86-64-v3"
KNOWN_REQUIREMENTS: frozenset[str] = frozenset({X86_64_V3})
HOST_ARCHES = ("aarch64", "x86_64")

_REF_RE = re.compile(r"([a-z]+):([0-9]+)")
_TOP_KEYS = ("os", "roles", "infra", "infra_mq", "stacks", "runtime")
_OS_KEYS = ("base_box", "base_box_version", "point", "iso", "arch", "requires", "ibm_support")
_ROLE_KEYS = ("bake", "mq_bearing", "components")
_RUNTIME_KEYS = ("python",)
_PYTHON_KEYS = ("version", "pbs_release", "sha256")
_PY_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_PBS_RELEASE_RE = re.compile(r"[0-9]{8}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_PBS_DOWNLOADS = "https://github.com/astral-sh/python-build-standalone/releases/download"
_STACK_KEYS = ("supported", "default")
_SUPPORT_KEYS = ("status", "source")
_BUILD_KEYS = ("os",)

_CATALOG_FIX = "fix lab/versions.yaml"
_CONFIG_FIX = (
    "edit your --config file (mqlab bootstrap <stack> --config <file>) or omit it to "
    "use the stack default (see lab/versions.yaml)"
)


class VersionError(RuntimeError):
    """A catalog, build file or version request is invalid; the message names the fix."""


@dataclass(frozen=True, order=True)
class OsRef:
    family: str
    major: int

    @classmethod
    def parse(cls, text: str) -> OsRef:
        """Parse ``<family>:<major>`` (e.g. ``rhel:9``); anything else is a VersionError."""
        match = _REF_RE.fullmatch(text)
        if match is None or match.group(1) not in FAMILIES:
            raise VersionError(
                f"bad OS reference {text!r}: expected <family>:<major> with family one of "
                f"{', '.join(FAMILIES)} (e.g. rhel:9)"
            )
        return cls(match.group(1), int(match.group(2)))

    @property
    def token(self) -> str:
        return f"{self.family}{self.major}"

    def __str__(self) -> str:
        return f"{self.family}:{self.major}"

    @property
    def label(self) -> str:
        """The human display label, e.g. ``"Ubuntu 24"`` / ``"RHEL 9"``."""
        return f"{FAMILIES[self.family]} {self.major}"


@dataclass(frozen=True)
class OsEntry:
    ref: OsRef
    base_box: str
    base_box_version: str | None
    point: str | None
    iso: str | None
    arch_pin: str | None
    requires: tuple[str, ...]
    ibm_unsupported_source: str | None


@dataclass(frozen=True)
class BoxEntry:
    name: str
    role: str
    os: OsEntry
    bake_stem: str
    mq_bearing: bool
    components: tuple[str, ...] = ()  # guest components the box bakes (epic .github#294)


@dataclass(frozen=True)
class BuildFile:
    os: OsRef | None


@dataclass(frozen=True)
class RuntimePin:
    """The ONE pinned guest Python runtime (epic .github#294 spec §5.2).

    A python-build-standalone CPython ``install_only`` build. The contract is the minor
    (``3.14``); each build pins the exact patch, release and per-arch sha256, so a
    patch bump is an edit to ``runtime.python`` in lab/versions.yaml and nowhere else.
    """

    version: str  # e.g. "3.14.8"
    pbs_release: str  # e.g. "20261003"
    sha256: dict[str, str]  # arch -> hex digest of the install_only tarball; keys == HOST_ARCHES

    @property
    def minor(self) -> str:
        """The contract version, e.g. ``"3.14"`` (also keys the guest path)."""
        major, minor, _patch = self.version.split(".")
        return f"{major}.{minor}"

    @property
    def token(self) -> str:
        """The exact build, e.g. ``"3.14.8+20261003"``."""
        return f"{self.version}+{self.pbs_release}"

    def tarball(self, arch: str) -> str:
        """The release asset name for ``arch`` (unknown arch -> VersionError)."""
        if arch not in self.sha256:
            raise VersionError(
                f"runtime.python has no build for arch {arch!r} (pinned: "
                f"{', '.join(sorted(self.sha256))}) — {_CATALOG_FIX}"
            )
        return f"cpython-{self.token}-{arch}-unknown-linux-gnu-install_only.tar.gz"

    def url(self, arch: str) -> str:
        """Where the tarball for ``arch`` is published."""
        return f"{_PBS_DOWNLOADS}/{self.pbs_release}/{self.tarball(arch)}"


@dataclass(frozen=True)
class Catalog:
    oses: dict[OsRef, OsEntry]
    roles: dict[str, dict[str, Any]]
    infra: OsRef  # shared roles that run no MQ (INFRA_ROLES); not support-gated
    infra_mq: OsRef  # shared roles that run MQ (INFRA_MQ_ROLES); support-gated
    stacks: dict[str, dict[str, Any]]
    runtime: RuntimePin

    def _entry(self, ref: OsRef) -> OsEntry:
        if ref not in self.oses:
            known = ", ".join(str(r) for r in sorted(self.oses))
            raise VersionError(
                f"OS {ref} is not in the catalog (known: {known}) — add it under os: in "
                "lab/versions.yaml"
            )
        return self.oses[ref]

    def box(self, role: str, ref: OsRef) -> BoxEntry:
        """The generated box for ``role`` on ``ref`` (name ``<role>-<os><major>``)."""
        if role not in self.roles:
            raise VersionError(
                f"unknown box role {role!r} (known: {', '.join(sorted(self.roles))}) — "
                "add it under roles: in lab/versions.yaml"
            )
        entry = self._entry(ref)
        spec = self.roles[role]
        stem = spec["bake"].get(ref.family)
        if stem is None:
            raise VersionError(
                f"role {role!r} has no bake for {ref.family} — add one under "
                f"roles.{role}.bake in lab/versions.yaml"
            )
        return BoxEntry(
            name=f"{role}-{ref.token}",
            role=role,
            os=entry,
            bake_stem=stem,
            mq_bearing=spec["mq_bearing"],
            components=spec["components"],
        )

    def _stack(self, stack: str) -> dict[str, Any]:
        if stack not in self.stacks:
            raise VersionError(
                f"unknown stack {stack!r} (known: {', '.join(sorted(self.stacks))}) — "
                "add it under stacks: in lab/versions.yaml"
            )
        return self.stacks[stack]

    def shared_os(self, role: str) -> OsRef:
        """The OS a shared (commons) role boots (#285).

        ``infra_mq`` for an MQ-bearing shared role (INFRA_MQ_ROLES), ``infra`` for the
        rest. A role that is not shared runs its stack's OS, so asking is an error.
        """
        if role in INFRA_MQ_ROLES:
            return self.infra_mq
        if role in INFRA_ROLES:
            return self.infra
        raise VersionError(
            f"role {role!r} is not a shared role (shared: {', '.join(SHARED_ROLES)}); it "
            "runs its stack's OS"
        )

    def default_os(self, stack: str) -> OsRef:
        """The stack's catalog default OS (unknown stack -> VersionError)."""
        default: OsRef = self._stack(stack)["default"]
        return default

    def stack_os(self, stack: str, build: BuildFile | None, facts: HostFacts) -> OsRef:
        """The OS major ``stack`` builds on: the build file's ``os`` or the stack default.

        Checks, in order: family, the stack's supported list, then the host gates.
        """
        spec = self._stack(stack)
        default: OsRef = spec["default"]
        ref = build.os if build is not None and build.os is not None else default
        supported: list[OsRef] = spec["supported"]
        listed = ", ".join(str(r) for r in supported)
        if ref.family != default.family:
            # Name the supported versions here too, so either refusal says what WOULD work
            # (#1391).
            article = "an" if default.family[0] in "aeiou" else "a"
            raise VersionError(
                f"{stack} is {article} {default.family} stack and supports [{listed}]; "
                f"got {ref} — {_CONFIG_FIX}"
            )
        if ref not in supported:
            raise VersionError(f"{stack} supports [{listed}]; got {ref} — {_CONFIG_FIX}")
        entry = self._entry(ref)
        if not _arch_ok(entry, facts):
            raise VersionError(
                f"{FAMILIES[ref.family]} needs an {entry.arch_pin} host; this host is "
                f"{facts.arch} — run {stack} on an {entry.arch_pin} host"
            )
        if X86_64_V3 in entry.requires and not facts.x86_64_v3:
            runnable = [r for r in supported if r != ref and host_can_run(self.oses[r], facts)]
            fix = (
                f"use os: {runnable[0]} in your --config file (mqlab bootstrap {stack} "
                "--config <file>)"
                if runnable
                else f"run {stack} on an x86-64-v3 host with KVM"
            )
            raise VersionError(f"{ref} needs an x86-64-v3 CPU; {x86_64_v3_gap(facts)} — {fix}")
        return ref

    def support_warning(self, ref: OsRef) -> str | None:
        """The warning to print when ``ref`` is IBM-unsupported (lab-only), else None."""
        source = self._entry(ref).ibm_unsupported_source
        if source is None:
            return None
        return (
            f"WARNING: IBM does not support MQ on {ref} ({source}); it is selectable for "
            "lab use only"
        )

    def all_boxes(self, facts: HostFacts, stack_roles: dict[str, set[str]]) -> list[BoxEntry]:
        """Every buildable box on this host, de-duplicated by name.

        Each shared role's box on its shared OS (Catalog.shared_os), then for each stack
        in ``stack_roles`` (stack -> the box roles its nodes use) every supported OS the
        host can run (host_can_run: RHEL is skipped on aarch64, and an OS that requires
        x86-64-v3 is skipped on a host without it).
        """
        out: dict[str, BoxEntry] = {}
        for role in SHARED_ROLES:
            entry = self.box(role, self.shared_os(role))
            out.setdefault(entry.name, entry)
        for stack, roles in stack_roles.items():
            for ref in self._stack(stack)["supported"]:
                if not host_can_run(self.oses[ref], facts):
                    continue
                for role in sorted(roles):
                    entry = self.box(role, ref)
                    out.setdefault(entry.name, entry)
        return list(out.values())


def _arch_ok(entry: OsEntry, facts: HostFacts) -> bool:
    return entry.arch_pin is None or entry.arch_pin == facts.arch


def host_can_run(entry: OsEntry, facts: HostFacts) -> bool:
    """Whether this host can build and run ``entry``: its arch pin (if any) is the
    host's, and every host requirement it declares is met."""
    return _arch_ok(entry, facts) and (X86_64_V3 not in entry.requires or facts.x86_64_v3)


# --- Topology: nodes name box ROLES; the version layer picks the concrete box --------

_TOPOLOGY_FIX = "fix lab/topology.yaml"


def node_role(node: str, spec: Any) -> str:
    """The box role a topology node declares (``box: <role>``); fail loud when absent."""
    role = spec.get("box") if isinstance(spec, dict) else None
    if not isinstance(role, str) or not role:
        raise VersionError(
            f"node {node}: declares no box role — give it `box: <role>` (a role under "
            f"roles: in lab/versions.yaml) — {_TOPOLOGY_FIX}"
        )
    return role


def node_stacks(topo: dict[str, Any]) -> dict[str, list[str]]:
    """node -> the stacks whose groups list it (declared order); shared nodes map to []."""
    groups: dict[str, Any] = topo.get("groups") or {}
    out: dict[str, list[str]] = {node: [] for node in topo.get("nodes") or {}}
    for stack, cfg in (topo.get("stacks") or {}).items():
        for group in (cfg or {}).get("groups") or []:
            for host in groups.get(group) or []:
                owners = out.setdefault(host, [])
                if stack not in owners:
                    owners.append(stack)
    return out


def stack_roles(topo: dict[str, Any], catalog: Catalog) -> dict[str, set[str]]:
    """stack -> the stack-OS box roles its member nodes declare (every topology stack).

    Shared roles run their shared OS whatever stack lists the node, so they are not a
    stack's roles. Fails loudly on a role the catalog does not
    know, so a topology typo can never silently drop a box from the fleet."""
    nodes: dict[str, Any] = topo.get("nodes") or {}
    owners = node_stacks(topo)
    out: dict[str, set[str]] = {stack: set() for stack in topo.get("stacks") or {}}
    for node, spec in nodes.items():
        role = _known_role(node, node_role(node, spec), catalog)
        if role in SHARED_ROLES:
            continue
        for stack in owners[node]:
            out[stack].add(role)
    return out


def _known_role(node: str, role: str, catalog: Catalog) -> str:
    if role not in catalog.roles:
        raise VersionError(
            f"node {node}: box role {role!r} is not in lab/versions.yaml roles (known: "
            f"{', '.join(sorted(catalog.roles))}) — {_TOPOLOGY_FIX} or add the role to "
            "lab/versions.yaml"
        )
    return role


def stack_ref(stack: str, catalog: Catalog) -> OsRef:
    """The OS a stack runs: its instance record's OS when it has one, else its default."""
    record = instances.read_record(stack)
    if record is not None:
        return record.os
    return catalog.default_os(stack)


def node_boxes(topo: dict[str, Any], catalog: Catalog) -> dict[str, BoxEntry]:
    """node -> BoxEntry for every topology node (the render covers all stacks at once).

    - Shared-role nodes (the commons nodes and the SAN targets) get their role on its
      shared OS (Catalog.shared_os: ``infra_mq`` for mq-client, else ``infra``). No
      record is consulted, so this works with no stack running.
    - Every other node gets its role on its owning stack's OS: the stack's instance
      record when one exists, else the stack default (combining every stack's record).

    No host gate here: the render must describe every node on every host (a RHEL node
    on an aarch64 host still renders, under TCG); the host gates live in
    Catalog.stack_os, applied when a stack is brought up. Fails loudly on a missing or
    unknown role, a stack node owned by zero or several stacks, a stack missing from the
    catalog, or a topology ``os_family`` that disagrees with the catalog."""
    owners = node_stacks(topo)
    stacks_cfg: dict[str, Any] = topo.get("stacks") or {}
    refs: dict[str, OsRef] = {}
    for stack in stacks_cfg:
        ref = stack_ref(stack, catalog)
        declared = (stacks_cfg[stack] or {}).get("os_family")
        if declared != ref.family:
            raise VersionError(
                f"stack {stack}: os_family {declared!r} disagrees with lab/versions.yaml "
                f"(which resolves it to {ref}) — {_TOPOLOGY_FIX}"
            )
        refs[stack] = ref
    out: dict[str, BoxEntry] = {}
    for node, spec in (topo.get("nodes") or {}).items():
        role = _known_role(node, node_role(node, spec), catalog)
        if role in SHARED_ROLES:
            out[node] = catalog.box(role, catalog.shared_os(role))
        elif len(owners[node]) != 1:
            listed = ", ".join(owners[node]) or "none"
            raise VersionError(
                f"node {node}: box role {role!r} runs its stack's OS, so the node must "
                f"belong to exactly one stack's groups (it is in: {listed}) — {_TOPOLOGY_FIX}"
            )
        else:
            out[node] = catalog.box(role, refs[owners[node][0]])
    return out


# --- Loading and eager validation ------------------------------------------------------


def _read_yaml(path: Path, what: str, fix: str) -> Any:
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise VersionError(f"{what} {path} not found — {fix}") from None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise VersionError(f"{what} {path} is not valid YAML ({exc}) — {fix}") from None


def _mapping(value: Any, where: str) -> dict[Any, Any]:
    if not isinstance(value, dict):
        raise VersionError(f"{where} must be a mapping — {_CATALOG_FIX}")
    return value


def _check_keys(raw: dict[Any, Any], allowed: tuple[str, ...], where: str, fix: str) -> None:
    unknown = sorted(str(k) for k in raw if k not in allowed)
    if unknown:
        names = ", ".join(repr(k) for k in unknown)
        raise VersionError(f"{where}: unknown key {names} (allowed: {', '.join(allowed)}) — {fix}")


def _string(raw: dict[str, Any], key: str, where: str) -> str | None:
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        raise VersionError(
            f"{where}: {key!r} must be a string (quote it); got {value!r} — {_CATALOG_FIX}"
        )
    return value


def _required(raw: dict[str, Any], key: str, where: str) -> str:
    value = _string(raw, key, where)
    if value is None:
        raise VersionError(f"{where}: missing required {key!r} — {_CATALOG_FIX}")
    return value


def _ref(value: Any, where: str) -> OsRef:
    if not isinstance(value, str):
        raise VersionError(
            f"{where}: expected <family>:<major> (e.g. rhel:9); got {value!r} — {_CATALOG_FIX}"
        )
    try:
        return OsRef.parse(value)
    except VersionError as exc:
        raise VersionError(f"{where}: {exc} — {_CATALOG_FIX}") from None


def _ibm_unsupported_source(raw: Any, where: str) -> str | None:
    if raw is None:
        return None
    where = f"{where}.ibm_support"
    support = _mapping(raw, where)
    _check_keys(support, _SUPPORT_KEYS, where, _CATALOG_FIX)
    status = support.get("status")
    if status not in ("supported", "unsupported"):
        raise VersionError(
            f"{where}: status must be 'supported' or 'unsupported'; got {status!r} — {_CATALOG_FIX}"
        )
    if status == "supported":
        _string(support, "source", where)  # optional citation, but type-checked
        return None
    return _required(support, "source", where)


def _os_entry(ref: OsRef, raw: Any) -> OsEntry:
    where = f"os.{ref.family}.{ref.major}"
    spec = _mapping(raw, where)
    _check_keys(spec, _OS_KEYS, where, _CATALOG_FIX)
    rhel = ref.family == "rhel"
    arch = _string(spec, "arch", where)
    if arch is not None and arch not in HOST_ARCHES:
        raise VersionError(
            f"{where}: arch must be one of {', '.join(HOST_ARCHES)}; got {arch!r} — {_CATALOG_FIX}"
        )
    requires = spec.get("requires", [])
    if not isinstance(requires, list) or not all(isinstance(r, str) for r in requires):
        raise VersionError(f"{where}: requires must be a list of strings — {_CATALOG_FIX}")
    unknown = sorted(set(requires) - KNOWN_REQUIREMENTS)
    if unknown:
        raise VersionError(
            f"{where}: unknown host requirement {', '.join(unknown)} (this mqlab cannot "
            f"gate it) — {_CATALOG_FIX}"
        )
    return OsEntry(
        ref=ref,
        base_box=_required(spec, "base_box", where),
        base_box_version=_string(spec, "base_box_version", where),
        point=_required(spec, "point", where) if rhel else _string(spec, "point", where),
        iso=_required(spec, "iso", where) if rhel else _string(spec, "iso", where),
        arch_pin=arch,
        requires=tuple(requires),
        ibm_unsupported_source=_ibm_unsupported_source(spec.get("ibm_support"), where),
    )


def _oses(raw: Any) -> dict[OsRef, OsEntry]:
    out: dict[OsRef, OsEntry] = {}
    for family, majors in _mapping(raw, "os").items():
        if family not in FAMILIES:
            raise VersionError(
                f"os: unknown family {family!r} (known: {', '.join(FAMILIES)}) — {_CATALOG_FIX}"
            )
        for major, spec in _mapping(majors, f"os.{family}").items():
            if isinstance(major, bool) or not isinstance(major, int):
                raise VersionError(
                    f"os.{family}: major {major!r} must be an integer — {_CATALOG_FIX}"
                )
            ref = OsRef(family, major)
            out[ref] = _os_entry(ref, spec)
    if not out:
        raise VersionError(f"os: declares no OS versions — {_CATALOG_FIX}")
    return out


def _roles(raw: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for role, raw_spec in _mapping(raw, "roles").items():
        where = f"roles.{role}"
        spec = _mapping(raw_spec, where)
        _check_keys(spec, _ROLE_KEYS, where, _CATALOG_FIX)
        bake = spec.get("bake")
        if not isinstance(bake, dict) or not bake:
            raise VersionError(
                f"{where}: bake must map at least one family to a bake stem — {_CATALOG_FIX}"
            )
        for family, stem in bake.items():
            if family not in FAMILIES or not isinstance(stem, str):
                raise VersionError(
                    f"{where}.bake: {family!r}: {stem!r} must map a known family "
                    f"({', '.join(FAMILIES)}) to a bake-stem string — {_CATALOG_FIX}"
                )
        mq_bearing = spec.get("mq_bearing", False)
        if not isinstance(mq_bearing, bool):
            raise VersionError(f"{where}: mq_bearing must be true or false — {_CATALOG_FIX}")
        out[str(role)] = {
            "bake": dict(bake),
            "mq_bearing": mq_bearing,
            "components": _components(spec.get("components", []), where),
        }
    return out


def _components(raw: Any, where: str) -> tuple[str, ...]:
    """A role's baked guest components: each must be a real ``components/<name>/`` project."""
    if not isinstance(raw, list) or not all(isinstance(c, str) for c in raw):
        raise VersionError(f"{where}: components must be a list of names — {_CATALOG_FIX}")
    if len(set(raw)) != len(raw):
        raise VersionError(f"{where}: components lists a name twice — {_CATALOG_FIX}")
    root = components_dir()
    missing = [c for c in raw if not (root / c / "pyproject.toml").is_file()]
    if missing:
        raise VersionError(
            f"{where}: unknown component {', '.join(missing)} (no {root}/<name>/pyproject.toml) "
            f"— {_CATALOG_FIX}"
        )
    return tuple(raw)


def _runtime(raw: Any) -> RuntimePin:
    runtime = _mapping(raw, "runtime")
    _check_keys(runtime, _RUNTIME_KEYS, "runtime", _CATALOG_FIX)
    where = "runtime.python"
    if "python" not in runtime:
        raise VersionError(f"runtime: missing required 'python' — {_CATALOG_FIX}")
    spec = _mapping(runtime["python"], where)
    _check_keys(spec, _PYTHON_KEYS, where, _CATALOG_FIX)
    version = _required(spec, "version", where)
    if _PY_VERSION_RE.fullmatch(version) is None:
        raise VersionError(
            f"{where}: version must be an exact <major>.<minor>.<patch> (e.g. 3.14.8); "
            f"got {version!r} — {_CATALOG_FIX}"
        )
    release = _required(spec, "pbs_release", where)
    if _PBS_RELEASE_RE.fullmatch(release) is None:
        raise VersionError(
            f"{where}: pbs_release must be the 8-digit release tag (e.g. 20261003); "
            f"got {release!r} — {_CATALOG_FIX}"
        )
    sha = _mapping(spec.get("sha256"), f"{where}.sha256")
    if set(sha) != set(HOST_ARCHES):
        raise VersionError(
            f"{where}.sha256 must pin exactly {', '.join(HOST_ARCHES)}; got "
            f"{', '.join(sorted(str(k) for k in sha)) or 'none'} — {_CATALOG_FIX}"
        )
    for arch, digest in sha.items():
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise VersionError(
                f"{where}.sha256.{arch} must be a 64-hex-digit lowercase sha256 — {_CATALOG_FIX}"
            )
    return RuntimePin(version=version, pbs_release=release, sha256=dict(sha))


def _known(ref: OsRef, oses: dict[OsRef, OsEntry], where: str) -> OsRef:
    if ref not in oses:
        raise VersionError(f"{where}: {ref} is not declared under os: — {_CATALOG_FIX}")
    return ref


def _stacks(raw: Any, oses: dict[OsRef, OsEntry]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for stack, raw_spec in _mapping(raw, "stacks").items():
        where = f"stacks.{stack}"
        spec = _mapping(raw_spec, where)
        _check_keys(spec, _STACK_KEYS, where, _CATALOG_FIX)
        listed = spec.get("supported")
        if not isinstance(listed, list) or not listed:
            raise VersionError(
                f"{where}: supported must be a non-empty list of OS refs — {_CATALOG_FIX}"
            )
        supported = [_known(_ref(item, where), oses, where) for item in listed]
        if len({r.family for r in supported}) != 1:
            raise VersionError(
                f"{where}: supported mixes OS families; a stack is one family — {_CATALOG_FIX}"
            )
        default = _known(_ref(spec.get("default"), f"{where}.default"), oses, where)
        if default not in supported:
            raise VersionError(
                f"{where}: default {default} is not in its supported list — {_CATALOG_FIX}"
            )
        source = oses[default].ibm_unsupported_source
        if source is not None:
            raise VersionError(
                f"default {default} for {stack} is IBM-unsupported ({source}); a stack "
                f"default must be IBM-supported — point stacks.{stack}.default at a "
                "supported version in lab/versions.yaml"
            )
        out[str(stack)] = {"supported": supported, "default": default}
    return out


def load_catalog(path: Path | None = None) -> Catalog:
    """Load and eagerly validate the catalog (default: lab/versions.yaml)."""
    path = path if path is not None else versions_catalog_path()
    data = _mapping(_read_yaml(path, "OS version catalog", _CATALOG_FIX), "the catalog")
    _check_keys(data, _TOP_KEYS, str(path), _CATALOG_FIX)
    missing = [k for k in _TOP_KEYS if k not in data]
    if missing:
        raise VersionError(f"{path}: missing required key {', '.join(missing)} — {_CATALOG_FIX}")
    oses = _oses(data["os"])
    roles = _roles(data["roles"])
    infra = _known(_ref(data["infra"], "infra"), oses, "infra")
    infra_mq = _known(_ref(data["infra_mq"], "infra_mq"), oses, "infra_mq")
    source = oses[infra_mq].ibm_unsupported_source
    if source is not None:
        # The support gate, applied to the shared nodes that run MQ exactly as to a stack
        # default (spec §4.1, #285). `infra` runs no MQ, so it is deliberately not gated.
        raise VersionError(
            f"infra_mq {infra_mq} is IBM-unsupported ({source}); the shared nodes that run "
            "MQ must be on an IBM-supported version — point infra_mq at a supported "
            "version in lab/versions.yaml"
        )
    catalog = Catalog(
        oses=oses,
        roles=roles,
        infra=infra,
        infra_mq=infra_mq,
        stacks=_stacks(data["stacks"], oses),
        runtime=_runtime(data["runtime"]),
    )
    for role in SHARED_ROLES:
        catalog.box(role, catalog.shared_os(role))  # every shared role bakes on its OS
    return catalog


def load_build_file(path: Path) -> BuildFile:
    """Load a ``--config`` build file. Omitted keys mean the stack default."""
    data = _read_yaml(path, "build file", f"pass an existing file to {_CONFIG_FIX}")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise VersionError(
            f"build file {path} must be a mapping (e.g. 'os: rhel:9') — {_CONFIG_FIX}"
        )
    _check_keys(data, _BUILD_KEYS, f"build file {path}", _CONFIG_FIX)
    if "os" not in data:
        return BuildFile(os=None)
    value = data["os"]
    if not isinstance(value, str):
        raise VersionError(
            f"build file {path}: os must be <family>:<major> (e.g. rhel:9); got {value!r} — "
            f"{_CONFIG_FIX}"
        )
    try:
        return BuildFile(os=OsRef.parse(value))
    except VersionError as exc:
        raise VersionError(f"build file {path}: {exc} — {_CONFIG_FIX}") from None
