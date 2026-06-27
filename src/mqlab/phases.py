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

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from mqlab.lifecycle import ACTIVE, RUNNING, classify, classify_net
from mqlab.netsel import lab_net_names
from mqlab.orchestrator import CommandStep
from mqlab.paths import repo_root
from mqlab.runner import Command
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
    """Hosts of the shared observability stack (commons: obs_box + probe groups).

    Read the same way stack_members reads a stack's groups, so the obs/mon-probe
    VMs the vms phase must bring up come from one source (topology), not a literal.
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
def _vms_build_steps(stack: Stack, deps: Any) -> list[CommandStep]:  # noqa: ARG001
    """`vagrant up` the stack members plus the commons (obs/mon-probe) VMs."""
    vms = _all_vms(stack)
    cmd = Command(["vagrant", "up", *vms], cwd=repo_root() / "lab")
    return [CommandStep(f"{stack.name} vms up", cmd)]


def _vms_satisfied(stack: Stack, states: dict[str, Any]) -> bool:
    """True iff every stack member and commons VM is RUNNING in the probed states."""
    domains = states["domains"]
    return all(classify(domains, vm) == RUNNING for vm in _all_vms(stack))


# --------------------------------------------------------------------------- #
# provision phase
# --------------------------------------------------------------------------- #
def _provision_build_steps(stack: Stack, deps: Any) -> list[CommandStep]:  # noqa: ARG001
    """Run the stack's provision playbook with the #351 QM extra-vars.

    Fail loud (no silent skip) if the stack declares no provision playbook — a
    reserved stack like nativeha-ubuntu cannot be bootstrapped.
    """
    if stack.provision is None:
        msg = f"stack {stack.name!r} has no provision playbook — cannot bootstrap"
        raise ValueError(msg)
    cmd = Command(
        ["ansible-playbook", Path(stack.provision).name, *_qm_extra_vars(stack)],
        cwd=repo_root() / "ansible",
    )
    return [CommandStep(f"{stack.name} provision", cmd)]


def _provision_satisfied(stack: Stack, states: dict[str, Any]) -> bool:  # noqa: ARG001
    """True iff the stack's QM is provisioned and up (from the qm-status probe)."""
    return bool(states["qm_up"])


# --------------------------------------------------------------------------- #
# observe phase
# --------------------------------------------------------------------------- #
def _observe_build_steps(stack: Stack, deps: Any) -> list[CommandStep]:  # noqa: ARG001
    """Render the targets + dashboard, then provision the obs stack for this QM.

    Rendering is emitted as `mqlab obs ...` subprocess steps (invoked by bare
    name via $PATH) rather than rendered eagerly here, so build_steps stays pure
    — the render happens when the runner executes the step. The obs playbook runs
    with this stack's #351 QM extra-vars so the exporters target the right QM.
    """
    return [
        CommandStep("render targets", Command(["mqlab", "obs", "targets"])),
        CommandStep("render dashboard", Command(["mqlab", "obs", "dashboard"])),
        CommandStep(
            f"{stack.name} provision observability",
            Command(
                ["ansible-playbook", "site-obs.yml", *_qm_extra_vars(stack)],
                cwd=repo_root() / "ansible",
            ),
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
