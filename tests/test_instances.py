"""Per-stack instance records (epic .github#280). T2 scaffold: the record type and a
read_record that finds no record for any stack; T3 implements the records."""

from __future__ import annotations

import dataclasses

import pytest

from mqlab import instances
from mqlab.instances import InstanceRecord
from mqlab.versions import OsRef


@pytest.mark.parametrize("stack", ["nativeha-ubuntu", "pcmk-ubuntu", "rdqm-rhel", "ghost"])
def test_read_record_finds_no_record_until_t3(stack):
    assert instances.read_record(stack) is None


def test_instance_record_carries_the_t3_fields():
    rec = InstanceRecord("nativeha-ubuntu", OsRef("ubuntu", 24), None, "2026-10-03T00:00:00Z")
    assert [f.name for f in dataclasses.fields(rec)] == ["stack", "os", "build_file", "created"]
    assert rec.os == OsRef("ubuntu", 24)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(rec, "stack", "other")  # noqa: B010 - exercising the frozen guard
