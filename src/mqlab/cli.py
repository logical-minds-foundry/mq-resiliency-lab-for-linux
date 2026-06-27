"""mqlab — the operator orchestrator CLI (Typer + Rich). A thin veneer (spec §2)."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.console import Console

from mqlab import buildenv, parity
from mqlab.arms import arm_of, lab_arms, resolve_verb
from mqlab.artifact import download_mq_tarball, ensure_mq_tarballs
from mqlab.buildenv import BuildEnvError
from mqlab.doctor import Check, run_checks, summarise
from mqlab.dr import Ledger, assert_self_correct, build_report, peak_exposure, reconcile
from mqlab.fleet import parse_domain_states
from mqlab.guestsel import resolve_guests
from mqlab.hostfacts import HostFacts, probe
from mqlab.inventory import inventory_path, lab_inventory
from mqlab.lifecycle import ABSENT, ACTIVE, INACTIVE, OFF, RUNNING, classify, classify_net, is_live
from mqlab.manifest import (
    DEFAULT_MQ_VERSION,
    box_version_pins,
    load_manifest,
    manifest_exists,
    obs_overlay,
    read_selection,
    resolve_selection,
    vars_overlay,
)
from mqlab.netsel import parse_net_states, resolve_nets
from mqlab.orchestrator import CommandStep, StepFailedError, run_steps
from mqlab.paths import (
    box_versions_path,
    lab_network,
    lab_script,
    mq_cache_dir,
    repo_root,
    reports_dir,
    resolved_topology_path,
    runs_dir,
    selection_state_path,
    state,
    work,
)
from mqlab.pauser import NoTTYError, TTYPauser
from mqlab.phases import PHASES, build_states, first_unsatisfied
from mqlab.platforms import PlatformError, build_domain_virt, ensure_resolved
from mqlab.render import Renderer
from mqlab.roster import lab_roster, roster_path
from mqlab.runner import Command, SubprocessRunner
from mqlab.runplan import baseline_run_plan
from mqlab.runreport import (
    RunReport,
    append_index,
    capture_metadata,
    read_commit,
    read_config_digest,
    read_versions,
    write_bundle,
)
from mqlab.setups import lab_setups, setup_members
from mqlab.stacks import lab_stacks
from mqlab.transcript import Transcript, transcript_path
from mqlab.vmstatus import vm_status_core

if TYPE_CHECKING:
    from collections.abc import Callable

    from mqlab.orchestrator import Pauser
    from mqlab.phases import Phase
    from mqlab.runner import CommandRunner
    from mqlab.setups import QmConfig, Setup
    from mqlab.stacks import Stack


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
_ManifestOpt = Annotated[
    str | None, typer.Option("--manifest", help="version manifest name (default: 'default')")
]


# --- Version manifest wiring (#266). All gracefully optional: a setup/repo with no
#     manifest behaves exactly as before (the helpers return None / []). -------------
def _fetch_mq_tarball(name: str, dest: Path) -> None:
    """Acquire a missing MQ tarball from IBM's no-auth public CDN (#276/#291) — no
    credentials, so a fresh or anonymous box bootstraps without manual placement.
    Only the RHEL OS image stays a manual artifact (licensed, not downloadable)."""
    download_mq_tarball(name, dest)


def _apply_manifest(
    setup_name: str, *, requested: str | None = None, at_create: bool = False
) -> Path | None:
    """Render the manifest overlay for a setup (None if it has no manifest). At create,
    also pin box_version. (MQ tarballs are ensured separately by _ensure_mq_artifacts,
    which is topology-driven and not gated on a manifest — #333.)"""
    if not manifest_exists(setup_name):
        return None
    name = (
        resolve_selection(setup_name, requested or "default")
        if at_create
        else resolve_selection(setup_name, requested)
    )
    man = load_manifest(setup_name, name)
    op = work("manifests", f"{setup_name}.overlay.json")
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(vars_overlay(man)))
    if at_create:
        bvf = box_versions_path()
        pins = json.loads(bvf.read_text()) if bvf.exists() else {}
        pins.update(box_version_pins(man))
        bvf.write_text(json.dumps(pins))
    return op


def resolve_mq_version(setup_name: str, requested: str | None = None) -> str:
    """The MQ version for a setup: its manifest's pin if it has one, else the repo
    default — so a manifest-less setup (e.g. monitoring) still resolves a version. (#333)"""
    if manifest_exists(setup_name):
        name = resolve_selection(setup_name, requested or "default")
        return load_manifest(setup_name, name).mq_version
    return DEFAULT_MQ_VERSION


def _ensure_mq_artifacts(setup_name: str, *, requested: str | None = None) -> None:
    """Ensure the arch-correct MQ tarball(s) for a setup's guest platforms are present,
    regardless of whether the setup has a manifest (#333). Topology-driven via
    setup_platforms; the host facts are passed explicitly so the arch tracks this
    controller rather than an implicit probe() default."""
    ensure_mq_tarballs(
        setup_name,
        resolve_mq_version(setup_name, requested),
        mq_cache_dir(),
        fetch=_fetch_mq_tarball,
        facts=probe(),
    )


def _galaxy_install_step() -> CommandStep:
    # Install the lab's Ansible galaxy collections (community.crypto, needed by the PKI
    # play) into build/cache — ansible.cfg's collections_path. Idempotent: ansible-galaxy
    # skips a collection already present. (#343)
    argv = [
        "ansible-galaxy",
        "collection",
        "install",
        "-r",
        "ansible/requirements.yml",
        "-p",
        "build/cache",
    ]
    return CommandStep("ansible collections", Command(argv, cwd=repo_root()))  # noqa: S607


def _pki_ensure_step() -> CommandStep:
    _render_pki_entities()
    return CommandStep("pki ensure", Command([*_PKI_PLAYBOOK], cwd=repo_root() / "ansible"))  # noqa: S607


def _ensure_prereqs(setup_name: str, *, requested: str | None = None, step: bool = False) -> None:
    """The single place that ensures every fresh-volume prerequisite a setup's playbooks
    need (#343). All live under build/ on the persistent volume, which a recreate wipes,
    so the provision verbs regenerate them — idempotently, in dependency order:
      1. Ansible galaxy collections (community.crypto — required by the PKI play)
      2. the MQ-for-Developers tarball(s) for the setup's guest platforms
      3. the PKI CA + entity keystores
    galaxy + PKI run through the step runner (progress/transcript); MQ is a Python fetch.
    """
    _ensure_mq_artifacts(setup_name, requested=requested)
    _execute("prerequisites", [_galaxy_install_step(), _pki_ensure_step()], step_mode=step)


# --- Host-arch gating (#276): render the host-resolved topology + enforce the native-
#     KVM requirement before any verb that loads the Vagrantfile. -------------------
def _doctor_checks() -> list[Check]:
    return run_checks(probe(), which=shutil.which)


def _prepare_lab() -> None:
    """Precondition of the vagrant-loading verbs: wire the build/ buckets (#286),
    then outside Vergil hard-gate on host prerequisites, then render the resolved
    topology into work/ (which enforces the native-KVM requirement). Fail loud."""
    _build_ensure()  # cache/state symlinks + work/temp dirs before anything writes build/ (#286)
    facts = probe()
    if not facts.in_vergil:
        ok, report = summarise(run_checks(facts, which=shutil.which))
        if not ok:
            typer.echo(report)
            raise typer.Exit(code=1)
    try:
        ensure_resolved(facts=facts)
    except PlatformError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc


@app.command("doctor")
def doctor() -> None:
    """Check this host can run the lab (arch, KVM, required tools)."""
    ok, report = summarise(_doctor_checks())
    typer.echo(report)
    raise typer.Exit(code=0 if ok else 1)


# --- build/ bucket lifecycle (#286): cache/state shared, work/temp local ----------
build_app = typer.Typer(
    help="build/ bucket lifecycle (cache/state/work/temp)", no_args_is_help=True
)
app.add_typer(build_app, name="build")


# thin seams so tests monkeypatch without real git/fs:
def _build_bucket_path(bucket: str) -> Path:
    return buildenv.bucket_path(bucket, repo_root())


def _build_ensure() -> None:
    buildenv.ensure(repo_root())


@app.callback()
def _root(ctx: typer.Context) -> None:
    """Wire the build/ buckets before *any* lab command runs (#304).

    Most verbs touch buckets only as a side effect (e.g. every command writes a transcript to
    build/state/runs/), so wiring must happen before the body — otherwise the first command in a
    fresh worktree creates a real local build/state and poisons the cache/state symlinks. Skip the
    `build` group: it manages bucket lifecycle explicitly, and `build path` is a shell hot-path."""
    if ctx.invoked_subcommand and ctx.invoked_subcommand != "build":
        _build_ensure()


def _build_clean(*, drop_cache: bool = False, drop_state: bool = False) -> list[str]:
    return buildenv.clean(repo_root(), drop_cache=drop_cache, drop_state=drop_state)


@build_app.command("path")
def build_path(bucket: str) -> None:
    """Print the resolved absolute path of a bucket (cache|state|work|temp)."""
    try:
        typer.echo(str(_build_bucket_path(bucket)))
    except BuildEnvError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc


@build_app.command("ensure")
def build_ensure() -> None:
    """Create the four buckets; in a worktree, symlink cache/+state/ back to main."""
    _build_ensure()


@build_app.command("clean")
def build_clean(
    cache: bool = False,
    state: bool = False,
    yes_destroy_state: Annotated[bool, typer.Option("--yes-destroy-state")] = False,
) -> None:
    """Nuke work/+temp/ (+stray). --cache also drops downloads; --state needs confirmation."""
    if state and not yes_destroy_state:
        typer.echo(
            "refusing to drop state/ (snapshots, ISO, a running lab's secrets). "
            "Re-run with --state --yes-destroy-state if you really mean it.",
            err=True,
        )
        raise typer.Exit(code=2)
    typer.echo("removed: " + ", ".join(_build_clean(drop_cache=cache, drop_state=state)))


@build_app.command("status")
def build_status() -> None:
    """Show each bucket: path, real-or-symlink."""
    for bucket in buildenv.BUCKETS:
        kind = "symlink->main" if (repo_root() / "build" / bucket).is_symlink() else "local"
        typer.echo(f"{bucket:6} {kind:14} {_build_bucket_path(bucket)}")


@build_app.command("migrate")
def build_migrate(dry_run: Annotated[bool, typer.Option("--dry-run")] = False) -> None:
    """Move existing top-level build/ contents into buckets (idempotent)."""
    for src, dst in buildenv.migrate(repo_root(), dry_run=dry_run):
        typer.echo(f"{'PLAN' if dry_run else 'MOVED'} {src} -> {dst}")


def _manifest_args(
    setup_name: str, *, requested: str | None = None, at_create: bool = False
) -> list[str]:
    op = _apply_manifest(setup_name, requested=requested, at_create=at_create)
    return ["-e", f"@{op}"] if op else []


def _obs_manifest_args() -> list[str]:
    shared = repo_root() / "manifests" / "_shared" / "observability.yaml"
    if not shared.exists():
        return []
    op = work("manifests", "_obs.overlay.json")
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(obs_overlay()))
    return ["-e", f"@{op}"]


def _qm_extra_vars(setup_name: str) -> list[str]:
    # Thread a setup's QM identity (the single source, QmConfig) into the ansible
    # plays that need it, so the QM/channel names DERIVE rather than referencing an
    # undefined `setup_dict`. The distributed provision playbooks (their-side MQSC)
    # and the obs exporters both consume these (#356). No qm -> no args.
    setup = lab_setups().get(setup_name)
    qm = setup.qm if setup else None
    if qm is None:
        return []
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


def _obs_qm_args() -> list[str]:
    # The probe's exporters monitor the pcmk distributed setup's QM pair. obs is
    # pcmk-pinned today (site-obs.yml historically hardcoded PCMKAPP/PCMKSVC); #350's
    # per-stack observe generalizes this.
    return _qm_extra_vars("distributed-pcmk-ubuntu")


def _vagrant_env() -> dict[str, str]:
    # Vagrant's per-machine state (the libvirt-domain <-> vagrant mapping + keys) is
    # SHARED, irreplaceable live-lab state — one lab, one instance — so it belongs in
    # build/state, not a per-worktree lab/.vagrant that dies with the worktree and
    # orphans the running lab. Redirect the dotfile via VAGRANT_DOTFILE_PATH so every
    # checkout/worktree drives the same lab. Resolved so all of them pass one canonical
    # path through the worktree's state/ symlink to the primary checkout (#355).
    return {"VAGRANT_DOTFILE_PATH": str(state("vagrant").resolve())}


def _manifest_id(setup_name: str) -> str:
    name = read_selection(setup_name)
    return f"{setup_name}/{name}" if name else ""


def _manifest_digest_paths(setup_name: str) -> list[Path]:
    p = selection_state_path(setup_name)
    return [p] if p.exists() else []


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

qm_app = typer.Typer(help="MQ queue managers (Pacemaker-managed HA)", no_args_is_help=True)
app.add_typer(qm_app, name="qm")


@obs_app.command("targets")
def obs_targets() -> None:
    """Render build/work/prometheus/targets/node.json from topology and echo it."""
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
    """Render build/work/grafana/dashboards/lab-status.json from topology and echo it."""
    from mqlab.dashboard import dashboard_path, lab_dashboard

    deps = build_deps("obs-dashboard", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        text = lab_dashboard()
        path = dashboard_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        deps.renderer.command(f"render -> {path}")
        deps.transcript.write(f"render -> {path}")
        from mqlab.clusterboard import (
            cluster_dashboard_path,
            lab_cluster_dashboard,
            lab_nativeha_dashboard,
            lab_rdqm_dashboard,
            nativeha_dashboard_path,
            rdqm_dashboard_path,
        )

        cockpit = cluster_dashboard_path()
        cockpit.write_text(lab_cluster_dashboard())
        deps.renderer.command(f"render -> {cockpit}")
        deps.transcript.write(f"render -> {cockpit}")
        nha_cockpit = nativeha_dashboard_path()
        nha_cockpit.write_text(lab_nativeha_dashboard())
        deps.renderer.command(f"render -> {nha_cockpit}")
        deps.transcript.write(f"render -> {nha_cockpit}")
        rdqm_cockpit = rdqm_dashboard_path()
        rdqm_cockpit.write_text(lab_rdqm_dashboard())
        deps.renderer.command(f"render -> {rdqm_cockpit}")
        deps.transcript.write(f"render -> {rdqm_cockpit}")
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


def _render_reach_peers() -> Path:
    """Write work/obs/reach-peers.json (host -> net -> peers) from topology; return its path."""
    import json as _json

    import yaml as _yaml

    from mqlab.netstate import net_peers

    topo = _yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    path = work("obs", "reach-peers.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(net_peers(topo), indent=2) + "\n")
    return path


# Fixed non-QM PKI entities (verbatim from ansible/vars/pki-entities.yml §non-QM rows).
# These are stable across QM renames and are never derived from the topology.
_FIXED_PKI_ENTITIES: list[dict[str, Any]] = [
    {"cn": "app-client", "org": "app-org", "ou": "apps", "kind": "personal", "trust": []},
    # Trusts svc-org too: one app-org ops identity that scrapes BOTH QMs (exporter
    # must validate the SVC QM's O=svc-org server cert — #250 / MON.SVRCONN).
    {
        "cn": "mq_prometheus",
        "org": "app-org",
        "ou": "ops",
        "kind": "personal",
        "trust": ["svc-org"],
    },
    {"cn": "mqweb", "org": "app-org", "ou": "ops", "kind": "personal", "trust": []},
    {"cn": "pymqrest", "org": "app-org", "ou": "ops", "kind": "trust_only", "trust": []},
    # Co-located SVC responder presents O=svc-org (#250 / SVC.SVRCONN).
    {
        "cn": "svc-responder",
        "org": "svc-org",
        "ou": "messaging",
        "kind": "personal",
        "trust": ["app-org"],
    },
]


def _render_pki_entities() -> Path:
    """Derive PKI entity list from topology and write build/work/pki/entities.json.

    Produces one entry per distinct app-org QM name (qm_app) and one entry for
    the svc-org QM (qm_svc), deduped across setups, plus the fixed non-QM entities.
    In Phase 1 this yields the same CN set as the static ansible/vars/pki-entities.yml.
    """
    from mqlab.setups import lab_setups

    seen_app: set[str] = set()
    seen_svc: set[str] = set()
    qm_entities: list[dict[str, Any]] = []

    for setup in lab_setups().values():
        if setup.qm is None:
            continue
        app_cn = setup.qm.qm_app
        if app_cn not in seen_app:
            seen_app.add(app_cn)
            qm_entities.append(
                {
                    "cn": app_cn,
                    "org": "app-org",
                    "ou": "messaging",
                    "kind": "personal",
                    "trust": ["svc-org"],
                }
            )
        svc_cn = setup.qm.qm_svc
        if svc_cn not in seen_svc:
            seen_svc.add(svc_cn)
            qm_entities.append(
                {
                    "cn": svc_cn,
                    "org": "svc-org",
                    "ou": "messaging",
                    "kind": "personal",
                    "trust": ["app-org"],
                }
            )

    entities = qm_entities + _FIXED_PKI_ENTITIES
    path = work("pki", "entities.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entities, indent=2) + "\n")
    return path


@obs_app.command("reach-peers")
def obs_reach_peers() -> None:
    """Render build/work/obs/reach-peers.json (host -> net -> peers) from topology."""
    deps = build_deps("obs-reach-peers", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        path = _render_reach_peers()
        deps.renderer.command(f"render -> {path}")
        deps.transcript.write(f"render -> {path}")
    finally:
        deps.transcript.close()


GRAFANA_URL = "http://10.50.0.2:3000"  # obs net-mgmt IP : Grafana port (direct, inside the VM)
# What the workstation actually browses: Lima auto-forwards the base VM's :3000 to
# the Mac's localhost:3000, and the vergil-portforward relay (below) bridges :3000
# to the obs guest — so from the Mac it's plain localhost:3000, no manual tunnel.
WORKSTATION_GRAFANA_URL = "http://localhost:3000"
# The systemd-socket-proxyd relay vergil-vm provisions from port_forwards in
# vergil.toml (#170). Restarting grafana (the obs role's notify) wedges its held
# downstream connection, so 'obs up' bounces it after provisioning (#264).
_RELAY_UNITS = ["vergil-portforward-3000.socket", "vergil-portforward-3000.service"]


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

    # the dedicated cluster cockpit boards, rendered beside lab-status: lab-pcmk-cluster (#219),
    # lab-nativeha-cluster (#279), and lab-rdqm-cluster (#287)
    from mqlab.clusterboard import (
        cluster_dashboard_path,
        lab_cluster_dashboard,
        lab_nativeha_dashboard,
        lab_rdqm_dashboard,
        nativeha_dashboard_path,
        rdqm_dashboard_path,
    )

    cockpit = cluster_dashboard_path()
    cockpit.parent.mkdir(parents=True, exist_ok=True)
    cockpit.write_text(lab_cluster_dashboard())
    nha_cockpit = nativeha_dashboard_path()
    nha_cockpit.write_text(lab_nativeha_dashboard())
    rdqm_cockpit = rdqm_dashboard_path()
    rdqm_cockpit.write_text(lab_rdqm_dashboard())

    return [
        CommandStep(
            "render targets + inventory + dashboard",
            Command(["echo", f"rendered -> {targets}, {inv}, {dash}"]),  # noqa: S607
        ),
        CommandStep(
            "monitoring create",
            Command(  # noqa: S607
                ["vagrant", "up", "obs", "mon-probe"], cwd=repo_root() / "lab", env=_vagrant_env()
            ),
        ),
        CommandStep(
            "provision monitoring",
            # bare filename, run from ansible/ so ansible.cfg (inventory path) is
            # picked up — matches dr-provision.sh.
            Command(
                ["ansible-playbook", "site-obs.yml", *_obs_qm_args(), *_obs_manifest_args()],  # noqa: S607
                cwd=repo_root() / "ansible",
            ),
        ),
        CommandStep(
            "provision host collector",
            # the Vergil VM (libvirt host) — node_exporter + the lab_network_state
            # timer — via a connection=local play.
            Command(
                [
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
        CommandStep(
            # provisioning above bounced grafana; clear the relay's stale downstream
            # so the workstation forward isn't left wedged (#264).
            "heal grafana port-forward relay",
            Command(["sudo", "systemctl", "restart", *_RELAY_UNITS]),  # noqa: S607
        ),
        CommandStep(
            # fail loud if the workstation-facing endpoint isn't actually serving —
            # don't report success while the browser path is dead (#264). -f makes
            # curl exit non-zero on any non-2xx or a dropped connection.
            "verify grafana reachable (workstation forward)",
            Command(["curl", "-fsS", "-m", "5", f"{WORKSTATION_GRAFANA_URL}/api/health"]),  # noqa: S607
        ),
    ]


@obs_app.command("up")
def obs_up(step: _StepFlag = False) -> None:
    """Render targets, create the monitoring pair, and provision Prometheus + Grafana."""
    _prepare_lab()  # obs up shells `vagrant up obs mon-probe` — gate + render (#276)
    # obs up runs site-obs.yml directly (not via _provision), so ensure monitoring's
    # fresh-volume prerequisites (galaxy collections + MQ tarball + PKI) first. (#343)
    _ensure_prereqs("monitoring", step=step)
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
    typer.echo(f"Workstation: {WORKSTATION_GRAFANA_URL}/d/lab-fleet-node  (Fleet — Node Health)")
    typer.echo(f"In the VM:   {GRAFANA_URL}/d/lab-fleet-node  (direct to the obs guest)")
    typer.echo(f"Live tail:   {WORKSTATION_GRAFANA_URL}/explore  (pick the Loki datasource, e.g.")
    typer.echo('             query {unit="mqlab-requester"} and toggle Live)')
    typer.echo("")
    typer.echo("The forward is automatic — no manual tunnel. Lima forwards the VM's")
    typer.echo("port 3000 to your Mac's localhost:3000, and the vergil-portforward relay")
    typer.echo("bridges that to the obs guest. Just browse localhost:3000 (anonymous —")
    typer.echo("no login, #258). If it drops after an 'obs up', the relay was wedged by a")
    typer.echo("grafana restart; 'mqlab obs up' now re-heals it as its last step (#264).")


@obs_app.command("instrument")
def obs_instrument(setup: str) -> None:
    """Instrument a setup's guests — install node_exporter (+ net-reach) via observability.yml."""
    _instrument(setup)


def _instrument(setup_name: str) -> None:
    # Fleet telemetry lives in observability.yml (hosts: all); per-setup provision
    # playbooks don't install node_exporter. This runs that play limited to the
    # setup, so its guests start reporting to Prometheus. Mirrors _provision.
    setup = lab_setups().get(setup_name)
    if setup is None:
        typer.echo(f"no lab setup named {setup_name!r} — see mqlab vm status", err=True)
        raise typer.Exit(code=2)
    members = setup_members(setup_name) or []
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps("obs-instrument", timestamp)
    try:
        states = _probe_states(deps)
        down = [m for m in members if classify(states, m) != RUNNING]
        if down:
            note = f"{', '.join(down)}: not running — run mqlab vm up {setup_name} first"
            deps.renderer.note(note)
            deps.transcript.write(note)
            raise typer.Exit(code=3)
        # Render the inventory the play resolves through and the reach-peers map
        # the net-reach role consumes, then run the play limited to this setup.
        inv = inventory_path()
        inv.parent.mkdir(parents=True, exist_ok=True)
        inv.write_text(lab_inventory())
        deps.renderer.note(f"rendered {inv}")
        deps.transcript.write(f"rendered {inv}")
        peers = _render_reach_peers()
        deps.renderer.note(f"rendered {peers}")
        deps.transcript.write(f"rendered {peers}")
        step = CommandStep(
            f"{setup_name} instrument",
            Command(
                ["ansible-playbook", "observability.yml", "--limit", setup_name],  # noqa: S607
                cwd=repo_root() / "ansible",
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


_VIRSH = ["virsh", "-c", "qemu:///system"]


def _create_step(g: str) -> CommandStep:
    # Create + provision via Vagrant — the one verb that needs Vagrant (#96).
    cmd = Command(["vagrant", "up", g], cwd=repo_root() / "lab", env=_vagrant_env())  # noqa: S607
    return CommandStep(f"{g} create", cmd)


# Boxes built locally (not on Vagrant Cloud) -> their build script. build-box.sh
# REUSEs the host-durable build/state/boxes cache when present (a quick `vagrant box add`)
# and only does the ISO build on a truly first-ever run — ~45-90 min under TCG
# (arm64 Mac), minutes under KVM on a native-x86 host (#276/#291/#327).
_LOCAL_BOX_BUILDERS = {
    "rhel/9.6-x86_64": "lab/boxes/rhel96/build-box.sh",
}


def parse_box_list(text: str) -> dict[str, str]:
    """Parse `vagrant box list` -> {box_name: trailing info}; 'no boxes' -> {}."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("there are no"):
            continue
        name, _, rest = line.partition(" ")
        out[name] = rest.strip()
    return out


def _resolved_nodes() -> dict[str, Any]:
    """The rendered resolved topology's nodes (build/work/lab/topology.resolved.yaml, #276)."""
    import yaml as _yaml

    data = _yaml.safe_load(resolved_topology_path().read_text())
    nodes: dict[str, Any] = data.get("nodes", {})
    return nodes


def _needed_local_boxes(guests: list[str]) -> dict[str, str]:
    """Local-built boxes the given guests need -> build script."""
    nodes = _resolved_nodes()
    boxes = {(nodes.get(g) or {}).get("box") for g in guests}
    return {box: script for box, script in _LOCAL_BOX_BUILDERS.items() if box in boxes}


def _guests_need_dvd(guests: list[str]) -> bool:
    """Whether any guest attaches a DVD ISO cdrom (the RHEL offline dnf repo)."""
    nodes = _resolved_nodes()
    return any((nodes.get(g) or {}).get("dvd") for g in guests)


def _box_build_steps(
    needed: dict[str, str], present: dict[str, str], facts: HostFacts
) -> list[CommandStep]:
    # The local-built box is RHEL x86_64; build_domain_virt is the single authority
    # for whether that build runs under KVM (native x86 host) or TCG (#327).
    domain_type, cpu_mode = build_domain_virt(facts)
    return [
        CommandStep(
            f"box {box}",
            Command(  # noqa: S607
                [
                    "bash",
                    str(repo_root() / script),
                    "--domain-type",
                    domain_type,
                    "--cpu-mode",
                    cpu_mode,
                ]
            ),
        )
        for box, script in sorted(needed.items())
        if box not in present
    ]


def parse_vol_list(text: str) -> dict[str, str]:
    """Parse `virsh vol-list <pool>` -> {volume_name: path}; chrome lines skipped."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("Name") or set(line) <= {"-"}:
            continue
        parts = line.split()
        out[parts[0]] = parts[1] if len(parts) > 1 else ""
    return out


def _orphan_volumes(guests: list[str], vol_names: dict[str, str]) -> list[str]:
    """Pool volumes belonging to the given guests (lab_<g>.img / lab_<g>-*), e.g. the
    extra-disk vdb that vagrant-libvirt leaves behind when a create fails midway."""
    return [
        name
        for g in guests
        for name in vol_names
        if name == f"lab_{g}.img" or name.startswith(f"lab_{g}-")
    ]


def _vol_delete_step(name: str) -> CommandStep:
    cmd = Command([*_VIRSH, "vol-delete", "--pool", "default", name])  # noqa: S607
    return CommandStep(f"vol {name}", cmd)


def _sweep_orphan_volumes(guests: list[str]) -> None:
    """After destroy, delete any pool volumes for these guests not tied to a domain —
    the recovery path for a partially-failed create (vagrant-libvirt doesn't clean up
    extra-disk volumes on failure, #276). No-op when there are no orphans."""
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps("vm-destroy-sweep", timestamp)
    try:
        vols = _probe(deps, Command([*_VIRSH, "vol-list", "default"]), parse_vol_list)  # noqa: S607
        run_steps(
            [_vol_delete_step(v) for v in _orphan_volumes(guests, vols)],
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


def _ensure_local_boxes(guests: list[str]) -> None:
    """Make the RHEL substrate ready before `vagrant up`, so a fresh box bootstraps
    without manual steps (#276/#291): build/register any local-built box not yet in
    `vagrant box list` (REUSE from cache when present, ~minutes), and stage the DVD
    ISO into the libvirt pool (idempotent). No-op for cloud Ubuntu boxes."""
    needed = _needed_local_boxes(guests)
    need_dvd = _guests_need_dvd(guests)
    if not needed and not need_dvd:
        return
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps("box-build", timestamp)
    try:
        steps: list[CommandStep] = []
        if needed:
            cmd = Command(  # noqa: S607
                ["vagrant", "box", "list"], cwd=repo_root() / "lab", env=_vagrant_env()
            )
            steps += _box_build_steps(needed, _probe(deps, cmd, parse_box_list), probe())
        if need_dvd:
            stage = Command(["bash", str(lab_script("stage-rhel-iso.sh"))], cwd=repo_root())  # noqa: S607
            steps.append(CommandStep("stage rhel dvd", stage))
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
        state = classify(states, g)
        if state == ABSENT:
            steps.append(_create_step(g))
        elif state == OFF:
            steps.append(_start_step(g))  # created but stopped -> bring it up (#339)
        else:  # RUNNING
            notes.append(f"{g}: already running — mqlab vm destroy to recreate")
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
        if classify(states, g) == ABSENT:
            notes.append(f"{g}: already gone")
        elif is_live(states, g):
            # running OR paused/suspended: a live qemu process to force off before
            # undefine --remove-all-storage (which needs a stopped domain) (#339)
            steps.extend([_forceoff_step(g), _undefine_step(g)])
        else:
            steps.append(_undefine_step(g))  # shut off: remove directly
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
    # the one verb that is not a captured/streamed step. Runs from lab/. Point vagrant
    # at the shared dotfile (#355) so it finds the running lab even when this checkout
    # never created it (e.g. driving from a worktree, or after one was cleaned up).
    os.environ.update(_vagrant_env())
    os.chdir(repo_root() / "lab")
    os.execvp("vagrant", ["vagrant", "ssh", guest])  # noqa: S606, S607 - TTY passthrough (lab)


@vm_app.command("create")
def vm_create(pattern: _Pattern, manifest: _ManifestOpt = None, step: _StepFlag = False) -> None:
    """Create + provision the selected guests (skips any that already exist)."""
    guests = _resolve_or_exit(pattern, resolve_guests, "guest")
    _prepare_lab()  # host-arch gate + render the resolved topology (#276)
    # Pin box_version + ensure the MQ tarball before the boxes come up (no-op if the
    # pattern is not a manifested setup). #266
    _apply_manifest(pattern, requested=manifest, at_create=True)
    _ensure_local_boxes(guests)  # build/register the RHEL box from its cache/ISO (#276/#291)
    _execute_stateful("vm-create", guests, _plan_create, step_mode=step)


@vm_app.command("up")
def vm_up(pattern: _Pattern, step: _StepFlag = False) -> None:
    """Start the selected guests (skips any already running)."""
    guests = _resolve_or_exit(pattern, resolve_guests, "guest")
    _prepare_lab()  # host-arch gate + render the resolved topology (#276)
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
    _sweep_orphan_volumes(guests)  # clean orphaned lab_<g>-* volumes from failed creates (#276)


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
    """Render build/work/inventory.ini from topology and echo it (the static map)."""
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


@vm_app.command("roster")
def vm_roster() -> None:
    """Render build/work/salt/roster from topology and echo it (the salt-ssh map)."""
    deps = build_deps("vm-roster", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        text = lab_roster()
        path = roster_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        deps.renderer.command(f"render -> {path}")
        for line in text.splitlines():
            deps.renderer.output(line)
            deps.transcript.write(line)
    finally:
        deps.transcript.close()


def _provision(setup_name: str, *, requested: str | None = None) -> None:
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
        # Ensure the setup's fresh-volume prerequisites (galaxy collections + MQ
        # tarball + PKI) before the playbook runs — one place, idempotent. (#343)
        _ensure_prereqs(setup_name, requested=requested)
        inv = inventory_path()
        inv.parent.mkdir(parents=True, exist_ok=True)
        inv.write_text(lab_inventory())
        deps.renderer.note(f"rendered {inv}")
        deps.transcript.write(f"rendered {inv}")
        step = CommandStep(
            f"{setup_name} provision",
            Command(
                [
                    "ansible-playbook",  # noqa: S607
                    Path(setup.provision).name,
                    *_qm_extra_vars(setup_name),
                    *_manifest_args(setup_name, requested=requested),
                ],
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
def vm_provision(setup: str, manifest: _ManifestOpt = None) -> None:
    """Provision a setup — render the inventory, then run its Ansible playbook."""
    _provision(setup, requested=manifest)


@vm_app.command("ssh")
def vm_ssh(guest: str) -> None:
    """Open an interactive shell on one guest (vagrant ssh)."""
    _prepare_lab()  # ssh loads the Vagrantfile — ensure the resolved topology exists (#276)
    _ssh_into(guest)


# --- qm: the MQ queue-manager lifecycle on the Pacemaker arm (#109) --------------
# create/destroy run the reproducible mq-pcmk-qmgr role (the client-reproducible
# deliverable); up/down/status are direct, streamed pcs ops on the cluster. Pacemaker
# is the only thing allowed to start/stop the QM (its systemd units are disabled).


def _setup_qm_or_exit(setup_name: str) -> QmConfig:
    setup = lab_setups().get(setup_name)
    if setup is None:
        typer.echo(f"no lab setup named {setup_name!r} — see mqlab vm status", err=True)
        raise typer.Exit(code=2)
    if setup.qm is None:
        typer.echo(f"setup {setup_name} has no qm config", err=True)
        raise typer.Exit(code=2)
    return setup.qm


def _render_inventory(deps: Deps) -> None:
    # The static map ansible needs to reach the hosts (#101). Cheap; always fresh.
    inv = inventory_path()
    inv.parent.mkdir(parents=True, exist_ok=True)
    inv.write_text(lab_inventory())
    deps.renderer.note(f"rendered {inv}")
    deps.transcript.write(f"rendered {inv}")


def _qm_playbook(setup_name: str, playbook: str, verb: str) -> None:
    # create/destroy: pre-flight members running -> render inventory -> run the play.
    qm = _setup_qm_or_exit(setup_name)
    members = setup_members(setup_name) or []
    deps = build_deps(verb, datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        states = _probe_states(deps)
        down = [m for m in members if classify(states, m) != RUNNING]
        if down:
            note = f"{', '.join(down)}: not running — run mqlab vm up {setup_name} first"
            deps.renderer.note(note)
            deps.transcript.write(note)
            raise typer.Exit(code=3)
        _render_inventory(deps)
        step = CommandStep(
            f"{setup_name} {verb}",
            Command(
                [
                    "ansible-playbook",
                    playbook,
                    "-e",
                    f"qm_name={qm.name}",
                    "-e",
                    f"qm_vip={qm.vip}",
                    "-e",
                    f"qm_vip_ext={qm.vip_ext}",
                    "-e",
                    f"qm_app={qm.qm_app}",
                    "-e",
                    f"qm_svc={qm.qm_svc}",
                    "-e",
                    f"chl_to_svc={qm.chl_to_svc}",
                    "-e",
                    f"chl_to_app={qm.chl_to_app}",
                    # the counterparty CONNAME, only when this QM talks to one (#147)
                    *(["-e", f"svc_conn={qm.svc_conn}"] if qm.svc_conn else []),
                ],  # noqa: S607
                cwd=repo_root() / "ansible",
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


def _qm_cluster_cmd(setup_name: str, shell_cmd: str, verb: str) -> None:
    # up/down/status: a single streamed shell op on the arm's cluster first node
    # (pcs for pcmk, rdqm* for rdqm). No pre-flight — if the cluster is unreachable,
    # ansible's own error speaks (#109).
    _setup_qm_or_exit(setup_name)
    group = lab_arms()[arm_of(setup_name)].cluster_group
    deps = build_deps(verb, datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        _render_inventory(deps)
        step = CommandStep(
            f"{setup_name} {verb}",
            Command(
                [
                    "ansible",
                    f"{group}[0]",
                    "-b",
                    "-m",
                    "shell",
                    "-a",
                    shell_cmd,
                ],  # noqa: S607
                cwd=repo_root() / "ansible",
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


def _qm_script(setup_name: str, script: str, qm: QmConfig, verb: str) -> None:
    # Run a lab script with the rdqm-qm-create contract: QM, the single data-plane
    # floating IP (RDQM allows one FIP per QM, #216 spike), and (when set) the
    # counterparty CONNAME for the inter-QM MQSC. The partner reaches us over net-ext
    # by per-node CONNAME list (site-rdqm-distributed.yml our_conn), not a second VIP.
    argv = ["bash", str(lab_script(script)), qm.name, qm.vip, qm.svc_conn or ""]
    deps = build_deps(verb, datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        step = CommandStep(f"{setup_name} {verb}", Command(argv))  # noqa: S607
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


def _qm_dispatch(setup_name: str, verb: str) -> None:
    # Validate the setup (exists + has a QM) for clean exit-2 messages, then resolve
    # the arm's implementation of the verb from the registry and run it (#202, #216).
    qm = _setup_qm_or_exit(setup_name)
    impl = resolve_verb(setup_name, verb)
    if impl.kind == "playbook":
        _qm_playbook(setup_name, impl.value, verb)
    elif impl.kind == "pcs":
        _qm_cluster_cmd(setup_name, f"pcs {impl.value}", verb)
    elif impl.kind == "cmd":
        _qm_cluster_cmd(setup_name, impl.value.format(qm=qm.name, vip=qm.vip), verb)
    elif impl.kind == "script":
        _qm_script(setup_name, impl.value, qm, verb)
    else:  # pragma: no cover - unknown kinds are a topology error
        typer.echo(f"qm {verb}: unknown arm verb kind {impl.kind!r}", err=True)
        raise typer.Exit(code=2)


@qm_app.command("create")
def qm_create(setup: str) -> None:
    """Create the queue manager + its HA resources (arm-dispatched)."""
    _qm_dispatch(setup, "qm-create")


@qm_app.command("destroy")
def qm_destroy(setup: str) -> None:
    """Remove the queue manager + its HA resources."""
    _qm_dispatch(setup, "qm-destroy")


@qm_app.command("up")
def qm_up(setup: str) -> None:
    """Start the queue manager."""
    _qm_dispatch(setup, "qm-up")


@qm_app.command("down")
def qm_down(setup: str) -> None:
    """Stop the queue manager (HA intact)."""
    _qm_dispatch(setup, "qm-down")


@qm_app.command("status")
def qm_status(setup: str) -> None:
    """Show the queue manager's HA resource state."""
    _qm_dispatch(setup, "qm-status")


# --- pki: the lab PKI / TLS certificate provider (#210) --------------------------
# Wraps the connection=local site-pki.yml playbook (the provider generates CA +
# entity material under build/state/secrets/pki/). Mirrors the qm command-wraps-playbook
# shape. Cert expiry/rotation is out of scope (spec §8.2).
pki_app = typer.Typer(help="lab PKI / TLS certificate provider", no_args_is_help=True)
app.add_typer(pki_app, name="pki")

_PKI_PLAYBOOK = ["ansible-playbook", "site-pki.yml", "-c", "local", "-i", "localhost,"]


@pki_app.command("ensure")
def pki_ensure(step: _StepFlag = False) -> None:
    """Create/ensure both org CAs and every entity's certs + PKCS#12 keystores."""
    _render_pki_entities()
    cmd = Command([*_PKI_PLAYBOOK], cwd=repo_root() / "ansible")  # noqa: S607
    _execute("pki-ensure", [CommandStep("pki ensure", cmd)], step_mode=step)


@pki_app.command("issue")
def pki_issue(entity: str, step: _StepFlag = False) -> None:
    """Issue (or re-issue) one entity's cert + keystore — runs the provider for just that CN."""
    _render_pki_entities()
    cmd = Command([*_PKI_PLAYBOOK, "-e", f"pki_only={entity}"], cwd=repo_root() / "ansible")  # noqa: S607
    _execute("pki-issue", [CommandStep(f"pki issue {entity}", cmd)], step_mode=step)


@pki_app.command("list")
def pki_list() -> None:
    """List the PKI entity inventory (org, OU, kind) derived from topology."""
    entities = json.loads(_render_pki_entities().read_text())
    for e in entities:
        typer.echo(f"{e['cn']:<14} org={e['org']:<11} ou={e.get('ou', '-'):<16} {e['kind']}")


@app.command("parity")
def parity_matrix() -> None:
    """Print the cross-arm capability matrix (which verbs each arm supports)."""
    typer.echo(parity.render_markdown())


def _lookup_setup_or_exit(name: str) -> Setup:
    setup = lab_setups().get(name)
    if setup is None:
        typer.echo(f"no setup named {name!r}", err=True)
        raise typer.Exit(code=2)
    return setup


_Seconds = Annotated[int, typer.Option("--seconds", help="flow duration in seconds")]
_Rate = Annotated[int, typer.Option("--rate", help="messages per second")]


@app.command("run")
def run_setup(  # pragma: no cover - drives the live lab; proven by the integration gate
    setup_name: Annotated[str, typer.Argument(help="setup to run (e.g. distributed-pcmk-ubuntu)")],
    seconds: _Seconds = 30,
    rate: _Rate = 20,
    step: _StepFlag = False,
) -> None:
    """Drive one no-fault baseline run of a setup and write a timestamped report
    bundle stamped with (setup x config x commit) under build/state/reports/."""
    setup = _lookup_setup_or_exit(setup_name)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = runs_dir() / f"{timestamp}-{setup.name}"
    run_dir.mkdir(parents=True, exist_ok=True)

    steps = baseline_run_plan(setup, run_dir, seconds=seconds, rate=rate)
    _execute("run", steps, step_mode=step)  # raises typer.Exit on any step failure

    app = Ledger.read_jsonl(run_dir / "app.jsonl")
    svc = Ledger.read_jsonl(run_dir / "svc.jsonl")
    facts = reconcile(
        app, svc, secondary_present=set(), primary_disk_present=set(), cutover_ts=float("inf")
    )
    assert_self_correct(facts)  # baseline must be all-Confirmed or the instrument is broken
    scenario = build_report("BASELINE", arm_of(setup.name), facts, peak_exposure=peak_exposure(app))
    metadata = capture_metadata(
        setup.name,
        timestamp,
        commit_reader=read_commit,
        digest_reader=lambda: read_config_digest(
            [
                repo_root() / "lab" / "topology.yaml",
                inventory_path(),
                *_manifest_digest_paths(setup.name),
            ]
        ),
        version_reader=read_versions,
        manifest_reader=lambda: _manifest_id(setup.name),
    )
    report = RunReport(metadata=metadata, scenarios=[scenario])
    bundle = write_bundle(report, reports_dir())
    append_index(report, bundle, reports_dir())
    typer.echo(f"run report written: {bundle}")


def _lookup_stack_or_exit(name: str) -> Stack:
    # Validate a stack name early, with a clean exit-2 message (mirrors
    # _lookup_setup_or_exit, but over the #350 canonical stack registry).
    stack = lab_stacks().get(name)
    if stack is None:
        typer.echo(f"no stack named {name!r}", err=True)
        raise typer.Exit(code=2)
    return stack


# Prometheus on the obs guest (net-mgmt IP : Prometheus port). The observe probe
# queries its targets API to see whether this stack's exporters are registered.
PROMETHEUS_URL = "http://10.50.0.2:9090"


def _probe_qm_up(deps: Deps, stack: Stack) -> bool:
    """True iff the stack's QM reports up via its qm-status verb (provision phase).

    Resolves the stack's `qm-status` verb (the same per-stack dispatch dict the
    qm commands use) and runs that status command on the cluster's first node via
    `ansible <cluster_group>[0] -b -m shell`. Exit 0 ⇒ provisioned + up.

    Uses `stack.cluster_group` (e.g. "pcmk_a", "rdqm_a") — NOT `stack.groups[0]`,
    which for pcmk-ubuntu is the SAN iSCSI-target host ("san_a") that has no
    Pacemaker or MQ tooling and would always return a non-zero exit code.

    A stack with no qm-status verb or no cluster_group (reserved stack) is, by
    definition, not provisioned — returns False with no runner call.
    """
    impl = stack.verbs.get("qm-status")
    if not impl:
        return False
    if not stack.cluster_group:
        return False
    [(kind, value)] = impl.items()
    # pcs/cmd are the only status shapes in the registry; both run a shell command
    # on the cluster's first node. value is a literal or a {qm}-templated cmd.
    shell_cmd = f"pcs {value}" if kind == "pcs" else str(value).format(qm=stack.qm.name)
    group = stack.cluster_group
    cmd = Command(
        ["ansible", f"{group}[0]", "-b", "-m", "shell", "-a", shell_cmd],  # noqa: S607
        cwd=repo_root() / "ansible",
    )
    code = _probe_exit(deps, cmd)
    return code == 0


def _probe_observe(deps: Deps, stack: Stack) -> bool:
    """True iff this stack's exporter is a healthy Prometheus target (observe phase).

    Queries Prometheus' /api/v1/targets and checks the stack's app exporter port
    (from alloc.exporter_app_port) appears among the active targets with health
    "up". A stack with no exporter port allocated cannot be observed.
    """
    port = stack.alloc.get("exporter_app_port")
    if not port:
        return False
    cmd = Command(
        ["curl", "-fsS", "-m", "5", f"{PROMETHEUS_URL}/api/v1/targets"],  # noqa: S607
    )
    captured: list[str] = []
    code = _probe_exit(deps, cmd, sink=captured.append)
    if code != 0:
        return False
    payload = json.loads("\n".join(captured)) if captured else {}
    targets = (payload.get("data") or {}).get("activeTargets") or []
    needle = f":{port}/"
    return any(needle in (t.get("scrapeUrl") or "") and t.get("health") == "up" for t in targets)


def _probe_exit(deps: Deps, cmd: Command, *, sink: Callable[[str], None] | None = None) -> int:
    """Run a probe command, echo+tee its output (no hiding), and return its exit code.

    Like _probe but returns the exit code (the truth a satisfied-probe needs) and
    optionally tees each line to `sink` for the caller to inspect (observe parses
    the JSON body). Fail-loud by surfacing the real exit code — never swallowed.
    """
    deps.renderer.command(cmd.display())
    deps.transcript.write(f"$ {cmd.display()}")

    def tee(line: str) -> None:
        deps.renderer.output(line)
        deps.transcript.write(line)
        if sink is not None:
            sink(line)

    return deps.runner.run(cmd, tee)


def _probe_all(deps: Deps, stack: Stack) -> dict[str, Any]:
    """Gather the live world into the canonical states dict the phases consume.

    The live counterpart of phases.py's pure satisfied-probes: it actually runs
    virsh (nets + domains), the stack's qm-status verb (provision truth), and the
    Prometheus targets query (observe truth), then assembles them via build_states
    so the shape matches the registry's contract exactly. Fail-loud: each probe
    surfaces its real exit/parse result; nothing is swallowed.
    """
    nets = _probe_net_states(deps)
    domains = _probe_states(deps)
    qm_up = _probe_qm_up(deps, stack)
    observe = _probe_observe(deps, stack)
    return build_states(nets=nets, domains=domains, qm_up=qm_up, observe=observe)


def _select_phases(
    stack: Stack, states: dict[str, Any], *, only: str | None, from_phase: str | None
) -> list[Phase]:
    """The phases to run for this invocation, in order (sequencer selection).

    --only PHASE   → just that phase
    --from PHASE   → that phase onward
    neither        → from the first unsatisfied phase onward (empty if all satisfied)
    """
    if only:
        return [p for p in PHASES if p.name == only]
    if from_phase:
        idx = [p.name for p in PHASES].index(from_phase)
        return PHASES[idx:]
    start = first_unsatisfied(stack, states)
    return [] if start is None else PHASES[start:]


def _bootstrap_run(
    stack_name: str, *, only: str | None = None, from_phase: str | None = None, step: bool
) -> None:
    """Bring a stack up by running its bring-up phases (net → vms → provision →
    observe) from the first unsatisfied one, so a re-run resumes. --only/--from
    override the selection. Each phase fails loud: a step failure halts the run
    and prints a resume hint naming the failing phase."""
    stack = _lookup_stack_or_exit(stack_name)
    _prepare_lab()  # host gate up front — fail loud before any phase touches the lab
    deps = build_deps("bootstrap", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        states = _probe_all(deps, stack)
        selected = _select_phases(stack, states, only=only, from_phase=from_phase)
        if not selected:
            deps.renderer.note(f"{stack_name}: already satisfied — nothing to do")
            return
        for phase in selected:  # one phase at a time so a failure names its phase
            try:
                run_steps(
                    phase.build_steps(stack, deps),
                    runner=deps.runner,
                    renderer=deps.renderer,
                    transcript=deps.transcript,
                    step_mode=step,
                    pauser=deps.pauser,
                )
            except StepFailedError as exc:
                hint = f"resume with: mqlab bootstrap {stack_name} --from {phase.name}"
                typer.echo(hint, err=True)  # to the CLI's stderr, where the operator sees it
                deps.transcript.write(hint)
                raise typer.Exit(code=exc.exit_code) from exc
    except NoTTYError as exc:
        deps.renderer.error(str(exc))
        raise typer.Exit(code=2) from exc
    finally:
        deps.transcript.close()


def _validate_phase_name(value: str | None, flag: str) -> str | None:
    # Reject an unknown --from/--only phase name early with a clean exit-2 message.
    if value is not None and value not in [p.name for p in PHASES]:
        names = ", ".join(p.name for p in PHASES)
        typer.echo(f"{flag}: unknown phase {value!r} (known: {names})", err=True)
        raise typer.Exit(code=2)
    return value


_PHASE_HELP = "net/vms/provision/observe"
_FromOpt = Annotated[
    str | None, typer.Option("--from", help=f"run from this phase onward ({_PHASE_HELP})")
]
_OnlyOpt = Annotated[
    str | None, typer.Option("--only", help=f"run only this phase ({_PHASE_HELP})")
]


@app.command("bootstrap")
def bootstrap(  # pragma: no cover - thin delegator; logic covered via _bootstrap_run
    stack_name: Annotated[str, typer.Argument(help="stack to bring up (e.g. pcmk-ubuntu)")],
    from_phase: _FromOpt = None,
    only: _OnlyOpt = None,
    step: _StepFlag = False,
) -> None:
    """Bring up a whole stack in one command: net → vms → provision → observe.

    Runs from the first unsatisfied phase, so a re-run resumes. Use --from PHASE
    to force a starting phase or --only PHASE to run a single phase. Run
    `mqlab doctor` first to pre-flight the host."""
    _validate_phase_name(from_phase, "--from")
    _validate_phase_name(only, "--only")
    _bootstrap_run(stack_name, only=only, from_phase=from_phase, step=step)


def main() -> None:
    app()
