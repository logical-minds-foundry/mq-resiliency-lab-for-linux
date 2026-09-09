"""Every mqlab submodule must import cleanly (#1063).

The bootstrap trap: `mqlab.cli` imports the whole package at startup, so a single
module that fails to import — e.g. syntax the pinned interpreter cannot parse —
kills the CLI before the in-CLI venv self-heal can run. Importing every submodule
here catches that class of regression in CI, whichever module regresses.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

import mqlab


def test_every_mqlab_submodule_imports() -> None:
    failures: dict[str, str] = {}

    def _record(name: str) -> None:
        exc = sys.exc_info()[1]
        failures[name] = f"{type(exc).__name__}: {exc}"

    for info in pkgutil.walk_packages(mqlab.__path__, prefix="mqlab.", onerror=_record):
        if info.name in failures:
            continue
        try:
            importlib.import_module(info.name)
        except Exception as exc:  # report every failure, don't stop at the first
            failures[info.name] = f"{type(exc).__name__}: {exc}"

    assert not failures, "mqlab submodules failed to import:\n" + "\n".join(
        f"  {name}: {err}" for name, err in sorted(failures.items())
    )
