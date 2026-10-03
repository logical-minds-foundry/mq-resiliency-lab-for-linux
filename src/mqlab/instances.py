"""Per-stack instance records: the OS a running stack was built on (epic .github#280).

A record pins a stack's OS major for its whole life, so the version layer
(versions.node_boxes) resolves a running stack's nodes from its record rather than
from the catalog default, which may move under it (spec §4.4).

- ``mqlab bootstrap`` writes the record BEFORE it renders the resolved topology, so
  Vagrant sees the selected boxes from the first ``vagrant up``.
- A ``--config`` that disagrees with an existing record is refused until
  ``mqlab teardown <stack>``.
- A live stack with no record is refused by every command except ``teardown``, which
  destroys domains by name, needs no version, and deletes the record.

Records are JSON under ``build/state/instances/<stack>.json`` (the shared state/
bucket: irreplaceable live-lab facts), written atomically (temp file + os.replace).
A record that cannot be read back fails loudly — the lab never guesses a running
stack's OS.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mqlab import versions
from mqlab.paths import instances_dir

if TYPE_CHECKING:
    from mqlab.versions import OsRef

_KEYS = ("stack", "os", "build_file", "created")


@dataclass(frozen=True)
class InstanceRecord:
    stack: str
    os: OsRef
    build_file: str | None  # the --config path as given, or None
    created: str  # ISO-8601 UTC


def _teardown_fix(stack: str) -> str:
    return f"run `mqlab teardown {stack}` and re-bootstrap"


def record_path(stack: str) -> Path:
    """Where ``stack``'s record lives (through the build-layout API, never hard-coded)."""
    return instances_dir() / f"{stack}.json"


def _corrupt(stack: str, path: Path, why: str) -> versions.VersionError:
    return versions.VersionError(
        f"instance record {path} for {stack} is unreadable ({why}) — {_teardown_fix(stack)}"
    )


def _parse(stack: str, path: Path, text: str) -> InstanceRecord:
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _corrupt(stack, path, f"not valid JSON: {exc}") from None
    if not isinstance(data, dict) or sorted(data) != sorted(_KEYS):
        raise _corrupt(stack, path, f"expected exactly the keys {', '.join(_KEYS)}")
    if data["stack"] != stack:
        raise _corrupt(stack, path, f"it names stack {data['stack']!r}")
    build_file = data["build_file"]
    if not isinstance(data["os"], str) or not isinstance(data["created"], str):
        raise _corrupt(stack, path, "os and created must be strings")
    if build_file is not None and not isinstance(build_file, str):
        raise _corrupt(stack, path, "build_file must be a string or null")
    try:
        ref = versions.OsRef.parse(data["os"])
    except versions.VersionError as exc:
        raise _corrupt(stack, path, str(exc)) from None
    return InstanceRecord(stack=stack, os=ref, build_file=build_file, created=data["created"])


def read_record(stack: str) -> InstanceRecord | None:
    """The instance record for ``stack``, or None when it has none.

    A record that exists but cannot be parsed is a VersionError naming the fix."""
    path = record_path(stack)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    return _parse(stack, path, text)


def write_record(rec: InstanceRecord) -> None:
    """Write ``rec`` atomically: a temp file in the same directory, then os.replace."""
    path = record_path(rec.stack)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "stack": rec.stack,
        "os": str(rec.os),
        "build_file": rec.build_file,
        "created": rec.created,
    }
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{rec.stack}.", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
        tmp.replace(path)  # os.replace: atomic within one filesystem
    except BaseException:
        tmp.unlink()
        raise


def delete_record(stack: str) -> None:
    """Delete ``stack``'s record; a missing record is not an error."""
    record_path(stack).unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconcile(
    stack: str, requested: OsRef, *, live: bool, build_file: str | None
) -> InstanceRecord:
    """Check ``requested`` against ``stack``'s record and return the record to run with.

    - No record, not live: write and return a new record.
    - No record, live: VersionError (run ``mqlab teardown <stack>`` and re-bootstrap).
    - Record at ``requested``: return it unchanged.
    - Record at another OS: VersionError naming ``mqlab teardown <stack>``.
    """
    existing = read_record(stack)
    if existing is None:
        if live:
            raise _live_without_record(stack)
        rec = InstanceRecord(stack=stack, os=requested, build_file=build_file, created=_now())
        write_record(rec)
        return rec
    if existing.os != requested:
        raise versions.VersionError(
            f"{stack} is running {existing.os}; requested {requested}; "
            f"run `mqlab teardown {stack}` first"
        )
    return existing


def _live_without_record(stack: str) -> versions.VersionError:
    return versions.VersionError(
        f"{stack} is running without a version record; {_teardown_fix(stack)} "
        f"(teardown destroys its domains by name, then `mqlab bootstrap {stack}` records "
        "the OS it builds)"
    )


def require_record_if_live(stack: str, *, live: bool) -> InstanceRecord | None:
    """``stack``'s record; None only when the stack is not live and has none.

    A live stack with no record is a VersionError: the lab never falls back to the
    stack default for a running instance (after a default flip that would mislabel it).
    """
    rec = read_record(stack)
    if rec is None and live:
        raise _live_without_record(stack)
    return rec
