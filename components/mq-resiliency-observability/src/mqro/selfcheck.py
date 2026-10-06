"""Prove this install can import everything on the interpreter it runs on.

`mq-resiliency-observability-selfcheck` imports every module in the package, asserts the
running interpreter is CPython 3.14, and prints the installed version. The version comes
from ``importlib.metadata`` (the installed distribution's metadata), never from a
module's ``__version__``, which can go stale. The installer runs this on the box and
fails the install on a non-zero exit.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import pkgutil
import platform
import sys

import mqro

DIST = "mq-resiliency-observability"
REQUIRED_MINOR = (3, 14)


def _modules() -> list[str]:
    return [m.name for m in pkgutil.walk_packages(mqro.__path__, "mqro.")]


def _interpreter() -> str:
    return f"{platform.python_implementation()} {sys.version.split()[0]}"


def main(argv: list[str] | None = None) -> int:
    """Entry point: exit 0 only if the interpreter is right and every module imports."""
    del argv  # no options; the signature matches every other entry point
    if platform.python_implementation() != "CPython" or tuple(sys.version_info[:2]) != (
        REQUIRED_MINOR
    ):
        print(
            f"selfcheck: interpreter {_interpreter()} is not CPython "
            f"{REQUIRED_MINOR[0]}.{REQUIRED_MINOR[1]}",
            file=sys.stderr,
        )
        return 1
    failed = 0
    for name in _modules():
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - every failure is reported, then exit 1
            print(f"FAIL {name}: {exc!r}", file=sys.stderr)
            failed += 1
        else:
            print(f"ok {name}")
    print(f"{DIST} {importlib.metadata.version(DIST)} on {_interpreter()}")
    return 1 if failed else 0
