"""Per-stack instance records: the OS a running stack was built on (epic .github#280).

A record pins a stack's OS major for its whole life, so the version layer
(versions.node_boxes) resolves a running stack's nodes from its record rather than
from the catalog default, which may move under it.

T2 SCAFFOLD: this module carries only the record type and ``read_record``, which
returns None for every stack, so every stack resolves to its catalog default. T3
(--config build files and instance records) implements writing, reading, deleting
and reconciling records behind these same signatures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mqlab.versions import OsRef


@dataclass(frozen=True)
class InstanceRecord:
    stack: str
    os: OsRef
    build_file: str | None  # the --config path as given, or None
    created: str  # ISO-8601 UTC


def read_record(stack: str) -> InstanceRecord | None:  # noqa: ARG001 - T3 reads build/state
    """The instance record for ``stack``, or None when it has none.

    No records exist until T3 implements them, so this is always None."""
    return None
