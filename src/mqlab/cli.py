"""mqlab — the operator orchestrator CLI (Typer + Rich). A thin veneer (spec §2)."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any

import typer
import yaml
from rich.console import Console

from mqlab import buildenv, parity
from mqlab.artifact import (
    download_mq_tarball,
    ensure_mq_tarballs_for_platforms,
)
from mqlab.buildenv import BuildEnvError
from mqlab.doctor import Check, run_checks, summarise
from mqlab.fleet import lab_guests, parse_domain_states
from mqlab.hostfacts import HostFacts, probe
from mqlab.inventory import inventory_path, lab_inventory
from mqlab.lifecycle import ABSENT, RUNNING, classify, is_live
from mqlab.manifest import (
    DEFAULT_MQ_VERSION,
    obs_overlay,
)
from mqlab.netsel import parse_net_states
from mqlab.orchestrator import CommandStep, StepFailedError, run_steps
from mqlab.paths import (
    lab_script,
    mq_cache_dir,
    repo_root,
    resolved_topology_path,
    state,
    work,
)
from mqlab.pauser import NoTTYError, TTYPauser
from mqlab.phases import PHASES, _commons_members, all_vms, build_states, first_unsatisfied
from mqlab.platforms import PlatformError, build_domain_virt, ensure_resolved
from mqlab.relay import GRAFANA_URL, RELAY_UNITS, WORKSTATION_GRAFANA_URL
from mqlab.render import Renderer
from mqlab.roster import lab_roster, roster_path
from mqlab.runner import Command, SubprocessRunner
from mqlab.stacks import lab_stacks, stack_members
from mqlab.transcript import Transcript, transcript_path
from mqlab.vmstatus import vm_status_core

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from mqlab.orchestrator import Pauser
    from mqlab.phases import Phase
    from mqlab.runner import CommandRunner
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


app = typer.Typer(help="mqlab — operator orchestrator for the MQ cluster lab", no_args_is_help=True)

_StepFlag = Annotated[bool, typer.Option("--step", help="pause after each step to inspect the lab")]
_Pattern = Annotated[str, typer.Argument(help="name, regex, or 'all'")]
_ManifestOpt = Annotated[
    str | None, typer.Option("--manifest", help="version manifest name (default: 'default')")
]


# --- MQ artifact + prerequisite wiring (#266/#350). MQ tarballs are arch-specific;
#     the repo-default version is used (the per-setup manifest pinning was dropped in
#     the #350 cutover). ----------------------------------------------------------
def _fetch_mq_tarball(name: str, dest: Path) -> None:
    """Acquire a missing MQ tarball from IBM's no-auth public CDN (#276/#291) — no
    credentials, so a fresh or anonymous box bootstraps without manual placement.
    Only the RHEL OS image stays a manual artifact (licensed, not downloadable)."""
    download_mq_tarball(name, dest)


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


def _verify_galaxy_collections() -> None:
    """Hard post-condition on the galaxy prereq (#596): every collection declared in
    ansible/requirements.yml must be present under the collections_path afterwards, else
    abort loudly. The install step is a fresh-volume prerequisite and `run_steps` only
    aborts on a step that runs and errors, so a newly-added collection could otherwise go
    silently missing — its callback/plugins then merely WARN at load and the run proceeds
    broken (this cost a ~76-minute instrumented rebuild when ansible.posix didn't land)."""
    data = yaml.safe_load((repo_root() / "ansible" / "requirements.yml").read_text())
    names = [c["name"] for c in data["collections"]]
    root = repo_root() / "build" / "cache" / "ansible_collections"
    missing = [n for n in names if not (root / n.replace(".", "/", 1)).is_dir()]
    if missing:
        raise StepFailedError(
            "ansible collections — missing after install: " + ", ".join(missing), 1
        )


def _pki_ensure_step() -> CommandStep:
    _render_pki_entities()
    return CommandStep("pki ensure", Command([*_PKI_PLAYBOOK], cwd=repo_root() / "ansible"))  # noqa: S607


def _commons_mq_platforms() -> set[str]:
    """Distinct MQ guest platforms among the commons VMs (svc/app run MQ; the probe
    runs the MQ exporters). Host-resolved via lab_guests (native-preferred, #276)."""
    platforms = lab_guests()
    return {platforms[host] for host in _commons_members() if host in platforms}


def _ensure_prereqs_for_commons(*, step: bool = False) -> None:
    """Ensure every fresh-volume prerequisite the commons (obs/site-obs.yml) provision
    needs (#343/#350). All live under build/ on the persistent volume, which a recreate
    wipes, so commons up regenerates them — idempotently, in dependency order:
      1. the MQ-for-Developers tarball(s) for the commons guest platforms
      2. Ansible galaxy collections (community.crypto — required by the PKI play)
      3. the PKI CA + entity keystores (the exporters consume these)
    MQ is a Python fetch; galaxy + PKI run through the step runner (progress/transcript).
    """
    ensure_mq_tarballs_for_platforms(
        _commons_mq_platforms(),
        DEFAULT_MQ_VERSION,
        mq_cache_dir(),
        fetch=_fetch_mq_tarball,
    )
    _execute("prerequisites", [_galaxy_install_step(), _pki_ensure_step()], step_mode=step)


# --- Stack-aware prerequisite ensures for the #350 bootstrap phase flow. -----------
#     The setup-based _ensure_prereqs above stays untouched (old commands still use
#     it). Bootstrap resolves prerequisites from the STACK and only for the phases
#     actually selected this run — each phase declares its prereq kinds as data in
#     phases.py (Phase.ensure); the dispatch below maps each name to its real I/O.
def _stack_mq_platforms(stack: Stack) -> set[str]:
    """Distinct MQ guest platforms a stack's provision installs MQ on.

    The MQ-for-Developers tarball is arch-specific, so we ensure one per distinct
    platform, host-resolved via lab_guests (native-preferred, #276) — the same
    source the setup path uses. Two cohorts run MQ and both need their tarball:
      - the stack's cluster (QM) member VMs (its groups' hosts), and
      - the commons SVC/app endpoints: every stack's provision playbook imports
        site-distributed-shared.yml, which runs mq-install on the svc/app hosts
        (the per-stack SVC counterparty QM + the requester app). These are Ubuntu
        (host-resolved), so on a cold cache — no prior `commons up` to leave the
        tarball behind in the shared build/cache — it is absent unless we fetch it
        here too (#407).
    obs/probe also land in _commons_mq_platforms, but they resolve to that same
    Ubuntu platform, so the union adds exactly the one Ubuntu tarball svc/app need.
    """
    members = stack_members(stack.name) or []
    platforms = lab_guests()
    member_platforms = {platforms[host] for host in members if host in platforms}
    return member_platforms | _commons_mq_platforms()


def _ensure_mq_artifacts_for_stack(stack: Stack) -> None:
    """Ensure the arch-correct MQ tarball(s) for a stack's cluster-node platforms.

    The stack counterpart of _ensure_mq_artifacts (which is setup-resolved). Version
    is the repo default — stacks carry no per-setup manifest pin in the #350 model.
    """
    ensure_mq_tarballs_for_platforms(
        _stack_mq_platforms(stack),
        DEFAULT_MQ_VERSION,
        mq_cache_dir(),
        fetch=_fetch_mq_tarball,
    )


def _ensure_prereqs_for_stack(stack: Stack, phase: Phase, *, step: bool) -> None:
    """Ensure the fresh-volume prerequisites a phase declares (phases.Phase.ensure),
    for a stack, before that phase's steps run (#350 Task 5).

    Dispatches each declared prereq kind in dependency order:
      boxes  -> build/register the local boxes for the stack's VMs (vms phase)
      mq     -> the MQ-for-Developers tarball(s) for the stack's platforms
      galaxy -> Ansible galaxy collections (community.crypto, needed by the PKI play)
      pki    -> the PKI CA + entity keystores (the exporters consume these too)
    The Python fetches (boxes, mq) run inline; galaxy + PKI run through the step
    runner (progress/transcript). Only kinds the phase declares run, so
    `--only observe` ensures only the exporter PKI."""
    kinds = phase.ensure
    if "boxes" in kinds:
        _ensure_local_boxes(all_vms(stack))
    if "mq" in kinds:
        _ensure_mq_artifacts_for_stack(stack)
    steps: list[CommandStep] = []
    if "galaxy" in kinds:
        steps.append(_galaxy_install_step())
    if "pki" in kinds:
        steps.append(_pki_ensure_step())
    if steps:
        _execute("prerequisites", steps, step_mode=step)
    if "galaxy" in kinds:
        # Hard-fail if a required collection did not actually land (#596) — the install
        # above can be a no-op on an existing cache when requirements.yml gains a collection.
        _verify_galaxy_collections()


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


def _obs_manifest_args() -> list[str]:
    shared = repo_root() / "manifests" / "_shared" / "observability.yaml"
    if not shared.exists():
        return []
    op = work("manifests", "_obs.overlay.json")
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(obs_overlay()))
    return ["-e", f"@{op}"]


def _obs_exporter_args() -> list[str]:
    # The probe runs one mq_prometheus pair PER STACK on the alloc ports (#423), no
    # longer pcmk-pinned. site-obs.yml loops the mq-exporter role over this list; the
    # per-stack QM/conn/port derivation is a pure function of topology (mqlab.scrape).
    from mqlab.scrape import lab_mq_exporters, mq_exporters_path

    path = mq_exporters_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(lab_mq_exporters())
    return ["-e", f"@{path}"]


def _vagrant_env() -> dict[str, str]:
    # Vagrant's per-machine state (the libvirt-domain <-> vagrant mapping + keys) is
    # SHARED, irreplaceable live-lab state — one lab, one instance — so it belongs in
    # build/state, not a per-worktree lab/.vagrant that dies with the worktree and
    # orphans the running lab. Redirect the dotfile via VAGRANT_DOTFILE_PATH so every
    # checkout/worktree drives the same lab. Resolved so all of them pass one canonical
    # path through the worktree's state/ symlink to the primary checkout (#355).
    return {"VAGRANT_DOTFILE_PATH": str(state("vagrant").resolve())}


commons_app = typer.Typer(
    help="shared commons VMs (obs + probe + svc + app) independently of any stack",
    no_args_is_help=True,
)
app.add_typer(commons_app, name="commons")

qm_app = typer.Typer(help="MQ queue managers (per-stack HA lifecycle)", no_args_is_help=True)
app.add_typer(qm_app, name="qm")

# vm / obs retain only their render + interactive utility subcommands after the #350
# cutover: the per-VM lifecycle moved to `bootstrap`/`teardown`. `vm inventory`/`roster`
# render the static maps the playbooks + scripts consume; `vm ssh` is interactive
# operator access. `obs targets`/`dashboard`/`net-state`/`reach-peers` are pure
# topology→file renders the observe phase and the host collector shell out to; `obs open`
# prints the Grafana URL. The setup-based `vm create/up/down/destroy/status/provision`
# and `obs up/status/instrument` commands are gone.
vm_app = typer.Typer(help="lab guest VM maps (inventory/roster) + ssh", no_args_is_help=True)
app.add_typer(vm_app, name="vm")

obs_app = typer.Typer(help="observability renders (targets/dashboard) + open", no_args_is_help=True)
app.add_typer(obs_app, name="obs")


@obs_app.command("targets")
def obs_targets(
    stack: str | None = typer.Option(
        None, "--stack", help="scope the exporter deployment list to one stack (#503)"
    ),
) -> None:
    """Render the Prometheus file_sd targets + the mq-exporter deployment list from topology.

    Renders three topology projections into build/work: the node file_sd targets,
    the per-stack ibmmq file_sd targets, and the per-stack mq-exporter deployment
    list (`mq_exporters`) that site-obs.yml loops the mq-exporter role over. The
    exporter list is folded in here — not a separate command — because the observe
    phase already runs `mqlab obs targets` as a render step (phases.py) and then
    hands the file to site-obs.yml as `-e @<file>`; keeping the render here pins the
    bootstrap `observe` call site to the same projection the `obs up` path uses (#434).
    """
    from mqlab.scrape import (
        lab_mq_exporters,
        lab_mq_scrape_targets,
        lab_scrape_targets,
        mq_exporters_path,
        mq_scrape_targets_path,
        scrape_targets_path,
    )

    deps = build_deps("obs-targets", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        # only the exporter deployment list is scoped to `stack`; the node/ibmmq
        # scrape-target files stay full-topology (a down target is benign) (#503)
        for renderer_fn, path_fn in (
            (lab_scrape_targets, scrape_targets_path),
            (lab_mq_scrape_targets, mq_scrape_targets_path),
            (lambda: lab_mq_exporters(stack), mq_exporters_path),
        ):
            text = renderer_fn()
            path = path_fn()
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
        # the per-stack cluster cockpit boards (#219/#279/#417/#287) — one per
        # provisioned stack, each under its own folder (#59)
        from mqlab.clusterboard import write_cluster_dashboards

        for cockpit in write_cluster_dashboards():
            deps.renderer.command(f"render -> {cockpit}")
            deps.transcript.write(f"render -> {cockpit}")
        # the per-stack messaging-flow boards (#431)
        from mqlab.messagingboard import write_messaging_dashboards

        write_messaging_dashboards()
        deps.renderer.command("render -> messaging boards (per stack)")
        deps.transcript.write("render -> messaging boards (per stack)")
        # the per-QM state boards (#489)
        from mqlab.qmboard import write_qm_dashboards

        write_qm_dashboards()
        deps.renderer.command("render -> per-QM boards")
        deps.transcript.write("render -> per-QM boards")
        # The Watcher — the lab-state front-door board (#488)
        from mqlab.watcherboard import lab_watcher_dashboard, watcher_dashboard_path

        watcher = watcher_dashboard_path()
        watcher.write_text(lab_watcher_dashboard())
        deps.renderer.command(f"render -> {watcher}")
        deps.transcript.write(f"render -> {watcher}")
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


dns_app = typer.Typer(help="DNS zone + BIND config renders (#476)", no_args_is_help=True)
app.add_typer(dns_app, name="dns")


@dns_app.command("render")
def dns_render() -> None:
    """Render BIND zone files + named.conf + host resolver facts into build/work/dns/ (#476)."""
    from mqlab.bind import lab_host_dns_facts, lab_render

    out_dir = work("dns")
    out_dir.mkdir(parents=True, exist_ok=True)
    files = lab_render()
    for name, content in files.items():
        (out_dir / name).write_text(content)
    (out_dir / "hostfacts.json").write_text(
        json.dumps(lab_host_dns_facts(), indent=2, sort_keys=True) + "\n"
    )
    typer.echo(f"rendered {len(files) + 1} DNS files -> {out_dir}")


rest_app = typer.Typer(help="Canonical published mqweb REST endpoints (#39)", no_args_is_help=True)
app.add_typer(rest_app, name="rest")


@rest_app.command("render")
def rest_render() -> None:
    """Render mqweb REST endpoints (both sites) to build/work/rest/endpoints.json (#39)."""
    from mqlab.rest import lab_rest_endpoints

    out_dir = work("rest")
    out_dir.mkdir(parents=True, exist_ok=True)
    recs = lab_rest_endpoints()
    (out_dir / "endpoints.json").write_text(json.dumps(recs, indent=2, sort_keys=True) + "\n")
    for rec in recs:
        for site, urls in rec["endpoints"].items():
            typer.echo(f"{rec['stack']:<16} {rec['kind']:<16} {site:<7} {' '.join(urls)}")
    typer.echo(f"rendered {len(recs)} REST surfaces -> {out_dir}")


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
    the svc-org QM (qm_svc), deduped across stacks, plus the fixed non-QM entities.
    QM CNs derive from each stack's #351 short token (<short>APP / <short>SVC); the
    CNs must match the SSLPEER/keystore labels the provision playbooks pass per QM.
    A reserved stack with no short contributes no QM CN. (#350)
    """
    from mqlab.stacks import lab_stacks

    seen_app: set[str] = set()
    seen_svc: set[str] = set()
    qm_entities: list[dict[str, Any]] = []

    for stack in lab_stacks().values():
        if not stack.short:
            continue
        app_cn = stack.qm.qm_app
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
        svc_cn = stack.qm.qm_svc
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


# GRAFANA_URL / WORKSTATION_GRAFANA_URL / RELAY_UNITS now live in mqlab.relay
# (imported at top) so the import-pure observe phase (phases.py) can share them
# without importing cli.py (circular). (#383)


def _obs_up_steps() -> list[CommandStep]:
    from mqlab.dashboard import dashboard_path, lab_dashboard
    from mqlab.inventory import inventory_path, lab_inventory
    from mqlab.scrape import (
        lab_mq_scrape_targets,
        lab_scrape_targets,
        mq_scrape_targets_path,
        scrape_targets_path,
    )

    # Render all the artifacts eagerly when the steps are built: the Prometheus
    # scrape targets (node + per-stack ibmmq, #423), the Ansible inventory the
    # provision step needs (mirrors dr-provision.sh), and the Grafana dashboard.
    targets = scrape_targets_path()
    targets.parent.mkdir(parents=True, exist_ok=True)
    targets.write_text(lab_scrape_targets())

    mq_targets = mq_scrape_targets_path()
    mq_targets.parent.mkdir(parents=True, exist_ok=True)
    mq_targets.write_text(lab_mq_scrape_targets())

    inv = inventory_path()
    inv.parent.mkdir(parents=True, exist_ok=True)
    inv.write_text(lab_inventory())

    dash = dashboard_path()
    dash.parent.mkdir(parents=True, exist_ok=True)
    dash.write_text(lab_dashboard())

    # the per-stack cluster cockpit boards (#219/#279/#417/#287) — one lab-<stack>-cluster
    # per provisioned stack, each under its own folder (#59)
    from mqlab.clusterboard import write_cluster_dashboards

    write_cluster_dashboards()

    # the per-stack messaging-flow boards (#431)
    from mqlab.messagingboard import write_messaging_dashboards

    write_messaging_dashboards()

    # the per-QM state boards (#489)
    from mqlab.qmboard import write_qm_dashboards

    write_qm_dashboards()

    # The Watcher — the lab-state front-door board (#488)
    from mqlab.watcherboard import lab_watcher_dashboard, watcher_dashboard_path

    watcher_dashboard_path().write_text(lab_watcher_dashboard())

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
                ["ansible-playbook", "site-obs.yml", *_obs_exporter_args(), *_obs_manifest_args()],  # noqa: S607
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
            Command(["sudo", "systemctl", "restart", *RELAY_UNITS]),  # noqa: S607
        ),
        CommandStep(
            # fail loud if the workstation-facing endpoint isn't actually serving —
            # don't report success while the browser path is dead (#264). -f makes
            # curl exit non-zero on any non-2xx or a dropped connection.
            "verify grafana reachable (workstation forward)",
            Command(["curl", "-fsS", "-m", "5", f"{WORKSTATION_GRAFANA_URL}/api/health"]),  # noqa: S607
        ),
    ]


@obs_app.command("open")
def obs_open() -> None:
    """Print the Grafana URL and how to reach it from your workstation."""
    typer.echo(f"Workstation: {WORKSTATION_GRAFANA_URL}/d/lab-watcher  (The Watcher — lab state)")
    typer.echo(f"In the VM:   {GRAFANA_URL}/d/lab-watcher  (direct to the obs guest)")
    typer.echo(
        f"Fleet — Node Health (#488 predecessor): {WORKSTATION_GRAFANA_URL}/d/lab-fleet-node"
    )
    typer.echo(f"Live tail:   {WORKSTATION_GRAFANA_URL}/explore  (pick the Loki datasource, e.g.")
    typer.echo('             query {unit="mqlab-requester"} and toggle Live)')
    typer.echo("")
    typer.echo("The forward is automatic — no manual tunnel. Lima forwards the VM's")
    typer.echo("port 3000 to your Mac's localhost:3000, and the vergil-portforward relay")
    typer.echo("bridges that to the obs guest. Just browse localhost:3000 (anonymous —")
    typer.echo("no login, #258). If it drops after an 'obs up', the relay was wedged by a")
    typer.echo("grafana restart; 'mqlab obs up' now re-heals it as its last step (#264).")


# ---------------------------------------------------------------------------
# commons — shared commons VMs (obs + probe + svc + app) independently of stacks
# ---------------------------------------------------------------------------

# The obs VMs that _obs_up_steps hardcodes in its monitoring-create vagrant up step.
# Used by _commons_up_steps to identify which extra commons members need a separate
# vagrant up call (svc-sim, app-client, or any future addition to the commons groups).
_OBS_UP_MEMBERS = frozenset({"obs", "mon-probe"})


def _commons_up_steps() -> list[CommandStep]:
    """Build step list for `commons up`.

    Reuses _obs_up_steps() for the observability render + vagrant up obs/probe +
    site-obs.yml + host-obs.yml + relay heal + grafana verify steps, then appends
    a vagrant up step for any extra commons members not covered by _obs_up_steps
    (i.e. svc-sim and app-client when present in the commons topology).

    Scope: commons up provisions shared infra VMs and observability only.  The per-
    stack svc QM and app instance (MQ workload on svc-sim/app-client) are provisioned
    by a stack's bootstrap provision phase — NOT here.
    """
    steps = _obs_up_steps()
    extra = [m for m in _commons_members() if m not in _OBS_UP_MEMBERS]
    if extra:
        steps.append(
            CommandStep(
                "commons create (svc/app)",
                Command(  # noqa: S607
                    ["vagrant", "up", *extra],
                    cwd=repo_root() / "lab",
                    env=_vagrant_env(),
                ),
            )
        )
    return steps


@commons_app.command("up")
def commons_up(step: _StepFlag = False) -> None:
    """Bring up all commons VMs and provision observability (site-obs.yml)."""
    _prepare_lab()  # commons up shells vagrant — gate + render (#276)
    _ensure_prereqs_for_commons(step=step)
    _execute("commons-up", _commons_up_steps(), step_mode=step)


@commons_app.command("status")
def commons_status() -> None:
    """Show commons health (topology joined with live virsh state)."""
    guests = _commons_members()
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps("commons-status", timestamp)
    try:
        code = vm_status_core(deps.runner, deps.renderer, deps.transcript, guests=guests)
    finally:
        deps.transcript.close()
    if code != 0:
        raise typer.Exit(code=code)


@commons_app.command("down")
def commons_down(step: _StepFlag = False) -> None:
    """Destroy all commons VMs (obs + probe + svc + app)."""
    _execute_stateful("commons-down", _commons_members(), _plan_destroy, step_mode=step)


_VIRSH = ["virsh", "-c", "qemu:///system"]


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


def _forceoff_step(g: str) -> CommandStep:
    return CommandStep(f"{g} force-off", Command([*_VIRSH, "destroy", f"lab_{g}"]))  # noqa: S607


def _undefine_step(g: str) -> CommandStep:
    # Remove the domain + per-guest overlay disk + UEFI nvram (base box untouched).
    cmd = Command([*_VIRSH, "undefine", f"lab_{g}", "--remove-all-storage", "--nvram"])  # noqa: S607
    return CommandStep(f"{g} undefine", cmd)


# State-aware destroy planner (#99): given the live state, act only where needed and
# emit advisory notes for the rest. Idempotency = looking before you leap. (The
# create/up/down planners retired with the vm lifecycle commands in #350; bootstrap's
# phases own bring-up now, teardown + commons-down own destroy.)
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
    # Auto-generated lab secret -> its value, injected into the provision playbook
    # env (the roles read e.g. PCMK_HACLUSTER_PASSWORD via lookup('env', ...)). No
    # hiding: the lab is a throwaway illusion, so this echoes + tees like any other
    # step. lab-secret.sh generates+persists once. (#373: restores the pre-#350 path
    # the cutover dropped with the old `vm provision`.)
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


@vm_app.command("ssh")
def vm_ssh(guest: str) -> None:
    """Open an interactive shell on one guest (vagrant ssh)."""
    _prepare_lab()  # ssh loads the Vagrantfile — ensure the resolved topology exists (#276)
    _ssh_into(guest)


# --- qm: the MQ queue-manager lifecycle, dispatched off the stack's verbs (#350) --
# create/destroy run the stack's qm-* playbooks (the client-reproducible deliverable);
# up/down/status are direct, streamed shell ops on the stack's cluster first node
# (pcs for pcmk, rdqm*/systemctl for the others). The verb implementations live on
# the canonical Stack (stack.verbs), so a single dispatch covers every mechanism.


def _stack_qm_or_exit(stack_name: str) -> Stack:
    stack = lab_stacks().get(stack_name)
    if stack is None:
        typer.echo(f"no stack named {stack_name!r} — see mqlab status", err=True)
        raise typer.Exit(code=2)
    if not stack.short:
        typer.echo(f"stack {stack_name} has no qm config", err=True)
        raise typer.Exit(code=2)
    return stack


def _render_inventory(deps: Deps) -> None:
    # The static map ansible needs to reach the hosts (#101). Cheap; always fresh.
    inv = inventory_path()
    inv.parent.mkdir(parents=True, exist_ok=True)
    inv.write_text(lab_inventory())
    deps.renderer.note(f"rendered {inv}")
    deps.transcript.write(f"rendered {inv}")


def _qm_playbook(stack: Stack, playbook: str, verb: str) -> None:
    # create/destroy: pre-flight members running -> render inventory -> run the play.
    qm = stack.qm
    members = stack_members(stack.name) or []
    deps = build_deps(verb, datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        states = _probe_states(deps)
        down = [m for m in members if classify(states, m) != RUNNING]
        if down:
            note = f"{', '.join(down)}: not running — run mqlab bootstrap {stack.name} first"
            deps.renderer.note(note)
            deps.transcript.write(note)
            raise typer.Exit(code=3)
        _render_inventory(deps)
        step = CommandStep(
            f"{stack.name} {verb}",
            Command(
                [
                    "ansible-playbook",
                    playbook,
                    "-e",
                    f"qm_name={qm.qm_app}",
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
                    "-e",
                    f"svc_req_queue={qm.req_queue}",
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


def _qm_cluster_cmd(stack: Stack, shell_cmd: str, verb: str) -> None:
    # up/down/status: a single streamed shell op on the stack's cluster first node
    # (pcs for pcmk, rdqm*/systemctl for the others). No pre-flight — if the cluster
    # is unreachable, ansible's own error speaks (#109). cluster_group is the correct
    # probe target — NOT groups[0], which may be a SAN host with no MQ tooling.
    group = stack.cluster_group
    deps = build_deps(verb, datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        _render_inventory(deps)
        step = CommandStep(
            f"{stack.name} {verb}",
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


def _qm_script(stack: Stack, script: str, verb: str) -> None:
    # Run a lab script with the rdqm-qm-create contract: QM, the single data-plane
    # floating IP (RDQM allows one FIP per QM, #216 spike), the counterparty CONNAME,
    # and the shared counterparty QM + this stack's request queue on it (#446). The
    # partner reaches us over net-ext by per-node CONNAME list, not a second VIP.
    qm = stack.qm
    argv = [
        "bash",
        str(lab_script(script)),
        qm.qm_app,
        qm.vip,
        qm.svc_conn or "",
        qm.qm_svc,
        qm.req_queue,
    ]
    deps = build_deps(verb, datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        step = CommandStep(f"{stack.name} {verb}", Command(argv))  # noqa: S607
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


def _qm_dispatch(stack_name: str, verb: str) -> None:
    # Validate the stack (exists + has a QM) for clean exit-2 messages, then resolve
    # the stack's implementation of the verb from its verbs dict and run it. The verb
    # kinds (playbook/pcs/cmd/script) mirror the per-mechanism shapes (#202, #216).
    stack = _stack_qm_or_exit(stack_name)
    qm = stack.qm
    impl = stack.verbs.get(verb)
    if not impl:
        typer.echo(f"stack {stack_name} does not implement verb {verb!r}", err=True)
        raise typer.Exit(code=2)
    [(kind, value)] = impl.items()
    if kind == "playbook":
        _qm_playbook(stack, value, verb)
    elif kind == "pcs":
        _qm_cluster_cmd(stack, f"pcs {value}", verb)
    elif kind == "cmd":
        _qm_cluster_cmd(stack, str(value).format(qm=qm.qm_app, vip=qm.vip), verb)
    elif kind == "script":
        _qm_script(stack, str(value), verb)
    else:  # pragma: no cover - unknown kinds are a topology error
        typer.echo(f"qm {verb}: unknown stack verb kind {kind!r}", err=True)
        raise typer.Exit(code=2)


@qm_app.command("create")
def qm_create(stack: str) -> None:
    """Create the queue manager + its HA resources (stack-dispatched)."""
    _qm_dispatch(stack, "qm-create")


@qm_app.command("destroy")
def qm_destroy(stack: str) -> None:
    """Remove the queue manager + its HA resources."""
    _qm_dispatch(stack, "qm-destroy")


@qm_app.command("up")
def qm_up(stack: str) -> None:
    """Start the queue manager."""
    _qm_dispatch(stack, "qm-up")


@qm_app.command("down")
def qm_down(stack: str) -> None:
    """Stop the queue manager (HA intact)."""
    _qm_dispatch(stack, "qm-down")


@qm_app.command("status")
def qm_status(stack: str) -> None:
    """Show the queue manager's HA resource state."""
    _qm_dispatch(stack, "qm-status")


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
        # The vms phase's `vagrant up` step carries env=None, so it inherits this
        # process's environment. Export the vagrant env up front so bring-up sees
        # VAGRANT_DOTFILE_PATH (the shared #355 dotfile, so it drives the one
        # canonical lab even from a worktree that never created it). Mirrors
        # _ssh_into's os.environ.update(_vagrant_env()); the secret injection below
        # is the same sequencer-owns-the-env pattern (#373).
        os.environ.update(_vagrant_env())
        # Refresh the ansible inventory before probing/provisioning (#377): the
        # provision/observe playbooks target the #350 stack-aggregate groups
        # (e.g. hosts: pcmk_ubuntu), and _probe_all + provision run ansible against
        # build/work/inventory.ini. A stale file (pre-cutover, missing the aggregate
        # groups) silently no-ops those plays (acl install, cold-boot guard). Always
        # render fresh here in the sequencer — phases.py stays pure.
        _render_inventory(deps)
        states = _probe_all(deps, stack)
        selected = _select_phases(stack, states, only=only, from_phase=from_phase)
        if not selected:
            deps.renderer.note(f"{stack_name}: already satisfied — nothing to do")
            return
        # Inject the stack's secrets as env vars for the provision playbook (#373):
        # its roles read e.g. PCMK_HACLUSTER_PASSWORD via lookup('env', ...). The I/O
        # (lab-secret.sh) lives here in the sequencer, not in pure phases.py — mirrors
        # os.environ.update(_vagrant_env()). The #350 cutover dropped this with the old
        # `vm provision`; without it the hacluster password is empty and chpasswd fails.
        if any(phase.name == "provision" for phase in selected):
            for secret in stack.secrets:
                os.environ[secret.upper()] = _source_secret(deps, secret)
        for phase in selected:  # one phase at a time so a failure names its phase
            try:
                # Ensure this phase's fresh-volume prerequisites first (#350 Task 5),
                # only for the phases actually selected this run.
                _ensure_prereqs_for_stack(stack, phase, step=step)
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


def _other_stacks_up(deps: Deps, exclude: str) -> bool:
    """True iff any member VM of a non-excluded, non-reserved stack is live.

    Probes `virsh list --all` once and checks each eligible stack's members
    against the result. "Reserved" means no cluster_group OR no members — those
    stacks have no real VMs to query and are skipped.

    Used by `_teardown_run` as a reference count: when the result is False the
    caller is the last stack down and commons VMs can be reclaimed.
    """
    stacks = lab_stacks()
    states = _probe_states(deps)
    for name, stack in stacks.items():
        if name == exclude:
            continue
        if stack.cluster_group is None:
            continue  # reserved stack — no real VMs
        members = stack_members(name) or []
        if not members:
            continue  # reserved/empty stack — skip
        if any(is_live(states, member) for member in members):
            return True
    return False


def _teardown_run(stack_name: str, *, commons: bool, step: bool) -> None:
    """Destroy a stack's member VMs; reclaim shared commons when last stack out.

    Logic:
    - Resolves the stack (exit 2 on unknown).
    - Plans member-VM destroy steps from the live domain states.
    - Decides commons fate: destroy if --commons OR no other stack is still up.
      When commons are kept a note is emitted so the operator knows why.
    - Runs all accumulated steps in one pass (fail-loud on StepFailedError).

    Extension point for Task 11: per-stack commons-instance cleanup (svc QM
    dltmqm, exporter unit, scrape-target entry) belongs in a
    `_teardown_stack_commons_instances(stack, deps)` helper inserted here
    before the commons-VM destroy block. Today's commons are single-instance
    shared VMs only.
    """
    stack = _lookup_stack_or_exit(stack_name)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps("teardown", timestamp)
    try:
        members = stack_members(stack.name) or []
        all_stack_vms = all_vms(stack)
        commons_vms = [vm for vm in all_stack_vms if vm not in members]

        states = _probe_states(deps)

        member_steps, member_notes = _plan_destroy(members, states)
        for note in member_notes:
            deps.renderer.note(note)

        destroy_commons = commons or not _other_stacks_up(deps, exclude=stack.name)

        commons_steps: list[CommandStep] = []
        if destroy_commons:
            raw_commons, commons_notes = _plan_destroy(commons_vms, states)
            for note in commons_notes:
                deps.renderer.note(note)
            # Inject "commons destroy" into every per-guest step label so tests
            # (and operators) can distinguish them from member steps.
            commons_steps = [
                CommandStep(f"commons destroy: {s.label}", s.command) for s in raw_commons
            ]
        else:
            deps.renderer.note(
                "commons VMs kept — another stack is still up (use --commons to force removal)"
            )

        all_steps = member_steps + commons_steps
        run_steps(
            all_steps,
            runner=deps.runner,
            renderer=deps.renderer,
            transcript=deps.transcript,
            step_mode=step,
            pauser=deps.pauser,
        )
    except StepFailedError as exc:
        raise typer.Exit(code=exc.exit_code) from exc
    except NoTTYError as exc:
        deps.renderer.error(str(exc))
        raise typer.Exit(code=2) from exc
    finally:
        deps.transcript.close()


@app.command("teardown")
def teardown(  # pragma: no cover - thin delegator; logic covered via _teardown_run
    stack_name: Annotated[str, typer.Argument(help="stack to tear down (e.g. rdqm-rhel)")],
    commons: Annotated[
        bool, typer.Option("--commons", help="also destroy shared commons VMs")
    ] = False,
    step: _StepFlag = False,
) -> None:
    """Destroy a stack's VMs; shared commons only when last stack down (or --commons)."""
    _teardown_run(stack_name, commons=commons, step=step)


def _status_one(stack: Stack, deps: Deps) -> None:
    """Render phase completion for a single non-reserved stack.

    Probes the live world once via _probe_all, then maps each Phase to ✓/✗ from
    phase.satisfied(stack, states). Renders a Rich table: Phase | Status.
    Fail-loud: real probe results surface; nothing is swallowed.
    """
    from rich.table import Table

    states = _probe_all(deps, stack)
    table = Table(title=f"stack: {stack.name}")
    table.add_column("Phase")
    table.add_column("Status")
    for phase in PHASES:
        ok = phase.satisfied(stack, states)
        mark = "✓" if ok else "✗"
        table.add_row(phase.name, mark)
    deps.renderer.table(table)


def _status_run(stack_name: str | None) -> None:
    """Render phase completion for one stack (if named) or all non-reserved stacks.

    A reserved stack is one whose cluster_group is None (e.g. nativeha-ubuntu):
    it has no provision playbook and no real VMs to probe.

    - Named reserved stack: emit a note and return without probing.
    - Named non-reserved stack: probe once, render phases.
    - No arg: iterate all non-reserved stacks, probing each once.
    """
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps("status", timestamp)
    try:
        if stack_name is not None:
            stack = _lookup_stack_or_exit(stack_name)
            if stack.cluster_group is None:
                deps.renderer.note(
                    f"{stack.name}: reserved stack — no provision playbook or cluster VMs"
                )
                return
            _status_one(stack, deps)
        else:
            stacks = lab_stacks()
            for stack in stacks.values():
                if stack.cluster_group is None:
                    continue  # skip reserved stacks in the all-stacks view
                _status_one(stack, deps)
    finally:
        deps.transcript.close()


@app.command("status")
def status(  # pragma: no cover - thin delegator; logic covered via _status_run
    stack_name: Annotated[
        str | None,
        typer.Argument(help="stack to show (e.g. pcmk-ubuntu); omit to show all stacks"),
    ] = None,
) -> None:
    """Show phase completion (net/vms/provision/observe) for a stack or all stacks."""
    _status_run(stack_name)


def main() -> None:
    app()
