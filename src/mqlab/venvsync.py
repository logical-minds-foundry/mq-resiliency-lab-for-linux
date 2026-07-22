"""Assert the dev venv matches the lockfile before a lab-lifecycle run (#776).

This repo runs DIRECTLY out of its uv-managed `.venv` — `mqlab` and the ansible
tooling execute straight from the checkout, not an installed package. A PR that
shifts a dependency leaves a stale `.venv`, and the failure surfaces deep inside
a spawned subprocess long after the command started: the motivating case was
`mqlab bootstrap`'s box-bake (`lab/boxes/build-fatbox.sh`) dying with
`ansible-playbook: command not found` (exit 127) because the freshly-recreated
venv wasn't fully synced. A manual `uv sync` fixed it.

`ensure_venv_current` runs `uv sync` up front so the venv matches `uv.lock`
before any venv-dependent subprocess spawns. It is called at the top of the
heavy lab-lifecycle flows only (bootstrap / teardown / box build), not the
trivial read verbs. The `which`/`run` seams are injected in tests so no real
`uv` runs under unit test.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

import typer

from mqlab.paths import repo_root

if TYPE_CHECKING:
    from collections.abc import Callable


def _uv_sync(uv: str) -> subprocess.CompletedProcess[str]:  # pragma: no cover - real subprocess
    return subprocess.run(  # noqa: S603 - trusted uv path from shutil.which; dev-loop tool
        [uv, "sync"],
        cwd=repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )


def ensure_venv_current(
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[[str], subprocess.CompletedProcess[str]] = _uv_sync,
) -> None:
    """Make the dev venv match `uv.lock` before a lab-lifecycle run (#776).

    - `uv` not on PATH  -> loud NOTICE, then return. Some environments don't
      launch via `uv run`; don't hard-fail a working non-uv setup.
    - `uv sync` non-zero -> fail loud (typer.Exit with uv's exit code). A broken
      sync is a real problem — surface it rather than proceed into the cryptic
      downstream subprocess failure it would otherwise cause.
    - success -> return.
    """
    uv = which("uv")
    if uv is None:
        typer.echo(
            "NOTICE: `uv` not found on PATH — skipping lab environment sync. "
            "If dependencies changed, sync the .venv to uv.lock by hand.",
            err=True,
        )
        return
    typer.echo("syncing lab environment…", err=True)
    result = run(uv)
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        typer.echo(
            f"mqlab: `uv sync` failed (rc={result.returncode}) — the lab venv is not "
            f"current, so a spawned tool (e.g. ansible-playbook) may be missing.\n{output}",
            err=True,
        )
        raise typer.Exit(code=result.returncode)
