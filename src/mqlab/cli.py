"""mqlab — the operator orchestrator CLI (Typer + Rich). A thin veneer (spec §2)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console

from mqlab.fleet import parse_domain_states
from mqlab.guestsel import resolve_guests
from mqlab.inventory import inventory_path, lab_inventory
from mqlab.lifecycle import ABSENT, ACTIVE, INACTIVE, OFF, RUNNING, classify, classify_net
from mqlab.netsel import parse_net_states, resolve_nets
from mqlab.orchestrator import CommandStep, StepFailedError, run_steps
from mqlab.paths import lab_network, lab_script, repo_root
from mqlab.pauser import NoTTYError, TTYPauser
from mqlab.render import Renderer
from mqlab.runner import Command, SubprocessRunner
from mqlab.setups import lab_setups, setup_members
from mqlab.transcript import Transcript, transcript_path
from mqlab.vmstatus import vm_status_core

if TYPE_CHECKING:
    from collections.abc import Callable

    from mqlab.orchestrator import Pauser
    from mqlab.runner import CommandRunner


@dataclass
class Deps:
    """The injectable dependencies of a run (real impls in build_deps)."""

    runner: CommandRunner
    renderer: Renderer
    transcript: Transcript
    pauser: Pauser


def build_deps(verb: str, timestamp: str) -> Deps:
    return Deps(
        runner=SubprocessRunner(),
        renderer=Renderer(Console()),
        transcript=Transcript(transcript_path(verb, timestamp)),
        pauser=TTYPauser(),
    )


def _execute(verb: str, steps: list[CommandStep], *, step_mode: bool) -> None:
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps(verb, timestamp)
    try:
        run_steps(
            steps,
            runner=deps.runner,
            renderer=deps.renderer,
            transcript=deps.transcript,
            step_mode=step_mode,
            pauser=deps.pauser,
        )
    except NoTTYError as exc:
        deps.renderer.error(str(exc))
        raise typer.Exit(code=2) from exc
    except StepFailedError as exc:
        raise typer.Exit(code=exc.exit_code) from exc
    finally:
        deps.transcript.close()


def _resolve_or_exit(pattern: str, resolver: Callable[[str], list[str]], noun: str) -> list[str]:
    """Resolve a selection pattern to names, or exit 2 with a clear message (#75)."""
    try:
        names = resolver(pattern)
    except re.error as exc:
        typer.echo(f"invalid pattern /{pattern}/: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if not names:
        typer.echo(f"no lab {noun} matches /{pattern}/", err=True)
        raise typer.Exit(code=2)
    return names


_NET_LIST = Command(["virsh", "-c", "qemu:///system", "net-list", "--all"])  # noqa: S607 - virsh on PATH (lab)


def _net_status_steps() -> list[CommandStep]:
    # No script exists; mqlab drives virsh directly. virsh's own output is already
    # tabular, so this is a plain treatment-A pass-through — no re-rendering (#70).
    return [CommandStep("networks status", _NET_LIST)]


def _net_show_steps(nets: list[str]) -> list[CommandStep]:
    # Multi-step verb (#75): three virsh reads per selected net — config and who is
    # attached. Pass-through; net-dhcp-leases exits 0 even on no-DHCP nets.
    base = ["virsh", "-c", "qemu:///system"]
    reads = [("info", "net-info"), ("config", "net-dumpxml"), ("leases", "net-dhcp-leases")]
    return [
        CommandStep(f"{net} {label}", Command([*base, verb, net]))  # noqa: S607
        for net in nets
        for label, verb in reads
    ]


app = typer.Typer(help="mqlab — operator orchestrator for the MQ cluster lab", no_args_is_help=True)
net_app = typer.Typer(help="libvirt lab networks", no_args_is_help=True)
app.add_typer(net_app, name="net")

_StepFlag = Annotated[bool, typer.Option("--step", help="pause after each step to inspect the lab")]
_Pattern = Annotated[str, typer.Argument(help="name, regex, or 'all'")]


@net_app.command("create")
def net_create(pattern: _Pattern, step: _StepFlag = False) -> None:
    """Define + autostart the selected networks (skips any already defined)."""
    nets = _resolve_or_exit(pattern, resolve_nets, "network")
    _execute_stateful(
        "net-create", nets, _net_plan_create, step_mode=step, prober=_probe_net_states
    )


@net_app.command("up")
def net_up(pattern: _Pattern, step: _StepFlag = False) -> None:
    """Activate the selected (already-defined) networks (skips any already up)."""
    nets = _resolve_or_exit(pattern, resolve_nets, "network")
    _execute_stateful("net-up", nets, _net_plan_up, step_mode=step, prober=_probe_net_states)


@net_app.command("down")
def net_down(pattern: _Pattern, step: _StepFlag = False) -> None:
    """Deactivate the selected networks — virsh net-destroy (skips any already down)."""
    nets = _resolve_or_exit(pattern, resolve_nets, "network")
    _execute_stateful("net-down", nets, _net_plan_down, step_mode=step, prober=_probe_net_states)


@net_app.command("destroy")
def net_destroy(pattern: _Pattern, step: _StepFlag = False) -> None:
    """Remove the selected networks — deactivates active ones first (skips absent)."""
    nets = _resolve_or_exit(pattern, resolve_nets, "network")
    _execute_stateful(
        "net-destroy", nets, _net_plan_destroy, step_mode=step, prober=_probe_net_states
    )


@net_app.command("status")
def net_status() -> None:
    """Show which lab networks are defined / active / autostart."""
    _execute("net-status", _net_status_steps(), step_mode=False)


@net_app.command("show")
def net_show(pattern: _Pattern, step: _StepFlag = False) -> None:
    """Show config + DHCP leases for the selected lab networks (name, regex, or 'all')."""
    nets = _resolve_or_exit(pattern, resolve_nets, "network")
    _execute("net-show", _net_show_steps(nets), step_mode=step)


vm_app = typer.Typer(help="lab guest VMs (vagrant + virsh)", no_args_is_help=True)
app.add_typer(vm_app, name="vm")

obs_app = typer.Typer(help="observability stack (Prometheus + Grafana)", no_args_is_help=True)
app.add_typer(obs_app, name="obs")


@obs_app.command("targets")
def obs_targets() -> None:
    """Render build/prometheus/targets/node.json from topology and echo it."""
    from mqlab.scrape import lab_scrape_targets, scrape_targets_path

    deps = build_deps("obs-targets", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        text = lab_scrape_targets()
        path = scrape_targets_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        deps.renderer.command(f"render -> {path}")
        for line in text.splitlines():
            deps.renderer.output(line)
            deps.transcript.write(line)
    finally:
        deps.transcript.close()


@obs_app.command("dashboard")
def obs_dashboard() -> None:
    """Render build/grafana/dashboards/lab-status.json from topology and echo it."""
    from mqlab.dashboard import dashboard_path, lab_dashboard

    deps = build_deps("obs-dashboard", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        text = lab_dashboard()
        path = dashboard_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        deps.renderer.command(f"render -> {path}")
        deps.transcript.write(f"render -> {path}")
    finally:
        deps.transcript.close()


@obs_app.command("net-state")
def obs_net_state() -> None:
    """Emit lab_network_state textfile metrics from `virsh net-list --all` (run on the host)."""
    from mqlab.netsel import lab_net_names, parse_net_states
    from mqlab.netstate import render_net_state_prom

    deps = build_deps("obs-net-state", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    captured: list[str] = []
    try:
        deps.runner.run(Command([*_VIRSH, "net-list", "--all"]), captured.append)  # noqa: S607
        states = parse_net_states("\n".join(captured))
        typer.echo(render_net_state_prom(lab_net_names(), states), nl=False)
    finally:
        deps.transcript.close()


@obs_app.command("reach-peers")
def obs_reach_peers() -> None:
    """Render build/obs/reach-peers.json (host -> net -> peers) from topology."""
    import json as _json

    import yaml as _yaml

    from mqlab.netstate import net_peers

    deps = build_deps("obs-reach-peers", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        topo = _yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
        path = repo_root() / "build" / "obs" / "reach-peers.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(net_peers(topo), indent=2) + "\n")
        deps.renderer.command(f"render -> {path}")
        deps.transcript.write(f"render -> {path}")
    finally:
        deps.transcript.close()


GRAFANA_URL = "http://10.50.0.2:3000"  # obs net-mgmt IP : Grafana port


def _obs_up_steps() -> list[CommandStep]:
    from mqlab.dashboard import dashboard_path, lab_dashboard
    from mqlab.inventory import inventory_path, lab_inventory
    from mqlab.scrape import lab_scrape_targets, scrape_targets_path

    # Render all three artifacts eagerly when the steps are built: the Prometheus
    # scrape targets, the Ansible inventory the provision step needs (mirrors
    # dr-provision.sh), and the Grafana dashboard the grafana role deploys.
    targets = scrape_targets_path()
    targets.parent.mkdir(parents=True, exist_ok=True)
    targets.write_text(lab_scrape_targets())

    inv = inventory_path()
    inv.parent.mkdir(parents=True, exist_ok=True)
    inv.write_text(lab_inventory())

    dash = dashboard_path()
    dash.parent.mkdir(parents=True, exist_ok=True)
    dash.write_text(lab_dashboard())

    return [
        CommandStep(
            "render targets + inventory + dashboard",
            Command(["echo", f"rendered -> {targets}, {inv}, {dash}"]),  # noqa: S607
        ),
        CommandStep(
            "monitoring create",
            Command(["vagrant", "up", "obs", "mon-probe"], cwd=repo_root() / "lab"),  # noqa: S607
        ),
        CommandStep(
            "provision monitoring",
            # bare filename, run from ansible/ so ansible.cfg (inventory path) is
            # picked up — matches dr-provision.sh.
            Command(
                ["uv", "run", "ansible-playbook", "site-obs.yml"],  # noqa: S607
                cwd=repo_root() / "ansible",
            ),
        ),
        CommandStep(
            "provision host collector",
            # the Vergil VM (libvirt host) — node_exporter + the lab_network_state
            # timer — via a connection=local play.
            Command(
                [
                    "uv",
                    "run",
                    "ansible-playbook",
                    "host-obs.yml",
                    "-c",
                    "local",
                    "-i",
                    "localhost,",
                ],  # noqa: S607
                cwd=repo_root() / "ansible",
            ),
        ),
    ]


@obs_app.command("up")
def obs_up(step: _StepFlag = False) -> None:
    """Render targets, create the monitoring pair, and provision Prometheus + Grafana."""
    _execute("obs-up", _obs_up_steps(), step_mode=step)


@obs_app.command("status")
def obs_status() -> None:
    """Show the monitoring pair's state (topology joined with live virsh state)."""
    guests = _resolve_or_exit("monitoring", resolve_guests, "guest")
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps("obs-status", timestamp)
    try:
        code = vm_status_core(deps.runner, deps.renderer, deps.transcript, guests=guests)
    finally:
        deps.transcript.close()
    if code != 0:
        raise typer.Exit(code=code)


@obs_app.command("open")
def obs_open() -> None:
    """Print the Grafana URL and how to reach it from your workstation."""
    typer.echo(f"Grafana:   {GRAFANA_URL}  (directly reachable inside the Vergil VM)")
    typer.echo(f"Dashboard: {GRAFANA_URL}/d/lab-fleet-node  (Fleet — Node Health)")
    typer.echo("")
    typer.echo("obs is a guest *inside* the Vergil VM, so forward a port through the VM.")
    typer.echo("On your workstation:")
    typer.echo("  1. limactl list   # find the instance whose DIR is this repo, note its name")
    typer.echo("  2. ssh -F ~/.lima/<instance>/ssh.config -L 3000:10.50.0.2:3000 <host-alias>")
    typer.echo("     # the <host-alias> is the ssh.config 'Host' line — Lima turns the")
    typer.echo("     # instance's dots into hyphens (lima-vergil-user-...-mq-cluster-tooling)")
    typer.echo("  3. browse http://localhost:3000/d/lab-fleet-node   (admin / admin)")


_VIRSH = ["virsh", "-c", "qemu:///system"]


def _create_step(g: str) -> CommandStep:
    # Create + provision via Vagrant — the one verb that needs Vagrant (#96).
    cmd = Command(["vagrant", "up", g], cwd=repo_root() / "lab")  # noqa: S607
    return CommandStep(f"{g} create", cmd)


def _start_step(g: str) -> CommandStep:
    return CommandStep(f"{g} start", Command([*_VIRSH, "start", f"lab_{g}"]))  # noqa: S607


def _shutdown_step(g: str) -> CommandStep:
    return CommandStep(f"{g} shutdown", Command([*_VIRSH, "shutdown", f"lab_{g}"]))  # noqa: S607


def _forceoff_step(g: str) -> CommandStep:
    return CommandStep(f"{g} force-off", Command([*_VIRSH, "destroy", f"lab_{g}"]))  # noqa: S607


def _undefine_step(g: str) -> CommandStep:
    # Remove the domain + per-guest overlay disk + UEFI nvram (base box untouched).
    cmd = Command([*_VIRSH, "undefine", f"lab_{g}", "--remove-all-storage", "--nvram"])  # noqa: S607
    return CommandStep(f"{g} undefine", cmd)


def _net_define_step(net: str) -> CommandStep:
    # Create the network from its declarative XML (the one verb that needs the file).
    cmd = Command([*_VIRSH, "net-define", str(lab_network(net))])  # noqa: S607
    return CommandStep(f"{net} define", cmd)


def _net_autostart_step(net: str) -> CommandStep:
    return CommandStep(f"{net} autostart", Command([*_VIRSH, "net-autostart", net]))  # noqa: S607


def _net_start_step(net: str) -> CommandStep:
    return CommandStep(f"{net} start", Command([*_VIRSH, "net-start", net]))  # noqa: S607


def _net_deactivate_step(net: str) -> CommandStep:
    # virsh confusingly names *deactivate* `net-destroy` — this is mqlab `net down`.
    return CommandStep(f"{net} deactivate", Command([*_VIRSH, "net-destroy", net]))  # noqa: S607


def _net_undefine_step(net: str) -> CommandStep:
    return CommandStep(f"{net} undefine", Command([*_VIRSH, "net-undefine", net]))  # noqa: S607


# State-aware planners (#99): given the live state, act only where needed and emit
# advisory notes for the rest. Idempotency = looking before you leap.
def _plan_create(guests: list[str], states: dict[str, str]) -> tuple[list[CommandStep], list[str]]:
    steps: list[CommandStep] = []
    notes: list[str] = []
    for g in guests:
        if classify(states, g) == ABSENT:
            steps.append(_create_step(g))
        else:
            notes.append(
                f"{g}: already created — mqlab vm up to start, mqlab vm destroy to recreate"
            )
    return steps, notes


def _plan_up(guests: list[str], states: dict[str, str]) -> tuple[list[CommandStep], list[str]]:
    steps: list[CommandStep] = []
    notes: list[str] = []
    for g in guests:
        state = classify(states, g)
        if state == OFF:
            steps.append(_start_step(g))
        elif state == RUNNING:
            notes.append(f"{g}: already running")
        else:
            notes.append(f"{g}: not created — run mqlab vm create first")
    return steps, notes


def _plan_down(guests: list[str], states: dict[str, str]) -> tuple[list[CommandStep], list[str]]:
    steps: list[CommandStep] = []
    notes: list[str] = []
    for g in guests:
        state = classify(states, g)
        if state == RUNNING:
            steps.append(_shutdown_step(g))
        elif state == OFF:
            notes.append(f"{g}: already off")
        else:
            notes.append(f"{g}: not created")
    return steps, notes


def _plan_destroy(guests: list[str], states: dict[str, str]) -> tuple[list[CommandStep], list[str]]:
    steps: list[CommandStep] = []
    notes: list[str] = []
    for g in guests:
        state = classify(states, g)
        if state == RUNNING:
            steps.extend([_forceoff_step(g), _undefine_step(g)])  # force off, then remove
        elif state == OFF:
            steps.append(_undefine_step(g))
        else:
            notes.append(f"{g}: already gone")
    return steps, notes


# State-aware net planners (#98) — the same probe-then-act model as the guest verbs,
# mapped onto the virsh net-* lifecycle. Existence = define/undefine; active =
# start/deactivate. (virsh `net-destroy` means *deactivate*, not remove.)
def _net_plan_create(
    nets: list[str], states: dict[str, str]
) -> tuple[list[CommandStep], list[str]]:
    steps: list[CommandStep] = []
    notes: list[str] = []
    for net in nets:
        if classify_net(states, net) == ABSENT:
            steps.extend([_net_define_step(net), _net_autostart_step(net)])
        else:
            notes.append(
                f"{net}: already created — mqlab net up to start, mqlab net destroy to recreate"
            )
    return steps, notes


def _net_plan_up(nets: list[str], states: dict[str, str]) -> tuple[list[CommandStep], list[str]]:
    steps: list[CommandStep] = []
    notes: list[str] = []
    for net in nets:
        state = classify_net(states, net)
        if state == INACTIVE:
            steps.append(_net_start_step(net))
        elif state == ACTIVE:
            notes.append(f"{net}: already up")
        else:
            notes.append(f"{net}: not created — run mqlab net create first")
    return steps, notes


def _net_plan_down(nets: list[str], states: dict[str, str]) -> tuple[list[CommandStep], list[str]]:
    steps: list[CommandStep] = []
    notes: list[str] = []
    for net in nets:
        state = classify_net(states, net)
        if state == ACTIVE:
            steps.append(_net_deactivate_step(net))
        elif state == INACTIVE:
            notes.append(f"{net}: already down")
        else:
            notes.append(f"{net}: not created")
    return steps, notes


def _net_plan_destroy(
    nets: list[str], states: dict[str, str]
) -> tuple[list[CommandStep], list[str]]:
    steps: list[CommandStep] = []
    notes: list[str] = []
    for net in nets:
        state = classify_net(states, net)
        if state == ACTIVE:
            steps.extend([_net_deactivate_step(net), _net_undefine_step(net)])  # deactivate, remove
        elif state == INACTIVE:
            steps.append(_net_undefine_step(net))
        else:
            notes.append(f"{net}: already gone")
    return steps, notes


def _probe(deps: Deps, cmd: Command, parser: Callable[[str], dict[str, str]]) -> dict[str, str]:
    # The awareness step: show + capture a virsh listing, parse it to per-name state.
    deps.renderer.command(cmd.display())
    deps.transcript.write(f"$ {cmd.display()}")
    captured: list[str] = []

    def sink(line: str) -> None:
        deps.renderer.output(line)
        deps.transcript.write(line)
        captured.append(line)

    deps.runner.run(cmd, sink)
    return parser("\n".join(captured))


def _probe_states(deps: Deps) -> dict[str, str]:
    # Per-domain state from `virsh list --all` (keyed lab_<guest>).
    return _probe(deps, Command([*_VIRSH, "list", "--all"]), parse_domain_states)  # noqa: S607


def _probe_net_states(deps: Deps) -> dict[str, str]:
    # Per-network state from `virsh net-list --all` (keyed by plain net name).
    return _probe(deps, Command([*_VIRSH, "net-list", "--all"]), parse_net_states)  # noqa: S607


def _source_secret(deps: Deps, name: str) -> str:
    # Auto-generated lab secret -> its value, to inject into the playbook env.
    # No hiding: the lab is a throwaway illusion, so this echoes + tees like any
    # other step (mirrors _probe_states). lab-secret.sh generates+persists once.
    cmd = Command(["bash", str(lab_script("lab-secret.sh")), name])  # noqa: S607
    deps.renderer.command(cmd.display())
    deps.transcript.write(f"$ {cmd.display()}")
    captured: list[str] = []

    def sink(line: str) -> None:
        deps.renderer.output(line)
        deps.transcript.write(line)
        captured.append(line)

    code = deps.runner.run(cmd, sink)
    if code != 0:
        deps.renderer.error(f"lab-secret.sh {name} failed (exit {code})")
        raise typer.Exit(code=2)
    return "\n".join(captured).strip()


def _execute_stateful(
    verb: str,
    items: list[str],
    planner: Callable[[list[str], dict[str, str]], tuple[list[CommandStep], list[str]]],
    *,
    step_mode: bool,
    prober: Callable[[Deps], dict[str, str]] = _probe_states,
) -> None:
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps(verb, timestamp)
    try:
        steps, notes = planner(items, prober(deps))
        for note in notes:
            deps.renderer.note(note)
            deps.transcript.write(note)
        run_steps(
            steps,
            runner=deps.runner,
            renderer=deps.renderer,
            transcript=deps.transcript,
            step_mode=step_mode,
            pauser=deps.pauser,
        )
    except NoTTYError as exc:
        deps.renderer.error(str(exc))
        raise typer.Exit(code=2) from exc
    except StepFailedError as exc:
        raise typer.Exit(code=exc.exit_code) from exc
    finally:
        deps.transcript.close()


def _ssh_into(guest: str) -> None:
    # Interactive: replace this process with vagrant ssh so the TTY passes through —
    # the one verb that is not a captured/streamed step. Runs from lab/.
    os.chdir(repo_root() / "lab")
    os.execvp("vagrant", ["vagrant", "ssh", guest])  # noqa: S606, S607 - TTY passthrough (lab)


@vm_app.command("create")
def vm_create(pattern: _Pattern, step: _StepFlag = False) -> None:
    """Create + provision the selected guests (skips any that already exist)."""
    guests = _resolve_or_exit(pattern, resolve_guests, "guest")
    _execute_stateful("vm-create", guests, _plan_create, step_mode=step)


@vm_app.command("up")
def vm_up(pattern: _Pattern, step: _StepFlag = False) -> None:
    """Start the selected guests (skips any already running)."""
    guests = _resolve_or_exit(pattern, resolve_guests, "guest")
    _execute_stateful("vm-up", guests, _plan_up, step_mode=step)


@vm_app.command("down")
def vm_down(pattern: _Pattern, step: _StepFlag = False) -> None:
    """Shut down the selected guests (skips any already off)."""
    guests = _resolve_or_exit(pattern, resolve_guests, "guest")
    _execute_stateful("vm-down", guests, _plan_down, step_mode=step)


@vm_app.command("destroy")
def vm_destroy(pattern: _Pattern, step: _StepFlag = False) -> None:
    """Remove the selected guests + disks (force-stops running ones; skips absent)."""
    guests = _resolve_or_exit(pattern, resolve_guests, "guest")
    _execute_stateful("vm-destroy", guests, _plan_destroy, step_mode=step)


@vm_app.command("status")
def vm_status(selector: _Pattern = "all") -> None:
    """Show the fleet — topology joined with live virsh state; optional selector filters it."""
    guests = _resolve_or_exit(selector, resolve_guests, "guest")
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps("vm-status", timestamp)
    try:
        code = vm_status_core(deps.runner, deps.renderer, deps.transcript, guests=guests)
    finally:
        deps.transcript.close()
    if code != 0:
        raise typer.Exit(code=code)


@vm_app.command("inventory")
def vm_inventory() -> None:
    """Render build/inventory.ini from topology and echo it (the static map)."""
    deps = build_deps("vm-inventory", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        text = lab_inventory()
        path = inventory_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        deps.renderer.command(f"render -> {path}")
        for line in text.splitlines():
            deps.renderer.output(line)
            deps.transcript.write(line)
    finally:
        deps.transcript.close()


def _provision(setup_name: str) -> None:
    setup = lab_setups().get(setup_name)
    if setup is None:
        typer.echo(f"no lab setup named {setup_name!r} — see mqlab vm status", err=True)
        raise typer.Exit(code=2)
    if setup.provision is None:
        typer.echo(f"setup {setup_name} has no provision playbook", err=True)
        raise typer.Exit(code=2)
    members = setup_members(setup_name) or []
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps("vm-provision", timestamp)
    try:
        states = _probe_states(deps)
        down = [m for m in members if classify(states, m) != RUNNING]
        if down:
            note = f"{', '.join(down)}: not running — run mqlab vm up {setup_name} first"
            deps.renderer.note(note)
            deps.transcript.write(note)
            raise typer.Exit(code=3)
        secret_env = {s.upper(): _source_secret(deps, s) for s in setup.secrets}
        inv = inventory_path()
        inv.parent.mkdir(parents=True, exist_ok=True)
        inv.write_text(lab_inventory())
        deps.renderer.note(f"rendered {inv}")
        deps.transcript.write(f"rendered {inv}")
        step = CommandStep(
            f"{setup_name} provision",
            Command(
                ["uv", "run", "ansible-playbook", Path(setup.provision).name],  # noqa: S607
                cwd=repo_root() / "ansible",
                env=secret_env or None,
            ),
        )
        run_steps(
            [step],
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


@vm_app.command("provision")
def vm_provision(setup: str) -> None:
    """Provision a setup — render the inventory, then run its Ansible playbook."""
    _provision(setup)


@vm_app.command("ssh")
def vm_ssh(guest: str) -> None:
    """Open an interactive shell on one guest (vagrant ssh)."""
    _ssh_into(guest)


def main() -> None:
    app()
