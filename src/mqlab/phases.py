"""The phase registry — the four idempotent bring-up phases of `bootstrap <stack>`.

`#350` replaces the old per-command, setup-based CLI with a `bootstrap <stack>`
that runs four phases in order — net -> vms -> provision -> observe — each gated
by a live "satisfied?" probe so a re-run resumes from the first incomplete phase.

This module is **pure and fully unit-testable**: it touches no subprocess, no
virsh/vagrant/prometheus. Each `Phase.satisfied(stack, states)` READS an
already-gathered `states` dict (a snapshot of the world) and returns a bool;
`Phase.build_steps(stack, deps)` emits the concrete `CommandStep`s for that
phase, parameterized by the Stack. Task 4's sequencer (`bootstrap` in cli.py)
owns the live state-gathering (`_probe_all`, which actually runs `virsh`, the
stack's `qm-status` verb and the Prometheus check) and feeds the resulting dict
to `first_unsatisfied`. Keeping the seam here means the registry is exercised
with plain dicts at 100% branch coverage — no subprocess mocking.

The `states` dict shape (the contract Task 4's `_probe_all` fills):

    {
      "nets":    {net_name: virsh-net state},   # for classify_net (net phase)
      "domains": {"lab_<guest>": virsh state},   # for classify     (vms phase)
      "qm_up":   bool,   # stack QM provisioned + running   (provision phase)
      "observe": bool,   # this stack's exporter responds +
                         #   targets registered             (observe phase)
    }
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from mqlab.lifecycle import ACTIVE, RUNNING, classify, classify_net
from mqlab.netsel import lab_net_names
from mqlab.orchestrator import CommandStep
from mqlab.paths import repo_root
from mqlab.relay import RELAY_UNITS, WORKSTATION_GRAFANA_URL
from mqlab.runner import Command
from mqlab.scrape import mq_exporters_path
from mqlab.stacks import Stack, stack_members

if TYPE_CHECKING:
    from collections.abc import Callable

# virsh, run as the lab host operator (mirrors cli.py's _VIRSH).
_VIRSH = ["virsh", "-c", "qemu:///system"]


@dataclass(frozen=True)
class Phase:
    """One bring-up phase: a name, a step emitter, a satisfied-probe, and the
    prerequisite kinds it needs.

    build_steps: emits the CommandSteps that ADVANCE this phase, for a stack.
    satisfied:   reads a gathered `states` dict and returns True iff this phase
                 needs no work (the gate that makes a re-run resume from the
                 first incomplete phase).
    ensure:      data-only tuple naming the fresh-volume prerequisites this phase
                 needs before its steps can run (#350 Task 5) — e.g. ("boxes",
                 "mq") for vms, ("galaxy", "mq", "pki") for provision. This
                 module stays PURE: the names are plain strings; the sequencer in
                 cli.py owns the real I/O (tarball fetch, galaxy/PKI plays) and
                 dispatches on these names. Keeping the declaration here means
                 each phase still expresses its prereqs declaratively, in one
                 place, alongside its steps.
    """

    name: str
    build_steps: Callable[[Stack, Any], list[CommandStep]]
    satisfied: Callable[[Stack, dict[str, Any]], bool]
    ensure: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Topology helpers (pure reads — no subprocess).
# --------------------------------------------------------------------------- #
def _topology() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return data


def _commons_members() -> list[str]:
    """Hosts of the shared commons set (commons: groups — obs_box + probe + svc + app
    + infra).

    Read the same way stack_members reads a stack's groups, so the commons VMs the
    vms phase must bring up come from one source (topology), not a literal.
    """
    data = _topology()
    all_groups: dict[str, list[str]] = {
        g: list(hosts) for g, hosts in (data.get("groups") or {}).items()
    }
    commons_groups = list((data.get("commons") or {}).get("groups") or [])
    members: list[str] = []
    for g in commons_groups:
        for host in all_groups.get(g, []):
            if host not in members:
                members.append(host)
    return members


# Commons groups that run no MQ — infrastructure-only nodes (DNS + core services,
# #606). They still boot in the vms phase (via _commons_members / all_vms) but carry
# no MQ SDK, so the MQ-media (tarball) enumeration in cli must exclude their hosts:
# their platform (infra-ubuntu2404) has no MQ tarball arch mapping by design (#634).
_NON_MQ_COMMONS_GROUPS = frozenset({"infra"})


def _non_mq_commons_hosts() -> set[str]:
    """Hosts of the commons groups that run no MQ (_NON_MQ_COMMONS_GROUPS), read from
    topology the same way _commons_members reads a group's hosts. The MQ-media
    enumeration excludes these — infra is a DNS/core-services box, not an MQ node."""
    all_groups: dict[str, list[str]] = _topology().get("groups") or {}
    return {host for g in _NON_MQ_COMMONS_GROUPS for host in (all_groups.get(g) or [])}


def all_vms(stack: Stack) -> list[str]:
    """Every VM bootstrap must bring up for this stack: members + commons, deduped.

    Public so the sequencer (cli.py) can ensure the same VM set's boxes before the
    vms phase runs them — one source of truth for "the stack's VMs", not a literal.
    """
    members = stack_members(stack.name) or []
    vms = list(members)
    for host in _commons_members():
        if host not in vms:
            vms.append(host)
    return vms


# Back-compat alias for the in-module callers below (kept private at the call sites).
_all_vms = all_vms


def _qm_extra_vars(stack: Stack) -> list[str]:
    """The #351 QM extra-vars for a stack's provision/obs plays, sourced from stack.qm.

    Names DERIVE from the stack's short token (qm.qm_app/qm_svc/chl_to_svc/
    chl_to_app) — no retired QM literal appears here.
    """
    qm = stack.qm
    return [
        "-e",
        f"qm_app={qm.qm_app}",
        "-e",
        f"qm_svc={qm.qm_svc}",
        "-e",
        f"chl_to_svc={qm.chl_to_svc}",
        "-e",
        f"chl_to_app={qm.chl_to_app}",
        "-e",
        f"svc_req_queue={qm.req_queue}",
    ]


# --------------------------------------------------------------------------- #
# net phase
# --------------------------------------------------------------------------- #
def _net_build_steps(stack: Stack, deps: Any) -> list[CommandStep]:  # noqa: ARG001
    """Define, autostart, and start every lab network the stack needs.

    The lab's networks are global (lab/networks/net-*.xml), shared across stacks;
    a stack needs them all up. Each gets the same define/autostart/start triple
    the old `net create` path used, built from virsh primitives.
    """
    steps: list[CommandStep] = []
    for net in lab_net_names():
        xml = repo_root() / "lab" / "networks" / f"{net}.xml"
        steps.append(CommandStep(f"{net} define", Command([*_VIRSH, "net-define", str(xml)])))
        steps.append(CommandStep(f"{net} autostart", Command([*_VIRSH, "net-autostart", net])))
        steps.append(CommandStep(f"{net} start", Command([*_VIRSH, "net-start", net])))
    return steps


def _net_satisfied(stack: Stack, states: dict[str, Any]) -> bool:  # noqa: ARG001
    """True iff every required lab network is ACTIVE in the probed net states."""
    net_states = states["nets"]
    return all(classify_net(net_states, net) == ACTIVE for net in lab_net_names())


# --------------------------------------------------------------------------- #
# vms phase
# --------------------------------------------------------------------------- #
# Default vms-phase boot batch size when the topology omits `boot_batch` (#638).
# Deliberately conservative: booting all 12 fat boxes at once (multi-GB image copies
# + the RDQM drbdpool vdb creates) spikes host disk I/O hard enough to time a heavy
# guest (infra-client, 4 NICs) out on SSH ('inaccessible') while its siblings come up.
_DEFAULT_BOOT_BATCH = 4


def _boot_batch() -> int:
    """The vms-phase boot batch size N — topology `boot_batch`, default 4 (#638).

    vagrant-libvirt has no native "N at a time" (all-parallel by default, or fully
    serial via --no-parallel), so the vms phase chunks the ordered VM list and issues
    one `vagrant up <batch>` per group of at most N. This is the deliberate
    speed<->reliability dial: **smaller N = fewer concurrent boots = more reliable but
    a bit slower**; larger N = faster but reintroduces the disk-I/O contention that
    flaked the all-at-once boot. Ordering is always preserved (batches are contiguous
    chunks of the ordered list), so the HADR bring-up order still holds.

    Fails loud (no silent clamp) on a non-positive or non-integer value — a garbled
    dial would otherwise silently drop VMs from the boot (a negative step yields empty
    chunks) or reorder them, which is exactly the failure this knob exists to prevent.
    """
    value = _topology().get("boot_batch", _DEFAULT_BOOT_BATCH)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        msg = f"topology boot_batch must be a positive integer, got {value!r}"
        raise ValueError(msg)
    return value


def _batch_guests(guests: list[str], size: int) -> list[list[str]]:
    """Split an ordered guest list into contiguous batches of at most `size`.

    Batches are contiguous slices in the original order — never reordered — so the
    HADR bring-up order (SANs first, site-A before site-B) is preserved across the
    per-batch `vagrant up` calls. Emits ceil(len/size) batches: a list shorter than one
    batch yields a single batch, size==1 yields one guest per batch (fully serial), and
    size>=len yields a single batch (the old all-at-once behaviour).
    """
    return [guests[i : i + size] for i in range(0, len(guests), size)]


def _vms_build_steps(stack: Stack, deps: Any) -> list[CommandStep]:  # noqa: ARG001
    """`vagrant up` the stack members plus the commons (obs/mon-probe) VMs, in
    contiguous batches of at most `boot_batch` (#638).

    One `vagrant up <batch>` per group of <= N, over the topology's ordered VM list, so
    the fat boxes don't all copy their multi-GB images at once. See `_boot_batch` for
    the speed<->reliability tradeoff and `_batch_guests` for the ordering guarantee.
    """
    vms = _all_vms(stack)
    batches = _batch_guests(vms, _boot_batch())
    steps: list[CommandStep] = []
    for index, batch in enumerate(batches, start=1):
        cmd = Command(["vagrant", "up", *batch], cwd=repo_root() / "lab")
        label = (
            f"{stack.name} vms up"
            if len(batches) == 1
            else f"{stack.name} vms up [{index}/{len(batches)}]"
        )
        steps.append(CommandStep(label, cmd))
    return steps


def _vms_satisfied(stack: Stack, states: dict[str, Any]) -> bool:
    """True iff every stack member and commons VM is RUNNING in the probed states."""
    domains = states["domains"]
    return all(classify(domains, vm) == RUNNING for vm in _all_vms(stack))


# --------------------------------------------------------------------------- #
# provision phase
# --------------------------------------------------------------------------- #
def _provision_build_steps(stack: Stack, deps: Any) -> list[CommandStep]:  # noqa: ARG001
    """Bring up DNS, then run the stack's provision playbook with the #351 QM
    extra-vars.

    DNS goes first (#478): render the zones from topology, serve them on the infra
    nodes (bind-dns), and point every guest's resolver at them (host-resolver), so
    the app-requester's runtime reverse lookups (#454) resolve from the outset
    rather than paying the ~10s getnameinfo tax until observe. site-dns.yml is
    limited to this stack's VM set (members + commons, which carries the infra
    nodes). Fail loud (no silent skip) if the stack declares no provision
    playbook — a reserved stack like nativeha-ubuntu cannot be bootstrapped.
    """
    if stack.provision is None:
        msg = f"stack {stack.name!r} has no provision playbook — cannot bootstrap"
        raise ValueError(msg)
    ansible = repo_root() / "ansible"
    nodes = ",".join(all_vms(stack))
    cmd = Command(
        ["ansible-playbook", Path(stack.provision).name, *_qm_extra_vars(stack)],
        cwd=ansible,
    )
    return [
        CommandStep("render dns zones", Command(["mqlab", "dns", "render"])),
        CommandStep(
            f"{stack.name} provision dns",
            Command(["ansible-playbook", "site-dns.yml", "--limit", nodes], cwd=ansible),
        ),
        CommandStep(f"{stack.name} provision", cmd),
    ]


def _provision_satisfied(stack: Stack, states: dict[str, Any]) -> bool:  # noqa: ARG001
    """True iff the stack's QM is provisioned and up (from the qm-status probe)."""
    return bool(states["qm_up"])


# --------------------------------------------------------------------------- #
# observe phase
# --------------------------------------------------------------------------- #
def _host_mqlab() -> str:
    """Absolute path to a host-runnable mqlab console script for the host services.

    The host-net-state systemd service runs as root with a minimal PATH (no `uv`,
    no mqlab on PATH), so it must call mqlab by absolute path. Use the console
    script beside the interpreter driving THIS bootstrap — host-runnable by
    definition, and the same venv `uv sync` keeps current at bootstrap (#776).

    (History: this originally dodged a corrupted `.venv/bin/mqlab` whose shebang
    was rewritten to the container path `/workspace/.venv/bin/python`, so the
    service died status=127 (#398). That corruption is fixed at the source —
    vergil-project/vergil-tooling#2473/#2495 give the container its own isolated
    venv so it never rewrites the host `.venv` — so the interpreter-sibling mqlab
    is now simply the correct host venv's console script, not a workaround.)
    """
    return str(Path(sys.executable).resolve().parent / "mqlab")


def _observe_build_steps(stack: Stack, deps: Any) -> list[CommandStep]:  # noqa: ARG001
    """Render the targets + dashboard, then provision the obs stack for this QM.

    Rendering is emitted as `mqlab obs ...` subprocess steps (invoked by bare
    name via $PATH) rather than rendered eagerly here, so build_steps stays pure
    — the render happens when the runner executes the step. The obs playbook runs
    with this stack's #351 QM extra-vars so the exporters target the right QM.

    Two playbooks (#381): site-obs.yml stands up the obs box (prometheus/grafana)
    + the probe's MQ exporters; observability.yml instruments the cluster NODES
    with node-exporter + the per-mechanism state collector (cluster-state /
    nativeha-state / rdqm-state) whose textfiles feed the `cluster_*` cockpit
    metrics. observability.yml is `hosts: all`, so it is --limited to THIS stack's
    VMs (else it targets other, down stacks' nodes); net-reach needs reach-peers
    rendered first.
    """
    ansible = repo_root() / "ansible"
    nodes = ",".join(all_vms(stack))
    return [
        # --stack scopes the exporter deployment list to THIS stack, so observing one
        # stack never stands up (crash-looping) exporter units for un-provisioned ones
        # (#503); the node/ibmmq scrape-target renders stay full-topology.
        CommandStep("render targets", Command(["mqlab", "obs", "targets", "--stack", stack.name])),
        CommandStep("render dashboard", Command(["mqlab", "obs", "dashboard"])),
        CommandStep("render reach-peers", Command(["mqlab", "obs", "reach-peers"])),
        # site-obs.yml loops the mq-exporter role over the `mq_exporters` extra-var, a
        # JSON list rendered from topology by the `render targets` step above into
        # mq_exporters_path(). It is a list, so it must be passed as a file (`-e @path`),
        # not inline like the #351 QM vars. The `obs up` call site (cli.py) passes the
        # same file via _obs_exporter_args(); #423 wired only that one, so the observe
        # phase dereferenced an undefined `mq_exporters` and mon-probe failed (#434).
        CommandStep(
            f"{stack.name} provision observability",
            Command(
                [
                    "ansible-playbook",
                    "site-obs.yml",
                    "-e",
                    f"@{mq_exporters_path()}",
                    *_qm_extra_vars(stack),
                ],
                cwd=ansible,
            ),
        ),
        CommandStep(
            f"{stack.name} instrument nodes",
            Command(
                ["ansible-playbook", "observability.yml", "--limit", nodes, *_qm_extra_vars(stack)],
                cwd=ansible,
            ),
        ),
        # Instrument the libvirt HOST (the Vergil VM, connection=local): node-exporter
        # exposes the virbr-* bridge byte counters that feed the per-net throughput
        # panels, and host-net-state emits lab_network_state/health. Without this the
        # network rx/tx + state + health graphs have no data (#383). `mqlab_bin` is the
        # host-runnable mqlab the net-state service must call by absolute path (#398).
        CommandStep(
            f"{stack.name} instrument host",
            Command(
                [
                    "ansible-playbook",
                    "host-obs.yml",
                    "-c",
                    "local",
                    "-i",
                    "localhost,",
                    "-e",
                    f"mqlab_bin={_host_mqlab()}",
                ],
                cwd=ansible,
            ),
        ),
        # site-obs.yml bounced grafana, which wedges the held downstream of the
        # vergil port-forward relay (#264) — heal it so the workstation can browse
        # grafana, then fail loud if that endpoint isn't actually serving.
        CommandStep(
            "heal grafana port-forward relay",
            Command(["sudo", "systemctl", "restart", *RELAY_UNITS]),
        ),
        CommandStep(
            "verify grafana reachable (workstation forward)",
            Command(["curl", "-fsS", "-m", "5", f"{WORKSTATION_GRAFANA_URL}/api/health"]),
        ),
    ]


def _observe_satisfied(stack: Stack, states: dict[str, Any]) -> bool:  # noqa: ARG001
    """True iff this stack's exporter responds and its targets are registered."""
    return bool(states["observe"])


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
# Per-phase prerequisite kinds (#350 Task 5), declared as plain data so phases.py
# stays import-pure. The sequencer (cli.py _ensure_prereqs_for_stack) dispatches
# each name to its real ensure before the phase's steps run, and only for phases
# actually selected — so `bootstrap --only observe` ensures only exporter PKI.
PHASES: list[Phase] = [
    Phase("net", _net_build_steps, _net_satisfied),
    Phase("vms", _vms_build_steps, _vms_satisfied, ensure=("boxes", "mq")),
    Phase(
        "provision",
        _provision_build_steps,
        _provision_satisfied,
        ensure=("galaxy", "mq", "pki"),
    ),
    Phase("observe", _observe_build_steps, _observe_satisfied, ensure=("pki",)),
]


def first_unsatisfied(stack: Stack, states: dict[str, Any]) -> int | None:
    """Index of the first phase whose satisfied-probe is False, or None if all pass.

    This is what Task 4's sequencer uses to resume a re-run from the first
    incomplete phase.
    """
    for index, phase in enumerate(PHASES):
        if not phase.satisfied(stack, states):
            return index
    return None


def build_states(
    *,
    nets: dict[str, str],
    domains: dict[str, str],
    qm_up: bool,
    observe: bool,
) -> dict[str, Any]:
    """Assemble a states dict in the canonical shape the satisfied-probes consume.

    Exported so Task 4's `_probe_all` and tests build the same structure from one
    place rather than duplicating the literal dict layout.
    """
    return {"nets": nets, "domains": domains, "qm_up": qm_up, "observe": observe}
