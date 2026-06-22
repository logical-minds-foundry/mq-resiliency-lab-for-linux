"""Assemble the CommandStep pipeline for a single-invocation baseline `mqlab run`
of a setup (pivot spec §4.4). The pipeline is one call into lab/scripts/dr-run.sh,
which deploys the flow clients, drives the steady-state flow, and collects the two
ledgers into the run dir. The in-process reconcile/report happens in the CLI after
these steps succeed (see cli.run_setup)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mqlab.orchestrator import CommandStep
from mqlab.paths import lab_script
from mqlab.runner import Command

if TYPE_CHECKING:
    from pathlib import Path

    from mqlab.setups import Setup


def baseline_run_plan(setup: Setup, run_dir: Path, *, seconds: int, rate: int) -> list[CommandStep]:
    if setup.qm is None:
        raise ValueError(f"setup {setup.name!r} has no QM to drive a baseline run")
    qm = setup.qm
    app_ledger = run_dir / "app.jsonl"
    svc_ledger = run_dir / "svc.jsonl"
    command = Command(
        [
            "bash",
            str(lab_script("dr-run.sh")),
            "--setup",
            setup.name,
            "--qm",
            qm.name,
            "--vip",
            qm.vip,
            "--seconds",
            str(seconds),
            "--rate",
            str(rate),
            "--firm-ledger",
            str(app_ledger),
            "--dtcc-ledger",
            str(svc_ledger),
        ]
    )
    label = f"baseline run {setup.name} ({seconds}s @ {rate}/s)"
    return [CommandStep(label, command)]
