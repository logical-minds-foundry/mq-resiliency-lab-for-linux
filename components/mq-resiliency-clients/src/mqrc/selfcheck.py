"""Prove this install can import everything on the interpreter it runs on.

Epic .github#294 spec §5.4 rule 4: import every module in the package, assert the
running interpreter is CPython 3.14, and report versions. When the ``mqi`` extra is
installed (the ``pymqi`` distribution is present, as on every guest), also import the
real compiled pymqi binding, so a broken build against the box's MQ SDK fails the
install instead of the first client run. Versions come from ``importlib.metadata``
only: ``pymqi.__version__`` is stale (it reports 1.12.11 for distribution 1.12.13).
"""

from __future__ import annotations

import importlib
import importlib.metadata
import pkgutil
import sys

import mqrc

DIST = "mq-resiliency-clients"
REQUIRED_MINOR = (3, 14)


def _modules() -> list[str]:
    return [m.name for m in pkgutil.walk_packages(mqrc.__path__, "mqrc.")]


def _mqi_installed() -> bool:
    try:
        importlib.metadata.version("pymqi")
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    if tuple(sys.version_info[:2]) != REQUIRED_MINOR:
        print(
            f"selfcheck: interpreter {sys.version.split()[0]} is not CPython "
            f"{REQUIRED_MINOR[0]}.{REQUIRED_MINOR[1]}",
            file=sys.stderr,
        )
        return 1
    failed = 0
    for name in _modules():
        try:
            importlib.import_module(name)
            print(f"ok {name}")
        except Exception as exc:  # noqa: BLE001 -- every failure is reported, then exit 1
            print(f"FAIL {name}: {exc!r}", file=sys.stderr)
            failed += 1
    if _mqi_installed():
        try:
            importlib.import_module("pymqi")
            print(f"ok pymqi {importlib.metadata.version('pymqi')}")
        except Exception as exc:  # noqa: BLE001 -- a broken binding is reported, then exit 1
            print(f"FAIL pymqi: {exc!r}", file=sys.stderr)
            failed += 1
    else:
        print("pymqi not installed (no mqi extra): real-binding check skipped")
    print(f"{DIST} {importlib.metadata.version(DIST)} on {sys.version.split()[0]}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
