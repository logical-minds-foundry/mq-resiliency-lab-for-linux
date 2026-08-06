"""`mqlab logsearch` — operator CLI for the logsearch tier (epic .github#149, Task 11).

Four commands over the single-node OpenSearch + Dashboards tier:

- ``status``   — `_cluster/health` + disk-used + read-only/full check. Green AND
  yellow are healthy on a single-node ``replicas:0`` cluster (the connector spike:
  the default ``replicas:1`` reads yellow forever); only red / unreachable fails.
  A read-only / flood-stage-full store is reported **loudly** and fails — never a
  silent skip.
- ``open``     — print the Dashboards URL (the mgmt-plane investigation surface).
- ``snapshot`` — take a native ``_snapshot`` (OpenSearch snapshot API,
  ``wait_for_completion``) then tar+fetch the fs repo to the host-durable
  ``$(mqlab build path state)/logsearch/`` bucket (snapshot spike, Task 2).
- ``restore``  — stage the latest (or ``--snapshot``) host artifact back to the
  guest repo and ``POST _restore``.

Security posture (v1): the ``opensearch`` / ``opensearch-dashboards`` roles run
with the security plugin DISABLED — plain http on :9200 / :5601, no auth (spec
§10 telemetry-plane tenet, follow-on #819). The health/API path is unauthenticated
http to match; this file wires no auth the cluster is not running.

Error vocabulary (layered): ``mqlab`` messages name fully-qualified ``mqlab``
commands; OpenSearch tooling speaks for itself.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import typer
from rich.console import Console

from mqlab.orchestrator import CommandStep, StepFailedError, run_steps
from mqlab.paths import repo_root, state
from mqlab.render import Renderer
from mqlab.runner import Command, SubprocessRunner
from mqlab.transcript import Transcript, transcript_path

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

# --- tier endpoints (mgmt plane) ------------------------------------------------
# The logsearch node carries a mgmt-only NIC (topology #830). mqlab runs on the
# libvirt host, which sits on the mgmt plane and reaches the guest directly — the
# same convention as obs's GRAFANA_URL (10.50.0.2) / probe (10.50.0.3); logsearch
# is the next free mgmt IP (.4, plan Task 8). Overridable per-invocation.
OPENSEARCH_HOST = "10.50.0.4"
OPENSEARCH_PORT = 9200
DASHBOARDS_PORT = 5601

# The native filesystem snapshot repository + its on-guest location (opensearch
# role defaults; snapshot spike Task 2). The CLI never hardcodes a build/<X> path —
# the host store resolves through `mqlab build path state` (state("logsearch")).
SNAPSHOT_REPO = "logsearch-fs"
REMOTE_REPO_DIR = "/var/lib/opensearch/snapshots"
# The Ansible inventory host/group the transport ad-hoc calls target (topology #830 /
# site-logsearch.yml #832 supply it at runtime; the CLI just names it).
INVENTORY_HOST = "logsearch"

# Informational flood-stage threshold for the disk line. The AUTHORITATIVE full
# signal is the read-only-allow-delete index block OpenSearch itself sets at the
# flood-stage watermark (read_only_indices); this is just for a louder disk line.
DISK_FULL_PERCENT = 90

_HTTP_TIMEOUT = 5


class LogsearchError(RuntimeError):
    """A loud, mqlab-scoped logsearch failure (unreachable store, failed snapshot,
    missing artifact). Carries a fully-qualified ``mqlab logsearch …`` message."""


# --- pure helpers ---------------------------------------------------------------


def interpret_health(health: dict[str, Any]) -> str:
    """Map a ``_cluster/health`` body to ``"healthy"`` / ``"unhealthy"``.

    Single-node, ``number_of_replicas: 0``: green AND yellow are both healthy
    (yellow only means unassigned replicas, which a single node can never place).
    Only red — or an absent/unknown status — is a failure.
    """
    return "healthy" if health.get("status") in {"green", "yellow"} else "unhealthy"


def latest_snapshot(names: Iterable[str]) -> str | None:
    """The lexically-greatest snapshot name (``snap-<UTC-timestamp>`` sorts as
    chronological), or ``None`` when there are none."""
    ordered = sorted(names)
    return ordered[-1] if ordered else None


def dashboards_url(host: str, port: int) -> str:
    """The OpenSearch Dashboards URL on the mgmt plane (plain http, v1)."""
    return f"http://{host}:{port}"


def _read_only_flag(entry: dict[str, Any]) -> bool:
    """True when an index's settings carry ``read_only_allow_delete: true`` in
    either the flat (``index.blocks.read_only_allow_delete``) or nested
    (``index.blocks.read_only_allow_delete``) form OpenSearch may return."""
    settings = entry.get("settings", {})
    flat = settings.get("index.blocks.read_only_allow_delete")
    nested = settings.get("index", {}).get("blocks", {}).get("read_only_allow_delete")
    value = flat if flat is not None else nested
    return str(value).lower() == "true"


def read_only_indices(settings: dict[str, Any]) -> list[str]:
    """The indices OpenSearch has flipped to read-only (the flood-stage / full
    signal). Sorted for a stable, testable report."""
    return sorted(name for name, entry in settings.items() if _read_only_flag(entry))


def disk_summary(allocation: list[dict[str, Any]]) -> str:
    """A per-node one-liner from ``_cat/allocation?format=json``. Rows with no
    ``node`` (the UNASSIGNED shard row) are skipped. ``"unknown"`` when empty."""
    parts: list[str] = []
    for row in allocation:
        node = row.get("node")
        if not node:
            continue
        pct = row.get("disk.percent", "?")
        used = row.get("disk.used", "?")
        total = row.get("disk.total", "?")
        parts.append(f"{node}: {pct}% used ({used}/{total})")
    return "; ".join(parts) if parts else "unknown"


def disk_full(allocation: list[dict[str, Any]], threshold: int = DISK_FULL_PERCENT) -> bool:
    """True when any node's disk-used percent is at/above the flood-stage threshold."""
    for row in allocation:
        if not row.get("node"):
            continue
        try:
            if float(row.get("disk.percent", 0)) >= threshold:
                return True
        except (TypeError, ValueError):
            continue
    return False


def snapshot_name(now: datetime) -> str:
    """A sortable snapshot name — ``snap-<UTC-timestamp>`` — so ``latest_snapshot``
    picks the most recent by plain lexical max (snapshot spike, Task 2 note)."""
    return "snap-" + now.strftime("%Y%m%dT%H%M%SZ")


# --- Ansible transport argv (native _snapshot fs repo; snapshot spike Task 2) ----
# `fetch` is single-file, so the guest tars the repo dir first, then `fetch`
# transports the tarball to the host state bucket. Restore reverses it: `copy` the
# host tarball to the guest, untar into the repo dir, then POST _restore.


def archive_argv(host: str, repo_dir: str, remote_tar: str) -> list[str]:
    """Ad-hoc: tar the fs snapshot repo on the guest into a single tarball."""
    return [
        "ansible",
        host,
        "-m",
        "ansible.builtin.shell",
        "-a",
        f"tar czf {remote_tar} -C {repo_dir} .",
    ]


def fetch_argv(host: str, remote_tar: str, dest_dir: str) -> list[str]:
    """Ad-hoc: fetch the guest tarball to the host state bucket (``flat=true`` so it
    lands as ``<dest_dir>/<basename>`` rather than under a per-host tree)."""
    return [
        "ansible",
        host,
        "-m",
        "ansible.builtin.fetch",
        "-a",
        f"src={remote_tar} dest={dest_dir}/ flat=true",
    ]


def copy_argv(host: str, local_tar: str, remote_tar: str) -> list[str]:
    """Ad-hoc: stage a host snapshot tarball back onto the guest."""
    return [
        "ansible",
        host,
        "-m",
        "ansible.builtin.copy",
        "-a",
        f"src={local_tar} dest={remote_tar}",
    ]


def untar_argv(host: str, repo_dir: str, remote_tar: str) -> list[str]:
    """Ad-hoc: expand a staged tarball back into the guest's fs snapshot repo."""
    return [
        "ansible",
        host,
        "-m",
        "ansible.builtin.shell",
        "-a",
        f"tar xzf {remote_tar} -C {repo_dir}",
    ]


# --- host / HTTP seams (real I/O; monkeypatched in unit tests) -------------------


def _snapshot_state_dir() -> Path:
    """The host-durable snapshot store — ``$(mqlab build path state)/logsearch/`` —
    resolved through the state bucket, never a hardcoded build/<X> path."""
    return state("logsearch")


def _request(method: str, url: str, body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - mgmt-plane http
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310
            raw = resp.read().decode()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise LogsearchError(
            f"mqlab logsearch: cannot reach OpenSearch at {url}: {exc}. "
            f"Is the logsearch node up (mqlab vm status) and site-logsearch.yml applied?"
        ) from exc
    return json.loads(raw) if raw else {}


def _get_json(url: str) -> Any:
    return _request("GET", url)


def _put_json(url: str, body: dict[str, Any] | None = None) -> Any:
    return _request("PUT", url, body)


def _post_json(url: str, body: dict[str, Any] | None = None) -> Any:
    return _request("POST", url, body)


def _execute_steps(verb: str, steps: list[CommandStep]) -> None:
    """Run transport steps through the CommandRunner seam (the only path to
    subprocess in mqlab). Streams to a transcript; raises on a failed step."""
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    transcript = Transcript(transcript_path(verb, timestamp))
    try:
        run_steps(
            steps,
            runner=SubprocessRunner(),
            renderer=Renderer(Console()),
            transcript=transcript,
            step_mode=False,
            pauser=_NoPause(),
        )
    finally:
        transcript.close()


class _NoPause:
    """A no-op pauser — step_mode is always False here, so wait() is never called,
    but run_steps still requires a Pauser object."""

    def wait(self) -> None:  # pragma: no cover - step_mode is always False
        return None


# --- the Typer app --------------------------------------------------------------

app = typer.Typer(
    help="logsearch tier (OpenSearch + Dashboards): status/open/snapshot/restore",
    no_args_is_help=True,
)

_HostOpt = typer.Option(OPENSEARCH_HOST, "--host", help="logsearch node mgmt IP/host")
_PortOpt = typer.Option(OPENSEARCH_PORT, "--port", help="OpenSearch http port")


@app.command("status")
def status(host: str = _HostOpt, port: int = _PortOpt) -> None:
    """Report cluster health, disk-used, and any read-only/full state.

    Green and yellow are healthy on this single-node, replicas:0 tier; only red or
    an unreachable node fails. A read-only / flood-stage-full store is reported
    LOUDLY and fails the command — never a silent skip.
    """
    base = f"http://{host}:{port}"
    try:
        health = _get_json(f"{base}/_cluster/health")
        allocation = _get_json(f"{base}/_cat/allocation?format=json")
        settings = _get_json(
            f"{base}/_all/_settings/index.blocks.read_only_allow_delete"
            "?flat_settings=true&expand_wildcards=all"
        )
    except LogsearchError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    state_word = interpret_health(health)
    typer.echo(f"cluster: {health.get('status', 'unknown')} ({state_word})")
    typer.echo(f"disk: {disk_summary(allocation)}")

    failed = state_word == "unhealthy"

    read_only = read_only_indices(settings)
    if read_only:
        typer.echo(
            "!!! STORE READ-ONLY / FULL: OpenSearch has flipped these indices to "
            f"read-only (flood-stage watermark): {', '.join(read_only)}. "
            "Ingestion is blocked until disk is freed. Take a snapshot and prune, "
            "or grow the node's disk.",
            err=True,
        )
        failed = True
    elif disk_full(allocation):
        typer.echo(
            f"!!! DISK NEAR FULL (>= {DISK_FULL_PERCENT}%): the store will go read-only "
            "at the flood-stage watermark. Free disk before ingestion stalls.",
            err=True,
        )

    if failed:
        raise typer.Exit(code=1)


@app.command("open")
def open_() -> None:
    """Print the OpenSearch Dashboards URL (the mgmt-plane investigation surface)."""
    url = dashboards_url(OPENSEARCH_HOST, DASHBOARDS_PORT)
    typer.echo(f"OpenSearch Dashboards: {url}")
    typer.echo(f"Discover (full-text over the corpus): {url}/app/data-explorer/discover")
    typer.echo("Reach it on the lab mgmt plane (v1: plain http, no auth — spec §10).")


@app.command("snapshot")
def snapshot(host: str = _HostOpt, port: int = _PortOpt) -> None:
    """Take a native ``_snapshot`` and land it in the host-durable state bucket.

    The OpenSearch snapshot API (``wait_for_completion``) guarantees a consistent
    point-in-time set BEFORE the repo is tarred and fetched (snapshot spike Task 2).
    """
    name = snapshot_name(datetime.now(tz=UTC))
    base = f"http://{host}:{port}"
    try:
        result = _put_json(
            f"{base}/_snapshot/{SNAPSHOT_REPO}/{name}?wait_for_completion=true",
            {"indices": "logs-*", "ignore_unavailable": True, "include_global_state": False},
        )
    except LogsearchError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    snap_state = result.get("snapshot", {}).get("state")
    if snap_state != "SUCCESS":
        typer.echo(
            f"mqlab logsearch snapshot: OpenSearch reported snapshot '{name}' state "
            f"{snap_state!r} (expected SUCCESS); the point-in-time set is not consistent, "
            "refusing to fetch it.",
            err=True,
        )
        raise typer.Exit(code=1)

    dest = _snapshot_state_dir()
    dest.mkdir(parents=True, exist_ok=True)
    remote_tar = f"/tmp/{name}.tar.gz"  # noqa: S108 - guest-side staging path
    steps = [
        CommandStep(
            "tar snapshot repo on guest",
            Command(archive_argv(INVENTORY_HOST, REMOTE_REPO_DIR, remote_tar), cwd=_ansible_dir()),
        ),
        CommandStep(
            "fetch snapshot to host state bucket",
            Command(fetch_argv(INVENTORY_HOST, remote_tar, str(dest)), cwd=_ansible_dir()),
        ),
    ]
    try:
        _execute_steps("logsearch-snapshot", steps)
    except StepFailedError as exc:
        typer.echo(
            f"mqlab logsearch snapshot: the snapshot '{name}' was taken but the "
            f"guest->host transport failed; it is not durable on the host yet.",
            err=True,
        )
        raise typer.Exit(code=exc.exit_code) from exc

    typer.echo(f"snapshot '{name}' captured and fetched to {dest / (name + '.tar.gz')}")


@app.command("restore")
def restore(
    snapshot_arg: str = typer.Option(
        None, "--snapshot", help="snapshot name to restore (default: the latest on the host)"
    ),
    host: str = _HostOpt,
    port: int = _PortOpt,
) -> None:
    """Stage a host snapshot artifact back to the guest and ``POST _restore``.

    With no ``--snapshot``, restores the latest artifact in the host state bucket.
    An empty store is a LOUD failure here (unlike bring-up's auto-restore, where a
    fresh build with no snapshot is a logged no-op) — an explicit ``restore`` with
    nothing to restore is an operator error, not a silent skip.
    """
    store = _snapshot_state_dir()
    available = sorted(p.name[: -len(".tar.gz")] for p in store.glob("*.tar.gz"))
    name = snapshot_arg or latest_snapshot(available)
    if not name:
        typer.echo(
            f"mqlab logsearch restore: no snapshot artifact found under {store}. "
            "Take one first with 'mqlab logsearch snapshot', or pass --snapshot <name>.",
            err=True,
        )
        raise typer.Exit(code=1)

    local_tar = store / f"{name}.tar.gz"
    if snapshot_arg and not local_tar.exists():
        typer.echo(
            f"mqlab logsearch restore: requested snapshot '{name}' has no artifact at "
            f"{local_tar}. Available: {', '.join(available) or 'none'}.",
            err=True,
        )
        raise typer.Exit(code=1)

    remote_tar = f"/tmp/{name}.tar.gz"  # noqa: S108 - guest-side staging path
    steps = [
        CommandStep(
            "stage snapshot to guest",
            Command(copy_argv(INVENTORY_HOST, str(local_tar), remote_tar), cwd=_ansible_dir()),
        ),
        CommandStep(
            "untar snapshot into guest repo",
            Command(untar_argv(INVENTORY_HOST, REMOTE_REPO_DIR, remote_tar), cwd=_ansible_dir()),
        ),
    ]
    try:
        _execute_steps("logsearch-restore", steps)
    except StepFailedError as exc:
        typer.echo(
            f"mqlab logsearch restore: staging snapshot '{name}' to the guest failed.",
            err=True,
        )
        raise typer.Exit(code=exc.exit_code) from exc

    base = f"http://{host}:{port}"
    try:
        _post_json(f"{base}/_snapshot/{SNAPSHOT_REPO}/{name}/_restore?wait_for_completion=true", {})
    except LogsearchError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"restored snapshot '{name}' from {local_tar}")


def _ansible_dir() -> Path:
    """The ``ansible/`` dir the ad-hoc runs from, so ansible.cfg (inventory) applies
    — same convention as the obs playbook steps in cli.py."""
    return repo_root() / "ansible"
