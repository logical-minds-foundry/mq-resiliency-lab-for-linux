"""mqlab — the operator orchestrator CLI (Typer + Rich). A thin veneer (spec §2)."""

from __future__ import annotations

import json
import os
import platform
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
import yaml
from rich.console import Console

from mqlab import buildenv, coldboot, hugepages, parity, perfdiff, topology, venvsync
from mqlab.artifact import (
    download_mq_tarball,
    ensure_mq_tarballs_for_boxes,
)
from mqlab.buildenv import BuildEnvError
from mqlab.doctor import Check, run_checks, summarise
from mqlab.fleet import parse_domain_states
from mqlab.hostfacts import probe
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
    san_deb_cache_dir,
    state,
    work,
)
from mqlab.pauser import NoTTYError, TTYPauser
from mqlab.perf import NullSink
from mqlab.perfrun import PREFLIGHT, BootstrapPerf, ansible_task_outcomes, prereq_phase
from mqlab.phases import (
    OBS_GROUP,
    PHASES,
    _commons_members,
    _non_mq_commons_hosts,
    all_vms,
    build_states,
    first_unsatisfied,
    group_hosts,
)
from mqlab.platforms import PlatformError, ensure_resolved
from mqlab.relay import GRAFANA_URL, RELAY_UNITS, WORKSTATION_GRAFANA_URL
from mqlab.render import Renderer
from mqlab.retired_boxes import RETIRED_BOX_NAMES
from mqlab.roster import lab_roster, roster_path
from mqlab.runner import Command, SubprocessRunner
from mqlab.sandeb import ensure_san_debs, observed_target_kernel
from mqlab.stacks import (
    lab_stacks,
    rhel_stack_unsupported_reason,
    stack_members,
    stack_san_targets,
)
from mqlab.transcript import Transcript, transcript_path
from mqlab.versions import load_catalog, node_boxes
from mqlab.vmstatus import vm_status_core

if TYPE_CHECKING:
    from collections.abc import Callable

    from mqlab.orchestrator import Pauser
    from mqlab.perf import PerfSink
    from mqlab.phases import Phase
    from mqlab.runner import CommandRunner
    from mqlab.stacks import Stack
    from mqlab.versions import BoxEntry


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


def _execute(
    verb: str,
    steps: list[CommandStep],
    *,
    step_mode: bool,
    before: Callable[[Deps], None] | None = None,
    perf: PerfSink | None = None,
) -> None:
    """Run `steps` under one transcript. `before` (optional) runs first with the same
    deps — e.g. the huge-page reservation `commons up` needs ahead of its `vagrant up`
    (#1241) — and fails the verb the same loud way a step does. `perf` (optional)
    receives each step's timing, grouped by the step's own `phase` (#1248)."""
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps(verb, timestamp)
    try:
        if before is not None:
            before(deps)
        run_steps(
            steps,
            runner=deps.runner,
            renderer=deps.renderer,
            transcript=deps.transcript,
            step_mode=step_mode,
            pauser=deps.pauser,
            perf=perf if perf is not None else NullSink(),
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


def _galaxy_install_step(phase: str = "") -> CommandStep:
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
    cmd = Command(argv, cwd=repo_root())  # noqa: S607
    return CommandStep("ansible collections", cmd, phase=phase)


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


def _pki_ensure_step(phase: str = "") -> CommandStep:
    _render_pki_entities()
    cmd = Command([*_PKI_PLAYBOOK], cwd=repo_root() / "ansible")  # noqa: S607
    return CommandStep("pki ensure", cmd, phase=phase)


def _lab_node_boxes() -> dict[str, BoxEntry]:
    """Every topology node's box, from the version layer (versions.node_boxes)."""
    return node_boxes(topology.load(), load_catalog())


def _commons_mq_boxes() -> set[BoxEntry]:
    """Distinct MQ guest boxes among the commons VMs (svc/app run MQ; the probe runs the
    MQ exporters). Resolved through the version layer (the infra OS, host-arch tracking).

    Excludes the infrastructure-only commons groups (_non_mq_commons_hosts, #634):
    infra is a DNS/core-services box that runs no MQ, so it needs no MQ tarball. The
    vms phase still boots those VMs (via _commons_members); only the media enum skips
    them.
    """
    boxes = _lab_node_boxes()
    excluded = _non_mq_commons_hosts()
    mq_hosts = [h for h in _commons_members() if h in boxes and h not in excluded]
    return {boxes[h] for h in mq_hosts}


def _ensure_prereqs_for_commons(*, step: bool = False) -> None:
    """Ensure every fresh-volume prerequisite the commons (obs/site-obs.yml) provision
    needs (#343/#350). All live under build/ on the persistent volume, which a recreate
    wipes, so commons up regenerates them — idempotently, in dependency order:
      1. the MQ-for-Developers tarball(s) for the commons guest boxes
      2. Ansible galaxy collections (community.crypto — required by the PKI play)
      3. the PKI CA + entity keystores (the exporters consume these)
    MQ is a Python fetch; galaxy + PKI run through the step runner (progress/transcript).
    """
    ensure_mq_tarballs_for_boxes(
        _commons_mq_boxes(),
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
def _stack_mq_boxes(stack: Stack) -> set[BoxEntry]:
    """Distinct MQ guest boxes a stack's provision installs MQ on.

    The MQ-for-Developers tarball is per OS family + arch, so we ensure one per distinct
    box family/arch, resolved through the version layer (versions.node_boxes). Two
    cohorts run MQ and both need their tarball:
      - the stack's cluster (QM) member VMs (its groups' hosts), and
      - the commons SVC/app endpoints: every stack's provision playbook imports
        site-distributed-shared.yml, which runs mq-install on the svc/app hosts
        (the per-stack SVC counterparty QM + the requester app). These run the infra
        Ubuntu, so on a cold cache — no prior `commons up` to leave the tarball behind
        in the shared build/cache — it is absent unless we fetch it here too (#407).
    obs/probe also land in _commons_mq_boxes; they resolve to the same Ubuntu tarball,
    so the union adds exactly the one Ubuntu tarball svc/app need.
    """
    members = stack_members(stack.name) or []
    boxes = _lab_node_boxes()
    member_boxes = {boxes[host] for host in members if host in boxes}
    return member_boxes | _commons_mq_boxes()


def _ensure_mq_artifacts_for_stack(stack: Stack) -> None:
    """Ensure the arch-correct MQ tarball(s) for a stack's cluster-node boxes.

    The stack counterpart of _ensure_mq_artifacts (which is setup-resolved). Version
    is the repo default — stacks carry no per-setup manifest pin in the #350 model.
    """
    ensure_mq_tarballs_for_boxes(
        _stack_mq_boxes(stack),
        DEFAULT_MQ_VERSION,
        mq_cache_dir(),
        fetch=_fetch_mq_tarball,
    )


def _ensure_san_debs_for_stack(stack: Stack) -> None:
    """Pre-cache the SAN install-half debs for a stack that has SAN targets (#796).

    A no-op for a stack without SAN targets (rdqm / native-ha): like the MQ ensure,
    this fires for every stack's provision phase but resolves to real work only where
    it applies (the pacemaker-san stack). The kernel keyed for the one kernel-coupled
    package (linux-modules-extra) is the SAN base box's *observed* kernel — recorded by
    the drbd-san role on a prior rebuild — falling back to the controller's own kernel
    only on the very first rebuild, before the box has ever booted (#816). This makes
    the offline fast-path fire on every rebuild after the first, instead of missing
    forever because the controller's kernel drifts from the cloud image's. Pre-caching
    is best-effort: an unreachable package is reported, not fatal, because the roles
    carry a network fallback."""
    if not stack_san_targets(stack.name):
        return
    kernel = observed_target_kernel(san_deb_cache_dir()) or platform.uname().release
    results = ensure_san_debs(san_deb_cache_dir(), kernel)
    staged = sorted(pkg for pkg, status in results.items() if status != "unavailable")
    fallback = sorted(pkg for pkg, status in results.items() if status == "unavailable")
    typer.echo(f"SAN install-half debs staged for kernel {kernel}: {', '.join(staged) or 'none'}")
    if fallback:
        typer.echo(f"  network fallback at install for: {', '.join(fallback)}")


# Controller-local prereq kinds whose ensure is idempotent and whose output no phase
# step mutates: once one succeeds in a bootstrap run, a later phase re-declaring it
# would redo identical work, so the run ensures it once (#1248). See
# _ensure_prereqs_for_stack for why the second `pki ensure` is provably redundant.
_ONCE_PER_RUN_KINDS = ("galaxy", "pki")


def _ensure_prereqs_for_stack(
    stack: Stack,
    phase: Phase,
    *,
    step: bool,
    perf: BootstrapPerf | None = None,
    ensured: set[str] | None = None,
) -> None:
    """Ensure the fresh-volume prerequisites a phase declares (phases.Phase.ensure),
    for a stack, before that phase's steps run (#350 Task 5).

    Dispatches each declared prereq kind in dependency order:
      boxes  -> build/register the local boxes for the stack's VMs (vms phase)
      mq     -> the MQ-for-Developers tarball(s) for the stack's boxes
      san    -> the SAN install-half debs (only a stack with SAN targets; #796)
      galaxy -> Ansible galaxy collections (community.crypto, needed by the PKI play)
      pki    -> the PKI CA + entity keystores (the exporters consume these too)
    The Python fetches (boxes, mq) run inline; galaxy + PKI run through the step
    runner (progress/transcript). Only kinds the phase declares run, so
    `--only observe` ensures only the exporter PKI.

    `perf` (#1248) times every ensure as a step of the `prereq:<phase>` report phase,
    so the box ensure and the PKI play show in the phase table.

    `ensured` (#1248) is the bootstrap run's record of the once-per-run kinds already
    ensured. Provision and observe BOTH declare `pki`, so a full bootstrap used to run
    the ~52s PKI play twice. The second run is provably redundant: the play is
    controller-local and idempotent, its only input (build/work/pki/entities.json) is
    rendered from the topology, which is the same within one run, and nothing between
    the two runs writes its outputs — the provision playbooks only READ the
    lab-pki keystores (pki-distribute, mq-nativeha tls), and the RDQM replication PKI
    lives in its own `pki/rdqm-repl` tree. So a run that already ensured a kind skips
    it. A fresh run (`--only observe`, `--from observe`, a resume) starts with an empty
    record and still ensures PKI. A kind is recorded only after its ensure succeeded."""
    kinds = phase.ensure
    report_phase = prereq_phase(phase.name)
    done = ensured if ensured is not None else set()

    def timed(label: str, fn: Callable[[], None]) -> None:
        if perf is None:
            fn()
        else:
            perf.timed(report_phase, label, fn)

    if "boxes" in kinds:
        # #858: BEFORE building/registering boxes and running `vagrant up`, forget any
        # guest whose cached box_meta names a box the topology has since repointed away
        # from — otherwise the stale box_meta wins over the Vagrantfile's node.vm.box and
        # `vagrant up` boots the OLD box. #636 handles this on teardown; this handles the
        # repoint-then-bootstrap-without-teardown path.
        timed("reconcile box meta", lambda: _reconcile_box_meta(all_vms(stack), step=step))
        timed("box ensure", lambda: _ensure_local_boxes(all_vms(stack)))
    if "mq" in kinds:
        timed("mq artifacts", lambda: _ensure_mq_artifacts_for_stack(stack))
    if "san" in kinds:
        timed("san debs", lambda: _ensure_san_debs_for_stack(stack))
    once = [k for k in _ONCE_PER_RUN_KINDS if k in kinds]
    for kind in (k for k in once if k in done):
        msg = (
            f"{phase.name} prerequisites: {kind} already ensured earlier in this run "
            "— skipped (#1248)"
        )
        typer.echo(msg)
        if perf is not None:
            perf.record.note(msg)
    todo = [k for k in once if k not in done]
    steps: list[CommandStep] = []
    if "galaxy" in todo:
        steps.append(_galaxy_install_step(phase=report_phase))
    if "pki" in todo:
        steps.append(_pki_ensure_step(phase=report_phase))
    if steps:
        _execute(
            "prerequisites",
            steps,
            step_mode=step,
            perf=perf.record if perf is not None else None,
        )
    if "galaxy" in todo:
        # Hard-fail if a required collection did not actually land (#596) — the install
        # above can be a no-op on an existing cache when requirements.yml gains a collection.
        _verify_galaxy_collections()
    done.update(todo)


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


def _emit_cold_boot_nudge() -> None:
    """Surface the banded cold-boot staleness NOTICE when there is one (epic
    .github#91 T6). Advisory only — never blocks, never raises."""
    message = coldboot.nudge()
    if message:
        typer.echo(message)


@app.command("doctor")
def doctor() -> None:
    """Check this host can run the lab (arch, KVM, required tools)."""
    _emit_cold_boot_nudge()
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
    """Wire build/; on failure print buildenv's diagnosis (git's own stderr, #1261) and exit 1."""
    try:
        buildenv.ensure(repo_root())
    except BuildEnvError as exc:
        typer.echo(f"mqlab: cannot wire build/: {exc}", err=True)
        raise typer.Exit(code=1) from exc


# (group, command) leaves that skip the build/ check (#1261). Each entry must PROVABLY
# write nothing under build/ (no transcript, no render file) and need no git — otherwise
# a first run in a fresh worktree would create a real local build/state and poison the
# shared-bucket symlinks (#304). Keep it explicit and small; justify every addition:
# - obs net-state: renders `virsh net-list` + lab/networks/*.xml to stdout only; the
#   host publish script (net-state-publish.sh) runs it at the end of observe, where a
#   transient git failure must not fail an otherwise-green bootstrap.
_BUILD_FREE: frozenset[tuple[str, str]] = frozenset({("obs", "net-state")})
_BUILD_FREE_GROUPS = frozenset(group for group, _ in _BUILD_FREE)


@app.callback()
def _root(ctx: typer.Context) -> None:
    """Wire the build/ buckets before *any* lab command runs (#304).

    Most verbs touch buckets only as a side effect (e.g. every command writes a transcript to
    build/state/runs/), so wiring must happen before the body — otherwise the first command in a
    fresh worktree creates a real local build/state and poisons the cache/state symlinks. Skip the
    `build` group: it manages bucket lifecycle explicitly, and `build path` is a shell hot-path.
    A group holding a `_BUILD_FREE` leaf defers the decision to its own callback
    (`_group_build_check`), which alone can see the leaf command name."""
    sub = ctx.invoked_subcommand
    if sub and sub != "build" and sub not in _BUILD_FREE_GROUPS:
        _build_ensure()


def _group_build_check(ctx: typer.Context) -> None:
    """Group-level half of `_root`: wire build/ unless the leaf is in `_BUILD_FREE`."""
    leaf = ctx.invoked_subcommand
    if leaf and (ctx.info_name, leaf) not in _BUILD_FREE:
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
        try:
            path = _build_bucket_path(bucket)
        except BuildEnvError as exc:  # a git failure: print its diagnosis, not a traceback
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc
        typer.echo(f"{bucket:6} {kind:14} {path}")


@build_app.command("migrate")
def build_migrate(dry_run: Annotated[bool, typer.Option("--dry-run")] = False) -> None:
    """Move existing top-level build/ contents into buckets + rename the per-host box
    cache to the arch-suffixed `<box>-<arch>.box` scheme (idempotent, #103 D5)."""
    for src, dst in buildenv.migrate(repo_root(), dry_run=dry_run):
        typer.echo(f"{'PLAN' if dry_run else 'MOVED'} {src} -> {dst}")
    for src, dst in box.migrate_box_cache(dry_run=dry_run):
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


@obs_app.callback()
def _obs_root(ctx: typer.Context) -> None:
    _group_build_check(ctx)  # `obs net-state` is build-free; every other obs verb wires build/


# logsearch tier CLI (OpenSearch + Dashboards): status/open/snapshot/restore. Its
# own module owns the Typer group + pure helpers (epic .github#149, Task 11), mirroring
# how the box sub-app lives in box.py; here we just mount it under `mqlab logsearch`.
from mqlab import logsearch  # noqa: E402

app.add_typer(logsearch.app, name="logsearch")


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
def obs_dashboard(
    portable: bool = typer.Option(
        False,
        "--portable",
        help="also render the datasource-portable work-edition boards to "
        "build/work/grafana/work-edition/ (#963)",
    ),
) -> None:
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
        # the datasource-portable work-edition boards (#963) — rendered only on
        # request, into their own build/work/grafana/work-edition/ tree
        if portable:
            from mqlab.workboards import write_work_dashboards

            for board in write_work_dashboards(work("grafana", "work-edition")):
                deps.renderer.command(f"render -> {board}")
                deps.transcript.write(f"render -> {board}")
    finally:
        deps.transcript.close()


@obs_app.command("net-state")
def obs_net_state() -> None:
    """Emit lab_network_state textfile metrics from `virsh net-list --all` (run on the host).

    Build-free (`_BUILD_FREE`, #1261): no transcript, nothing under build/, so it runs
    without the git-based build/ check. A failed virsh fails loud rather than rendering
    every net as an (indistinguishable) absent 0."""
    from mqlab.netsel import lab_net_names, parse_net_states
    from mqlab.netstate import render_net_state_prom

    captured: list[str] = []
    rc = _virsh_runner().run(Command([*_VIRSH, "net-list", "--all"]), captured.append)  # noqa: S607
    if rc != 0:
        typer.echo(f"virsh net-list failed (exit {rc}):", err=True)
        for line in captured:
            typer.echo(line, err=True)
        raise typer.Exit(code=1)
    states = parse_net_states("\n".join(captured))
    typer.echo(render_net_state_prom(lab_net_names(), states), nl=False)


def _virsh_runner() -> CommandRunner:  # seam: tests swap in a RecordingRunner
    return SubprocessRunner()


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


def _logsearch_fanout_path() -> Path:
    """The fleet-wide fan-out gate file (#832). group_vars/all/logsearch.yml reads it
    with an absence-tolerant lookup, so its presence turns Alloy's #831 fan-out ON
    fleet-wide and its absence leaves fan-out inert."""
    return work("logsearch", "fanout.json")


def _render_logsearch_fanout() -> Path:
    """Write build/work/logsearch/fanout.json enabling fleet-wide Alloy->OpenSearch
    fan-out at the topology-derived Data Prepper endpoint (obs mgmt IP : 21892 — the
    log-search tier was consolidated onto obs in #1179). Mirrors _render_reach_peers —
    the idiomatic 'rendered gate file' seam."""
    import json as _json

    import yaml as _yaml

    from mqlab.logsearch import fanout_gate

    topo = _yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    path = _logsearch_fanout_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(fanout_gate(topo), indent=2) + "\n")
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

    # The fleet-wide Alloy->OpenSearch fan-out gate (#832): its endpoint is Data Prepper
    # on obs (log-search tier consolidated onto obs, #1179). Rendered eagerly here — like
    # the targets/inventory above — so group_vars/all/logsearch.yml reads it and turns the
    # fan-out ON when site-obs.yml runs. Its absence leaves fan-out cleanly inert.
    gate = _render_logsearch_fanout()

    return [
        CommandStep(
            "render targets + inventory + dashboard + fan-out gate",
            Command(["echo", f"rendered -> {targets}, {inv}, {dash}, {gate}"]),  # noqa: S607
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
            # picked up — matches dr-provision.sh. opensearch_snapshot_state_dir threads the
            # host-durable snapshot bucket so the folded OpenSearch configure (#1179) restores
            # the last snapshot on bring-up; empty (role default) is a clean no-op.
            Command(
                [
                    "ansible-playbook",
                    "site-obs.yml",
                    *_obs_exporter_args(),
                    *_obs_manifest_args(),
                    "-e",
                    f"opensearch_snapshot_state_dir={state('logsearch')}",
                ],  # noqa: S607
                cwd=repo_root() / "ansible",
            ),
        ),
        CommandStep(
            "provision host collector",
            # the Vergil VM (libvirt host) — node_exporter — via a connection=local
            # play, which also retires the old checkout-bound lab-net-state (#1253) and
            # lab-relay-heal (#1251) timers. No host service runs mqlab (#1252).
            Command(
                ["ansible-playbook", "host-obs.yml", "-c", "local", "-i", "localhost,"],
                cwd=repo_root() / "ansible",
            ),
        ),
        CommandStep(
            # lab_network_state is published on change by net-up.sh / net-down.sh;
            # publish once now that the drop zone exists (#1253).
            "publish net state",
            Command(["bash", str(lab_script("net-state-publish.sh"))]),  # noqa: S607
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
            Command(
                ["curl", "-fsS", "-m", "5", f"{WORKSTATION_GRAFANA_URL}/api/health"]  # noqa: S607
            ),
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
    typer.echo('             query {unit="mq-app-requester"} and toggle Live)')
    typer.echo("")
    typer.echo("The forward is automatic — no manual tunnel. Lima forwards the VM's")
    typer.echo("port 3000 to your Mac's localhost:3000, and the vergil-portforward relay")
    typer.echo("bridges that to the obs guest. Just browse localhost:3000 (anonymous —")
    typer.echo("no login, #258). If it drops after an 'obs up', the relay was wedged by a")
    typer.echo("grafana restart; 'mqlab obs up' now re-heals it as its last step (#264).")


# The periodic relay self-heal (lab-relay-heal.timer + lab/scripts/relay-heal.sh, #984)
# is retired (#1251): the sustained-use fd leak it recovered is fixed upstream
# (vergil-project/vergil-vm#298), and it ran from one checkout's path, so it failed every
# tick once that checkout was deleted. The `obs up` post-provision bounce above stays: it
# covers the separate grafana-restart wedge (#264), runs as the lab user, and is
# checkout-independent.


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
    # The log-search tier (OpenSearch + Dashboards + Data Prepper) was consolidated onto obs
    # (#1179): it now comes up as part of _obs_up_steps() above — the fan-out gate is rendered
    # there and site-obs.yml runs the folded log-stack configure. No separate bring-up.
    return steps


def _commons_up_before(deps: Deps) -> None:
    _announce_env(deps)  # the effective env profile + its source, once (#1245)
    _reserve_hugepages(deps, lambda: _guests_to_boot(deps, _commons_members()))


@commons_app.command("up")
def commons_up(step: _StepFlag = False) -> None:
    """Bring up all commons VMs and provision observability (site-obs.yml)."""
    _prepare_lab()  # commons up shells vagrant — gate + render (#276)
    _ensure_prereqs_for_commons(step=step)
    # Huge-page-backed guests (#1241, macos lever) cannot boot without their pages:
    # reserve for the commons not already running before any `vagrant up` (no-op when
    # the lever is unset).
    _execute(
        "commons-up",
        _commons_up_steps(),
        step_mode=step,
        before=_commons_up_before,
    )


@commons_app.command("status")
def commons_status() -> None:
    """Show commons health (topology joined with live virsh state)."""
    # The log-search tier rides the obs node now (#1179) — obs is already in commons.
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
    # Remove the fan-out gate file so fleet-wide Alloy fan-out reverts to inert once obs is
    # gone — a subsequent observe run must not fan out to a torn-down Data Prepper endpoint
    # (#832; log-search tier consolidated onto obs, #1179). work/ is regenerated, so
    # deleting the render is safe.
    _logsearch_fanout_path().unlink(missing_ok=True)
    members = _commons_members()
    _execute_stateful("commons-down", members, _plan_destroy, step_mode=step)


_VIRSH = ["virsh", "-c", "qemu:///system"]


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


# The box sub-app (epic .github#91). Imported here — AFTER parse_box_list and the
# other helpers box.py calls back into — to keep the cli<->box import cycle well-ordered.
# box.FLEET derives from the OS version catalog (lab/versions.yaml, epic .github#280).
from mqlab import box  # noqa: E402
from mqlab.versions import VersionError, load_build_file  # noqa: E402

box_app = typer.Typer(
    help="baked-box fleet: status/build/rebuild/clean/gc the local-built boxes",
    no_args_is_help=True,
)
app.add_typer(box_app, name="box")


_BoxNames = Annotated[
    list[str] | None, typer.Argument(help="box names to show (default: the whole fleet)")
]
_AllBoxes = Annotated[bool, typer.Option("--all", help="operate on the whole fleet")]


@box_app.command("status")
def box_status(boxes: _BoxNames = None) -> None:
    """Show the baked-box fleet: cache/age/hash/registration + REUSE/BUILD/STALE/FORCE decision."""
    names = boxes or list(box.FLEET)
    _emit_cold_boot_nudge()  # prepend the cold-boot staleness NOTICE, if any (T6)
    typer.echo(box.render_status(names))


def _select_boxes(names: list[str] | None, all_: bool) -> list[str]:
    """Resolve a mutating verb's box selection: the whole fleet for --all, else the
    validated explicit names. Fail loud (exit 2) when neither is given or a name is
    not in the fleet — so a typo never silently no-ops. Shared by build/rebuild/clean."""
    if all_:
        return list(box.FLEET)
    if not names:
        typer.echo(
            f"mqlab box: name at least one box or pass --all (fleet: {', '.join(box.FLEET)})",
            err=True,
        )
        raise typer.Exit(code=2)
    unknown = [n for n in names if n not in box.FLEET]
    if unknown:
        typer.echo(
            f"mqlab box: unknown box(es): {', '.join(unknown)} (fleet: {', '.join(box.FLEET)})",
            err=True,
        )
        raise typer.Exit(code=2)
    return names


_BuildConfig = Annotated[
    Path | None,
    typer.Option(
        "--config",
        help="a build file (e.g. 'os: rhel:9'): build every box it needs, for every stack",
    ),
]


def _config_boxes(config: Path) -> list[str]:
    """The boxes a `--config` build file needs (box.boxes_for_build), fail-loud: a bad
    build file or an unsupported OS request exits 2 with the version layer's message,
    which names the fix."""
    try:
        return box.boxes_for_build(load_build_file(config), facts=probe())
    except VersionError as exc:
        typer.echo(f"mqlab box: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@box_app.command("build")
def box_build(
    boxes: _BoxNames = None, all_: _AllBoxes = False, config: _BuildConfig = None
) -> None:
    """Ensure each box is present: REUSE a valid cache, else bake."""
    if config is not None:
        if boxes or all_:
            typer.echo(
                "mqlab box build: --config cannot be combined with box names or --all", err=True
            )
            raise typer.Exit(code=2)
        box.build_boxes(_config_boxes(config), force=False)
        return
    box.build_boxes(_select_boxes(boxes, all_), force=False)


@box_app.command("rebuild")
def box_rebuild(boxes: _BoxNames = None, all_: _AllBoxes = False) -> None:
    """Force a fresh bake (--rebuild-box), overwriting the cache — the box-rebake-in-place tier."""
    box.build_boxes(_select_boxes(boxes, all_), force=True)


_YesRebakeAll = Annotated[
    bool,
    typer.Option("--yes-rebake-all", help="confirm cleaning the whole fleet (forces a re-bake)"),
]


@box_app.command("clean")
def box_clean(boxes: _BoxNames = None, all_: _AllBoxes = False, yes: _YesRebakeAll = False) -> None:
    """Make pristine: remove the durable .box (+ .manifest-hash) and deregister; build re-bakes."""
    # clean is destructive+expensive: an unguarded --all forces a full fleet
    # re-bake. Require an explicit --yes-rebake-all to confirm it; a single named
    # box just cleans (fast, targeted).
    if all_ and not yes:
        typer.echo(
            "mqlab box: refusing to clean --all (forces a full fleet re-bake). "
            "Re-run with --all --yes-rebake-all.",
            err=True,
        )
        raise typer.Exit(code=2)
    removed = box.clean_boxes(_select_boxes(boxes, all_))
    typer.echo("removed: " + ", ".join(removed))


_GcDryRun = Annotated[
    bool, typer.Option("--dry-run", help="report what would be reclaimed, delete nothing")
]


def _retired_summary(boxes: list[str], artifacts: list[str], *, dry_run: bool) -> str:
    """Render what `box gc` did (or would do) with the retired box names (#1274)."""
    if not boxes and not artifacts:
        return "box gc: no retired box names registered or cached"
    verb = "would remove" if dry_run else "removed"
    lines = [
        f"box gc: {verb} {len(boxes)} retired box registration(s) and "
        f"{len(artifacts)} retired cache file(s) (box rename, epic .github#280)"
    ]
    lines += [f"  - vagrant box '{name}'" for name in boxes]
    lines += [f"  - {path}" for path in artifacts]
    return "\n".join(lines)


@box_app.command("gc")
def box_gc(dry_run: _GcDryRun = False) -> None:
    """Reclaim box leftovers: deregister retired box names and delete their dead caches
    (the <role>-<os><major> rename), then drop orphaned base images from re-bakes (#759)."""
    retired = box.clean_retired(dry_run=dry_run)
    artifacts = box.clean_retired_cache(dry_run=dry_run)
    typer.echo(_retired_summary(retired, artifacts, dry_run=dry_run))
    typer.echo(box.gc_summary(box.gc_orphaned_images(dry_run=dry_run)))


def _resolved_nodes() -> dict[str, Any]:
    """The rendered resolved topology's nodes (build/work/lab/topology.resolved.yaml, #276)."""
    import yaml as _yaml

    data = _yaml.safe_load(resolved_topology_path().read_text())
    nodes: dict[str, Any] = data.get("nodes", {})
    return nodes


def _needed_local_boxes(guests: list[str]) -> list[str]:
    """The local-built boxes (box.FLEET) the given guests boot, sorted. A guest on a
    cloud box (e.g. the host-resolved Ubuntu base) needs nothing built."""
    nodes = _resolved_nodes()
    boxes = {(nodes.get(g) or {}).get("box") for g in guests}
    return sorted(name for name in box.FLEET if name in boxes)


def _guests_dvds(guests: list[str]) -> list[str]:
    """The DVD ISO filenames the guests attach as a cdrom (the RHEL offline dnf repo),
    sorted and de-duplicated; empty when none does. Read from each resolved node's `dvd`
    pool path, so the staged name always matches what the guest attaches."""
    nodes = _resolved_nodes()
    dvds = {(nodes.get(g) or {}).get("dvd") for g in guests}
    return sorted(Path(dvd).name for dvd in dvds if dvd)


def _ensure_local_boxes(guests: list[str]) -> None:
    """Make the RHEL substrate ready before `vagrant up`, so a fresh box bootstraps
    without manual steps (#276/#291): build/register any local-built box the guests
    need (REUSE from cache when present, ~minutes), and stage the DVD ISO into the
    libvirt pool (idempotent). No-op for cloud Ubuntu boxes.

    Box building delegates to box.build_boxes — the same core `mqlab box build`
    drives — so bootstrap and the CLI share one path (epic .github#91). The
    builder makes the REUSE-vs-BUILD decision, so a delegated build over a valid
    cache stays cheap. The DVD staging step is preserved here."""
    needed = _needed_local_boxes(guests)
    if needed:
        box.build_boxes(needed, force=False)
    dvds = _guests_dvds(guests)
    if dvds:
        _stage_rhel_dvd(dvds)


def _stage_rhel_dvd(isos: list[str]) -> None:
    """Stage each named RHEL DVD ISO into the libvirt pool (idempotent), fail-loud."""
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps("dvd-stage", timestamp)
    try:
        stage_script = lab_script("stage-rhel-iso.sh")
        steps = [
            CommandStep(
                f"stage rhel dvd {iso}",
                Command(["bash", str(stage_script), "--iso", iso], cwd=repo_root()),  # noqa: S607
            )
            for iso in isos
        ]
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
    argv = [*_VIRSH, "undefine", f"lab_{g}", "--remove-all-storage", "--nvram"]
    cmd = Command(argv)  # noqa: S607
    return CommandStep(f"{g} undefine", cmd)


def _vagrant_machine_dir(g: str) -> Path:
    """Vagrant's per-machine state dir for guest `g` under the shared #355 dotfile."""
    return state("vagrant", "machines", g)


def _forget_machine_step(g: str) -> CommandStep:
    # Delete Vagrant's per-machine metadata (box_meta + id + …) after the domain is
    # gone. teardown removes the libvirt domain with `virsh undefine`, but that leaves
    # Vagrant's data dir untouched — and its box_meta pins the guest to the box it was
    # FIRST created with. On the next `vagrant up`, Vagrant core reloads box_meta over
    # the Vagrantfile's box (vagrant/vagrantfile.rb: config.vm.box = box_meta["name"]),
    # so a guest first stood up on a BASE box reboots from that base box forever —
    # never the baked fat box the resolved topology now assigns. Its stale-box fallback
    # only fires when the referenced box is GONE, and the base box is still registered.
    # Forgetting the metadata makes the guest brand-new so `vagrant up` reads the box
    # from the Vagrantfile (#636, epic .github#70).
    cmd = Command(["rm", "-rf", str(_vagrant_machine_dir(g))])  # noqa: S607
    return CommandStep(f"{g} forget vagrant machine", cmd)


def _cached_box_name(g: str) -> str | None:
    """The box name Vagrant cached in guest `g`'s per-machine box_meta, or None.

    The first time Vagrant stands a guest up it writes box_meta (JSON) under the
    provider subdir, naming the box it was created from — and honors that cached
    name OVER the Vagrantfile's node.vm.box on every later `vagrant up`. Returns
    the cached "name", or None when Vagrant has never created the guest (no
    box_meta yet — nothing to reconcile). A present box_meta is JSON Vagrant
    itself wrote; a malformed one is a genuine anomaly and surfaces (json.loads
    raises) rather than being swallowed into silently booting an unknown box.
    """
    meta = _vagrant_machine_dir(g) / "libvirt" / "box_meta"
    if not meta.exists():
        return None
    name = json.loads(meta.read_text()).get("name")
    return name if isinstance(name, str) else None


def _stale_box_meta_note(g: str, nodes: dict[str, Any]) -> str | None:
    """Why guest `g`'s cached box_meta is stale, or None when it is not (#858, #1274).

    Stale when the cached box is a RETIRED name (the <role>-<os><major> rename, epic
    .github#280): a retired box is never booted again, whatever the guest resolves to
    now. Otherwise stale when it names a DIFFERENT box than the resolved topology now
    assigns. A guest with no cached metadata (never created) or whose cache already
    matches is not stale — this acts only on a positively-confirmed repoint."""
    cached = _cached_box_name(g)
    if cached is None:
        return None
    if cached in RETIRED_BOX_NAMES:
        return (
            f"{g}: cached box {cached} is a retired box name (renamed, #1274); "
            "forgetting stale vagrant metadata"
        )
    resolved = (nodes.get(g) or {}).get("box")
    if resolved is not None and cached != resolved:
        return (
            f"{g}: box repointed {cached} -> {resolved}; forgetting stale vagrant metadata (#858)"
        )
    return None


def _stale_box_meta_guests(guests: list[str]) -> list[str]:
    """The guests whose cached Vagrant box_meta is stale (see _stale_box_meta_note)."""
    nodes = _resolved_nodes()
    return [g for g in guests if _stale_box_meta_note(g, nodes) is not None]


def _plan_reconcile_box_meta(guests: list[str]) -> tuple[list[CommandStep], list[str]]:
    """Forget the per-machine metadata of any guest whose cached box_meta is stale:
    it names a retired box, or a DIFFERENT box than the resolved topology now assigns
    (#858, the #636 class; #1274).

    #636 clears box_meta on TEARDOWN, but a box REPOINT (a node's box changing in
    the topology, or a box rename) followed by a bootstrap WITHOUT a teardown of that
    stack leaves the stale box_meta in place — and Vagrant honors box_meta over the
    Vagrantfile's node.vm.box, so `vagrant up` boots the OLD box (often the bare
    base box) and the repoint silently does nothing (MQ gets re-installed instead
    of skipped, defeating the bake). This closes the gap on the bring-up side:
    before `vagrant up`, forget the machine dir of each stale guest, making it
    brand-new so Vagrant reads the repointed box from the Vagrantfile.
    """
    nodes = _resolved_nodes()
    steps: list[CommandStep] = []
    notes: list[str] = []
    for g in guests:
        note = _stale_box_meta_note(g, nodes)
        if note is not None:
            notes.append(note)
            steps.append(_forget_machine_step(g))
    return steps, notes


def _reconcile_box_meta(guests: list[str], *, step: bool) -> None:
    """Run the #858 box_meta reconciliation before the vms phase's `vagrant up`.

    Emits an operator-visible note per repointed guest and forgets its stale
    per-machine metadata (see _plan_reconcile_box_meta). A no-op when nothing was
    repointed — the common case — so a steady-state bootstrap stays quiet.
    """
    steps, notes = _plan_reconcile_box_meta(guests)
    for note in notes:
        typer.echo(note)
    if steps:
        _execute("reconcile box", steps, step_mode=step)


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
        # Forget Vagrant's per-machine metadata whenever it survives on disk — even for
        # an already-gone domain — so a stale box_meta cannot shadow the fat box on the
        # next `vagrant up` (#636). Guarded on existence to stay quiet when there is
        # nothing to clean (a genuinely fresh guest).
        if _vagrant_machine_dir(g).exists():
            steps.append(_forget_machine_step(g))
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
    ansible = repo_root() / "ansible"
    cmd = Command([*_PKI_PLAYBOOK, "-e", f"pki_only={entity}"], cwd=ansible)  # noqa: S607
    _execute("pki-issue", [CommandStep(f"pki issue {entity}", cmd)], step_mode=step)


@pki_app.command("list")
def pki_list() -> None:
    """List the PKI entity inventory (org, OU, kind) derived from topology."""
    entities = json.loads(_render_pki_entities().read_text())
    for e in entities:
        typer.echo(f"{e['cn']:<14} org={e['org']:<11} ou={e.get('ou', '-'):<16} {e['kind']}")


# --- dr: cross-site DR cutover / failback (the DR *operation*, #867) --------------
# This is the operator verb that DRIVES a cross-site cutover, distinct from the
# mqlab.dr package (the DR *measurement* framework: ledger/classifier/report). It
# wraps lab/scripts/rdqm-dr-cutover.sh — which needs `ansible` on PATH — by shelling
# it from inside the mqlab venv (SubprocessRunner inherits this process's env, so the
# venv PATH carries ansible), removing the manual `uv run` the bare script required
# (#294's deferred item). cutover = a2b (site A -> B); failback = b2a (B -> A).
dr_app = typer.Typer(
    help="disaster-recovery operation: cross-site cutover / failback (#867)",
    no_args_is_help=True,
)
app.add_typer(dr_app, name="dr")

_RDQM_DR_CUTOVER_SCRIPT = "rdqm-dr-cutover.sh"
_Rpo0DrillOpt = Annotated[
    bool,
    typer.Option(
        "--rpo0-drill",
        help=(
            "seed a persistent message before the cut and assert it survives at the peer "
            "(RPO-0, no message loss); exercises only against a live rdqm DR arm"
        ),
    ),
]


def _dr_run(stack_name: str, direction: str, verb: str, *, rpo0_drill: bool) -> None:
    # Resolve + gate the stack, render the inventory the script's ansible calls read, then
    # shell rdqm-dr-cutover.sh with the direction + the stack's app QM. Only the rdqm
    # mechanism has this script (the pacemaker arm's cutover is a different flow); refuse
    # anything else with a clean exit-2 message rather than run the wrong script.
    stack = _stack_qm_or_exit(stack_name)
    if stack.mechanism != "rdqm":
        typer.echo(
            f"dr {verb}: only the rdqm mechanism has an rdqm-dr-cutover flow "
            f"(stack {stack_name!r} is {stack.mechanism!r})",
            err=True,
        )
        raise typer.Exit(code=2)
    argv = ["bash", str(lab_script(_RDQM_DR_CUTOVER_SCRIPT)), direction, stack.qm.qm_app]
    env = {"RPO0_DRILL": "1"} if rpo0_drill else None
    deps = build_deps(verb, datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        # The script drives ansible against build/work/inventory.ini (via ansible.cfg) —
        # render it fresh so the site nodes resolve, mirroring the qm/bootstrap paths.
        _render_inventory(deps)
        step = CommandStep(
            f"{stack.name} dr {verb}",
            Command(argv, cwd=repo_root() / "ansible", env=env),  # noqa: S607
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


@dr_app.command("cutover")
def dr_cutover(stack: str, rpo0_drill: _Rpo0DrillOpt = False) -> None:
    """Cut a stack's live QM over to its DR peer site (a2b: site A -> B)."""
    _dr_run(stack, "a2b", "cutover", rpo0_drill=rpo0_drill)


@dr_app.command("failback")
def dr_failback(stack: str, rpo0_drill: _Rpo0DrillOpt = False) -> None:
    """Fail a stack's QM back to its original site (b2a: site B -> A)."""
    _dr_run(stack, "b2a", "failback", rpo0_drill=rpo0_drill)


# --- netem: tunable WAN latency on the cross-region plane (#1105) ----------------
# A thin wrapper over lab/scripts/net-latency.sh, which owns the tc attach-point
# decision: a root `netem` delay qdisc on every tap enslaved to virbr-wan (guest<->
# guest traffic never traverses the bridge's own root qdisc, so bridge-root netem is
# a no-op on it). A symmetric one-way delay D on every tap yields RTT ~= 2D, and the
# HA/heartbeat bridges (virbr-hb-*) are structurally excluded. Mirrors the pki/dr
# command-wraps-script shape; step_mode=False (one shot, nothing to pause between).
netem_app = typer.Typer(
    help="inject WAN latency on the cross-region plane (tc netem, delay-only)",
    no_args_is_help=True,
)
app.add_typer(netem_app, name="netem")

_NET_LATENCY_SCRIPT = "net-latency.sh"


def _netem(action: str, *args: str) -> None:
    argv = ["bash", str(lab_script(_NET_LATENCY_SCRIPT)), action, *args]
    step = CommandStep(f"netem {action}", Command(argv))  # noqa: S607
    _execute(f"netem-{action}", [step], step_mode=False)


@netem_app.command("set")
def netem_set(
    delay: Annotated[
        str, typer.Option("--delay", help="one-way delay on virbr-wan, e.g. 10ms (RTT ~= 2x)")
    ],
) -> None:
    """Apply a symmetric one-way delay on the cross-region plane (RTT ~= 2 x delay)."""
    _netem("set", delay)


@netem_app.command("clear")
def netem_clear() -> None:
    """Remove all netem shaping from virbr-wan (restore the default qdisc)."""
    _netem("clear")


@netem_app.command("show")
def netem_show() -> None:
    """Show the current netem qdisc on each virbr-wan tap."""
    _netem("show")


@app.command("parity")
def parity_matrix() -> None:
    """Print the cross-arm capability matrix (which verbs each arm supports)."""
    typer.echo(parity.render_markdown())


# --- perf reports (epic .github#275): compare two runs' perf-*.json (#1204) -------------
perf_app = typer.Typer(
    help="perf reports: compare two runs (timings + host contention)", no_args_is_help=True
)
app.add_typer(perf_app, name="perf")


@perf_app.command("diff")
def perf_diff(
    a: Annotated[str, typer.Argument(help="perf report A (e.g. the macOS run)")],
    b: Annotated[str, typer.Argument(help="perf report B (e.g. the x86 cloud run)")],
) -> None:
    """Diff two perf reports: per-phase/milestone deltas (A-B) + ratios (A/B), the dominant
    divergence, and top vCPU-steal contributors. A comparison aid — no pass/fail verdict;
    cross-hardware numbers are directional (docs/development/perf-and-staging.md)."""
    try:
        report = perfdiff.diff(perfdiff.load(a), perfdiff.load(b))
    except perfdiff.PerfDiffError as exc:
        typer.echo(f"mqlab perf diff {a} {b}: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(report.render(label_a=a, label_b=b))


def _lookup_stack_or_exit(name: str) -> Stack:
    # Validate a stack name early, with a clean exit-2 message (mirrors
    # _lookup_setup_or_exit, but over the #350 canonical stack registry).
    stack = lab_stacks().get(name)
    if stack is None:
        typer.echo(f"no stack named {name!r}", err=True)
        raise typer.Exit(code=2)
    return stack


def _gate_stack_host_arch(stack: Stack) -> None:
    """Abort a RHEL-stack bring-up on a non-x86 host BEFORE any box bake or VM boot
    (#847). The RHEL arms require an x86_64 host — RHEL is unavailable for Apple
    Silicon and emulated cross-arch box builds are disabled (#103 D11) — so on an
    aarch64 host only the Ubuntu stacks run. Formerly this surfaced only as a deep
    box-bake failure partway through bootstrap; this fast preflight fails loud with
    an actionable message instead. Exit 2 mirrors the other precondition gates."""
    reason = rhel_stack_unsupported_reason(stack, probe())
    if reason is not None:
        typer.echo(reason, err=True)
        raise typer.Exit(code=2)


# Prometheus on the obs guest (net-mgmt IP : Prometheus port). The observe probe
# queries its targets API to see whether this stack's exporters are registered.
PROMETHEUS_URL = "http://10.50.0.2:9090"


def _probe_skipped_down(deps: Deps, probe: str, domains: dict[str, str], hosts: list[str]) -> bool:
    """True (and say why, echoed + teed) iff a probe's target guests are not all RUNNING.

    The pre-flight gate (#1212): `_probe_all` has already read every domain's state,
    so a probe whose target guest isn't running is unsatisfied by definition — running
    it only burns the load-tuned SSH connect budget (#590: ~4 min on a cold lab) or an
    HTTP timeout to reach the same False. An empty host list (the target group is
    absent from topology) has nothing to probe and is skipped the same way. Never
    silent: the skip reason lands in the run transcript.
    """
    down = [h for h in hosts if classify(domains, h) != RUNNING]
    if hosts and not down:
        return False
    reason = f"domain(s) not running: {', '.join(down)}" if down else "no target hosts"
    message = f"{probe} probe skipped: {reason}"
    deps.renderer.note(message)
    deps.transcript.write(message)
    return True


def _probe_qm_up(deps: Deps, stack: Stack, domains: dict[str, str]) -> bool:
    """True iff the stack's QM reports up via its qm-status verb (provision phase).

    Resolves the stack's `qm-status` verb (the same per-stack dispatch dict the
    qm commands use) and runs that status command on the cluster's first node via
    `ansible <cluster_group>[0] -b -m shell`. Exit 0 ⇒ provisioned + up.

    Uses `stack.cluster_group` (e.g. "pcmk_a", "rdqm_a") — NOT `stack.groups[0]`,
    which for pcmk-ubuntu is the SAN iSCSI-target host ("san_a") that has no
    Pacemaker or MQ tooling and would always return a non-zero exit code.

    A stack with no qm-status verb or no cluster_group (reserved stack) is, by
    definition, not provisioned — returns False with no runner call. Likewise when
    the probe's target guest (`<cluster_group>[0]`, per the already-probed
    `domains`) is not RUNNING (#1212): no SSH attempt, a logged skip, False.
    """
    impl = stack.verbs.get("qm-status")
    if not impl:
        return False
    if not stack.cluster_group:
        return False
    # Gate on exactly the host the ansible pattern below targets ([0]), so a running
    # target keeps today's semantics byte-for-byte.
    if _probe_skipped_down(deps, "qm-status", domains, group_hosts(stack.cluster_group)[:1]):
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


def _probe_observe(deps: Deps, stack: Stack, domains: dict[str, str]) -> bool:
    """True iff this stack's exporter is a healthy Prometheus target (observe phase).

    Queries Prometheus' /api/v1/targets and checks the stack's app exporter port
    (from alloc.exporter_app_port) appears among the active targets with health
    "up". A stack with no exporter port allocated cannot be observed, nor can one
    whose obs guest (OBS_GROUP, where Prometheus runs) is not RUNNING in the
    already-probed `domains` (#1212: skipped with a logged reason, no HTTP call).
    """
    port = stack.alloc.get("exporter_app_port")
    if not port:
        return False
    if _probe_skipped_down(deps, "observe", domains, group_hosts(OBS_GROUP)):
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
    surfaces its real exit/parse result; nothing is swallowed. The domain states
    gate the qm-status and observe probes (#1212): a probe whose target guest is not
    running is skipped (logged) rather than left to time out against a missing VM.
    """
    nets = _probe_net_states(deps)
    domains = _probe_states(deps)
    qm_up = _probe_qm_up(deps, stack, domains)
    observe = _probe_observe(deps, stack, domains)
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


def _guests_to_boot(deps: Deps, guests: list[str]) -> list[str]:
    """The guests in `guests` that are not live (absent or shut off) per a fresh
    `virsh list --all` — the ones a `vagrant up` will (re)start."""
    states = _probe_states(deps)
    return [g for g in guests if not is_live(states, g)]


def _announce_env(deps: Deps) -> topology.EnvResolution:
    """Print the effective env profile and its source once for this run (#1245).

    Explicit MQLAB_ENV, else the detected platform; an inconclusive detection says in
    the same one line that the base topology is in use — never a silent profile."""
    resolution = topology.resolve_env()
    deps.renderer.note(resolution.describe())
    return resolution


def _reserve_hugepages(
    deps: Deps,
    guests: Callable[[], list[str]],
    *,
    perf: BootstrapPerf | None = None,
) -> None:
    """Reserve the 2 MiB huge pages huge-page-backed guests need, before `vagrant up`.

    No-op unless the effective topology sets `memory_backing: hugepages` (#1241, the
    macos env profile) — default and cloud runs never reach `guests()` or the play.
    `guests()` names the guests about to boot (live ones already hold their pages);
    their summed RAM plus a margin is the free-page need (hugepages.needed_pages). The
    host-hugepages.yml play (become, localhost) grows vm.nr_hugepages, retries after
    drop_caches + compact_memory, and exits non-zero with needed-vs-got when the kernel
    still falls short — surfaced here as StepFailedError (never a 4 KiB fallback).

    The play is a `preflight` step of the perf report, and the outcome (needed, before/
    after counters, whether the reclaim retry ran) is noted in it.
    """
    topo = topology.load()  # effective topology: base + MQLAB_ENV profile (#1202)
    if not hugepages.enabled(topo):
        return
    to_boot = guests()
    needed = hugepages.needed_pages(topo, to_boot)
    if needed == 0:
        msg = "huge pages: every guest is already running — no reservation needed"
        deps.renderer.note(msg)
        if perf is not None:
            perf.record.note(msg)
        return
    before = hugepages.read_meminfo()
    step = CommandStep(
        f"reserve huge pages ({needed} x 2 MiB)",
        hugepages.reserve_command(needed),
        phase=PREFLIGHT,
    )
    run_steps(
        [step],
        runner=deps.runner,
        renderer=deps.renderer,
        transcript=deps.transcript,
        step_mode=False,
        pauser=deps.pauser,
        perf=perf.record if perf is not None else NullSink(),
    )
    after = hugepages.read_meminfo()
    lines = deps.transcript.path.read_text(encoding="utf-8", errors="replace").splitlines()
    reclaim = ansible_task_outcomes(lines, [hugepages.RECLAIM_TASK]).get(hugepages.RECLAIM_TASK)
    retried = reclaim is not None and reclaim.ok
    msg = (
        f"huge pages: needed {needed} x 2 MiB ({needed * hugepages.PAGE_MIB} MiB, incl. "
        f"{hugepages.MARGIN_PAGES}-page margin) for {', '.join(to_boot)}; "
        f"{'reserved after a drop_caches + compact_memory retry' if retried else 'reserved'}; "
        f"before {hugepages.summary(before)} -> after {hugepages.summary(after)}"
    )
    deps.renderer.note(msg)
    if perf is not None:
        perf.record.note(msg)


def _release_hugepages(deps: Deps) -> None:
    """Return the huge pages to the kernel (vm.nr_hugepages=0, verified by the play).

    Callers decide WHEN: only once no lab guest can still be using them (the last stack
    down). The play fails loud if pages are still mapped, rather than pretending."""
    run_steps(
        [CommandStep("release huge pages", hugepages.release_command())],
        runner=deps.runner,
        renderer=deps.renderer,
        transcript=deps.transcript,
        step_mode=False,
        pauser=deps.pauser,
    )


def _bootstrap_run(
    stack_name: str,
    *,
    only: str | None = None,
    from_phase: str | None = None,
    step: bool,
    no_dr: bool = False,
) -> None:
    """Bring a stack up by running its bring-up phases (net → vms → provision →
    observe) from the first unsatisfied one, so a re-run resumes. --only/--from
    override the selection. Each phase fails loud: a step failure halts the run
    and prints a resume hint naming the failing phase.

    --no-dr (#188) brings up the HA site only: it shapes the phases it runs (site-A
    guests, `dr_enabled=false` to the provision) and never tears down a site-B some
    earlier full bootstrap left running. Stateless — nothing is persisted."""
    venvsync.ensure_venv_current()  # sync dev venv to uv.lock before subprocesses spawn (#776)
    stack = _lookup_stack_or_exit(stack_name)
    # Fail loud before any lab I/O: --no-dr on a stack that declares no DR site to skip
    # would otherwise pass dr_enabled=false to a playbook that does not honour it — and
    # bring up site-A guests only to fail provisioning (spec §3.5).
    if no_dr and not stack.dr_groups:
        typer.echo(f"{stack.name} declares no DR site to skip (no dr_groups)", err=True)
        raise typer.Exit(code=2)
    _gate_stack_host_arch(stack)  # #847: abort a RHEL stack on aarch64 pre-bake
    _prepare_lab()  # host gate up front — fail loud before any phase touches the lab
    _emit_cold_boot_nudge()  # advisory staleness NOTICE in the preflight, before phases (T6)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps("bootstrap", timestamp)
    # Perf capture (#1205): additive + non-fatal — BootstrapPerf never raises into the run
    # or changes its exit status; the report is written in `finally`, so a bootstrap that
    # fails partway still gets one (that is the most valuable data). It starts BEFORE the
    # pre-flight (#1215) so the inventory render + lab-state probe are on the perf clock,
    # each timed as a `preflight` step (wrapped here at the call site; the probe itself
    # is untouched).
    perf = BootstrapPerf.start(
        stack.name, lambda: all_vms(stack, no_dr=no_dr), renderer=deps.renderer
    )
    try:
        # The vms phase's `vagrant up` step carries env=None, so it inherits this
        # process's environment. Export the vagrant env up front so bring-up sees
        # VAGRANT_DOTFILE_PATH (the shared #355 dotfile, so it drives the one
        # canonical lab even from a worktree that never created it). Mirrors
        # _ssh_into's os.environ.update(_vagrant_env()); the secret injection below
        # is the same sequencer-owns-the-env pattern (#373).
        os.environ.update(_vagrant_env())
        # The effective env profile + its source, printed once and recorded (#1245).
        env = _announce_env(deps)
        perf.record.set_env(env.env, env.source)
        perf.record.note(env.describe())
        # Refresh the ansible inventory before probing/provisioning (#377): the
        # provision/observe playbooks target the #350 stack-aggregate groups
        # (e.g. hosts: pcmk_ubuntu), and _probe_all + provision run ansible against
        # build/work/inventory.ini. A stale file (pre-cutover, missing the aggregate
        # groups) silently no-ops those plays (acl install, cold-boot guard). Always
        # render fresh here in the sequencer — phases.py stays pure.
        perf.preflight("render inventory", lambda: _render_inventory(deps))
        states = perf.preflight("probe lab state", lambda: _probe_all(deps, stack))
        selected = _select_phases(stack, states, only=only, from_phase=from_phase)
        if not selected:
            perf.nothing_to_do()
            deps.renderer.note(f"{stack_name}: already satisfied — nothing to do")
            return
        # Huge-page-backed guests (#1241, macos lever): reserve their 2 MiB pages before
        # the vms phase's first `vagrant up` — only when that phase runs, and a no-op
        # unless the effective topology sets memory_backing. A short reservation fails
        # the bootstrap here, loud, rather than letting a guest boot fail mid-batch.
        if any(phase.name == "vms" for phase in selected):
            domains = states["domains"]
            try:
                _reserve_hugepages(
                    deps,
                    lambda: [g for g in all_vms(stack, no_dr=no_dr) if not is_live(domains, g)],
                    perf=perf,
                )
            except StepFailedError as exc:
                perf.failed(PREFLIGHT, exc.exit_code)
                typer.echo(
                    "huge-page reservation failed (needed vs got above); free Vergil VM "
                    f"RAM, then re-run: mqlab bootstrap {stack_name}",
                    err=True,
                )
                raise typer.Exit(code=exc.exit_code) from exc
        # Inject the stack's secrets as env vars for the provision playbook (#373):
        # its roles read e.g. PCMK_HACLUSTER_PASSWORD via lookup('env', ...). The I/O
        # (lab-secret.sh) lives here in the sequencer, not in pure phases.py — mirrors
        # os.environ.update(_vagrant_env()). The #350 cutover dropped this with the old
        # `vm provision`; without it the hacluster password is empty and chpasswd fails.
        if any(phase.name == "provision" for phase in selected):
            for secret in stack.secrets:
                os.environ[secret.upper()] = _source_secret(deps, secret)
        # Once-per-run prereq kinds already ensured this run (#1248): provision and
        # observe both declare `pki`, and the second play is redundant within a run.
        ensured: set[str] = set()
        for phase in selected:  # one phase at a time so a failure names its phase
            try:
                # Ensure this phase's fresh-volume prerequisites first (#350 Task 5),
                # only for the phases actually selected this run, timed as the report's
                # `prereq:<phase>` phase (#1248).
                try:
                    _ensure_prereqs_for_stack(stack, phase, step=step, perf=perf, ensured=ensured)
                except typer.Exit as exc:
                    perf.failed(prereq_phase(phase.name), exc.exit_code)
                    raise
                steps = phase.build_steps(stack, deps, no_dr=no_dr)
                perf.register_phase(phase.name, steps)
                if phase.name == "observe":
                    # The log-search tier is a CORE observability layer (#1018) and now rides
                    # the obs node (#1179): its bring-up is folded into site-obs.yml (run by
                    # the observe steps). Render the fleet-wide Alloy->OpenSearch fan-out gate
                    # here, before this phase configures Alloy, so the fleet ships to a live
                    # (obs) Data Prepper instead of hot-looping into a disk-fill.
                    _render_logsearch_fanout()
                run_steps(
                    steps,
                    runner=deps.runner,
                    renderer=deps.renderer,
                    transcript=deps.transcript,
                    step_mode=step,
                    pauser=deps.pauser,
                    perf=perf.record,
                )
            except StepFailedError as exc:
                perf.failed(phase.name, exc.exit_code)
                # Carry --no-dr into the resume hint (#1163). The flag shapes which
                # guests every phase targets (site-A only, dr_enabled=false), so a
                # resume that dropped it would re-enter in full-HADR mode and fail
                # UNREACHABLE on the site-B guests this run never booted (#188) — the
                # opposite of clean re-entry. The hint must reproduce the run's own
                # footprint; the VAL-A path is `bootstrap nativeha-ubuntu --no-dr`.
                # --step is deliberately NOT carried: it is a per-invocation interaction
                # choice, not a footprint-shaping flag, and a resume runs fine unpaused.
                dr_flag = " --no-dr" if no_dr else ""
                hint = f"resume with: mqlab bootstrap {stack_name} --from {phase.name}{dr_flag}"
                typer.echo(hint, err=True)  # to the CLI's stderr, where the operator sees it
                deps.transcript.write(hint)
                raise typer.Exit(code=exc.exit_code) from exc
        perf.completed()
    except NoTTYError as exc:
        deps.renderer.error(str(exc))
        raise typer.Exit(code=2) from exc
    finally:
        perf.finish(deps.transcript.path, timestamp)
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
    no_dr: Annotated[
        bool,
        typer.Option("--no-dr", help="skip the DR site — bring up the HA site only"),
    ] = False,
) -> None:
    """Bring up a whole stack in one command: net → vms → provision → observe.

    Runs from the first unsatisfied phase, so a re-run resumes. Use --from PHASE
    to force a starting phase or --only PHASE to run a single phase. --no-dr brings
    up only the HA site (skips the DR guests + DR provisioning) for a lighter
    footprint. Run `mqlab doctor` first to pre-flight the host."""
    _validate_phase_name(from_phase, "--from")
    _validate_phase_name(only, "--only")
    _bootstrap_run(stack_name, only=only, from_phase=from_phase, step=step, no_dr=no_dr)


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
    - Releases the huge pages (#1241, only when the memory_backing lever is set) on the
      last-stack-down condition, after every destroy succeeded — never under running guests.
    - Runs all accumulated steps in one pass (fail-loud on StepFailedError).

    Extension point for Task 11: per-stack commons-instance cleanup (svc QM
    dltmqm, exporter unit, scrape-target entry) belongs in a
    `_teardown_stack_commons_instances(stack, deps)` helper inserted here
    before the commons-VM destroy block. Today's commons are single-instance
    shared VMs only.
    """
    venvsync.ensure_venv_current()  # sync dev venv to uv.lock before subprocesses spawn (#776)
    stack = _lookup_stack_or_exit(stack_name)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps("teardown", timestamp)
    try:
        _announce_env(deps)  # same resolution as bootstrap -> same huge-page lever (#1245)
        members = stack_members(stack.name) or []
        all_stack_vms = all_vms(stack)
        commons_vms = [vm for vm in all_stack_vms if vm not in members]

        states = _probe_states(deps)

        member_steps, member_notes = _plan_destroy(members, states)
        for note in member_notes:
            deps.renderer.note(note)

        # Huge pages (#1241) are released on the SAME condition that reclaims commons
        # unforced: this is the last stack down. --commons alone is not enough — another
        # stack's running guests may still hold pages. Probe the reference count only when
        # something depends on it (unchanged behaviour when the lever is unset).
        hugepage_lever = hugepages.enabled(topology.load())
        last_stack_down = (
            not _other_stacks_up(deps, exclude=stack.name)
            if (hugepage_lever or not commons)
            else False
        )
        destroy_commons = commons or last_stack_down

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
        # Every lab guest is gone now (last stack + its commons destroyed above, and a
        # failed destroy raised before reaching here), so no running guest holds a page.
        if hugepage_lever and last_stack_down:
            _release_hugepages(deps)
        elif hugepage_lever:
            deps.renderer.note("huge pages kept — another stack's guests still use them")
        # The destroyed overlays freed their box base images; reclaim any now-orphaned
        # older ones (keep the newest per box). Best-effort — never fail a teardown on
        # a GC hiccup, but a failure is reported, not swallowed (#759).
        gc_result = box.gc_orphaned_images_best_effort()
        if gc_result and gc_result.deleted:
            deps.renderer.note(box.gc_summary(gc_result))
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
