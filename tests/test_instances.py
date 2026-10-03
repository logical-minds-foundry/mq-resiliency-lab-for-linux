"""Per-stack instance records (epic .github#280, spec §4.4).

A record pins the OS a stack was built on for its whole life. These tests run against
the per-test instances dir the autouse ``tmp_state`` fixture (conftest) points the
module at, so they never touch the developer's build/state.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from mqlab import instances, topology, versions
from mqlab.instances import InstanceRecord
from mqlab.versions import OsRef, VersionError, load_catalog
from tests.boxfleet import REAL_CATALOG

if TYPE_CHECKING:
    from pathlib import Path

U24 = OsRef("ubuntu", 24)
U26 = OsRef("ubuntu", 26)


def _committed() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(REAL_CATALOG.read_text())
    return data


def _catalog_file(tmp_path: Path, defaults: dict[str, str]) -> Path:
    """The committed catalog plus ubuntu:26, offered to both Ubuntu stacks."""
    data = copy.deepcopy(_committed())
    data["os"]["ubuntu"][26] = {"base_box": "cloud-image/ubuntu-26.04"}
    for stack in ("nativeha-ubuntu", "pcmk-ubuntu"):
        data["stacks"][stack] = {
            "supported": ["ubuntu:24", "ubuntu:26"],
            "default": defaults.get(stack, "ubuntu:24"),
        }
    path = tmp_path / "versions.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.fixture
def catalog_24_26(tmp_path):
    return load_catalog(_catalog_file(tmp_path, {}))


@pytest.fixture
def catalog_with_default(tmp_path):
    def make(stack: str, default: str) -> versions.Catalog:
        return load_catalog(_catalog_file(tmp_path, {stack: default}))

    return make


# --- The record type -------------------------------------------------------------------


def test_instance_record_fields():
    rec = InstanceRecord("nativeha-ubuntu", U24, None, "2026-10-03T00:00:00Z")
    assert [f.name for f in dataclasses.fields(rec)] == ["stack", "os", "build_file", "created"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(rec, "stack", "other")  # noqa: B010 - exercising the frozen guard


def test_record_path_is_under_the_instances_dir(tmp_state):
    assert instances.record_path("rdqm-rhel") == tmp_state / "rdqm-rhel.json"


# --- read / write / delete -------------------------------------------------------------


@pytest.mark.parametrize("stack", ["nativeha-ubuntu", "pcmk-ubuntu", "rdqm-rhel", "ghost"])
def test_read_record_none_when_absent(stack):
    assert instances.read_record(stack) is None


def test_write_then_read_round_trips(tmp_state):
    rec = InstanceRecord("rdqm-rhel", OsRef("rhel", 9), "b.yaml", "2026-10-03T01:02:03Z")
    instances.write_record(rec)
    assert instances.read_record("rdqm-rhel") == rec
    assert json.loads((tmp_state / "rdqm-rhel.json").read_text()) == {
        "stack": "rdqm-rhel",
        "os": "rhel:9",
        "build_file": "b.yaml",
        "created": "2026-10-03T01:02:03Z",
    }


def test_write_creates_the_dir_and_leaves_no_temp_file(tmp_state, monkeypatch):
    target = tmp_state / "fresh" / "instances"  # build/state/instances not yet created
    monkeypatch.setattr(instances, "instances_dir", lambda: target)
    instances.write_record(InstanceRecord("s", U24, None, "t"))
    assert sorted(p.name for p in target.iterdir()) == ["s.json"]


def test_write_is_atomic_and_cleans_up_on_failure(tmp_state, monkeypatch):
    instances.write_record(InstanceRecord("s", U24, None, "old"))

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(instances.json, "dump", boom)
    with pytest.raises(OSError, match="disk full"):
        instances.write_record(InstanceRecord("s", U26, None, "new"))
    # the old record is intact and the temp file is gone
    assert instances.read_record("s") == InstanceRecord("s", U24, None, "old")
    assert sorted(p.name for p in tmp_state.iterdir()) == ["s.json"]


def test_delete_record(tmp_state):
    instances.write_record(InstanceRecord("s", U24, None, "t"))
    instances.delete_record("s")
    assert instances.read_record("s") is None


def test_delete_missing_record_is_not_an_error():
    instances.delete_record("never-bootstrapped")  # no exception


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("{not json", "not valid JSON"),
        ("[]", "expected exactly the keys stack, os, build_file, created"),
        ('{"stack": "s", "os": "ubuntu:24"}', "expected exactly the keys"),
        (
            '{"stack": "other", "os": "ubuntu:24", "build_file": null, "created": "t"}',
            "it names stack 'other'",
        ),
        (
            '{"stack": "s", "os": 24, "build_file": null, "created": "t"}',
            "os and created must be strings",
        ),
        (
            '{"stack": "s", "os": "ubuntu:24", "build_file": null, "created": 1}',
            "os and created must be strings",
        ),
        (
            '{"stack": "s", "os": "ubuntu:24", "build_file": 7, "created": "t"}',
            "build_file must be a string or null",
        ),
        (
            '{"stack": "s", "os": "debian:12", "build_file": null, "created": "t"}',
            "bad OS reference",
        ),
    ],
)
def test_unreadable_record_fails_loud(tmp_state, text, why):
    (tmp_state / "s.json").write_text(text)
    with pytest.raises(VersionError) as exc:
        instances.read_record("s")
    msg = str(exc.value)
    assert why in msg
    assert msg.startswith(f"instance record {tmp_state / 's.json'} for s is unreadable (")
    assert msg.endswith("— run `mqlab teardown s` and re-bootstrap")


# --- reconcile / require_record_if_live ------------------------------------------------


def test_first_bootstrap_writes_record():
    rec = instances.reconcile("nativeha-ubuntu", U24, live=False, build_file=None)
    assert instances.read_record("nativeha-ubuntu") == rec
    assert (rec.stack, rec.os, rec.build_file) == ("nativeha-ubuntu", U24, None)
    assert rec.created.endswith("Z")


def test_first_bootstrap_records_the_build_file_as_given():
    rec = instances.reconcile("s", U26, live=False, build_file="cfg/b.yaml")
    assert instances.read_record("s") == rec
    assert rec.build_file == "cfg/b.yaml"


def test_config_matching_record_accepted():  # Review Focus 2
    instances.reconcile("s", U24, live=False, build_file="a.yaml")
    assert instances.reconcile("s", U24, live=True, build_file="a.yaml").os == U24


def test_matching_record_is_returned_unchanged():
    first = instances.reconcile("s", U24, live=False, build_file="a.yaml")
    again = instances.reconcile("s", U24, live=True, build_file=None)
    assert again == first  # the original build_file/created are kept


def test_config_conflicting_record_refused():  # Review Focus 2
    instances.reconcile("s", U24, live=False, build_file=None)
    with pytest.raises(
        VersionError, match=r"running ubuntu:24; requested ubuntu:26; run `mqlab teardown s` first"
    ):
        instances.reconcile("s", U26, live=True, build_file="b.yaml")
    rec = instances.read_record("s")
    assert rec is not None and rec.os == U24  # the record is untouched


def test_conflict_refused_even_when_not_live():
    """A record outlives its domains until teardown deletes it: a stopped stack is still
    pinned, so a different OS is refused regardless of liveness."""
    instances.reconcile("s", U24, live=False, build_file=None)
    with pytest.raises(VersionError, match=r"run `mqlab teardown s` first"):
        instances.reconcile("s", U26, live=False, build_file=None)


def test_reconcile_live_without_record_refused():
    with pytest.raises(
        VersionError, match=r"running without a version record; run `mqlab teardown s`"
    ):
        instances.reconcile("s", U24, live=True, build_file=None)
    assert instances.read_record("s") is None  # nothing written


def test_live_without_record_refused():
    with pytest.raises(
        VersionError, match=r"running without a version record; run `mqlab teardown s`"
    ):
        instances.require_record_if_live("s", live=True)


def test_require_record_returns_the_record_when_live():
    rec = instances.reconcile("s", U24, live=False, build_file=None)
    assert instances.require_record_if_live("s", live=True) == rec


def test_require_record_none_when_not_live_and_unrecorded():
    assert instances.require_record_if_live("s", live=False) is None


# --- The version layer reads the records (Review Focus 1, 3) ---------------------------


def test_record_pins_through_default_change(catalog_with_default):  # Review Focus 3
    instances.reconcile("nativeha-ubuntu", U24, live=False, build_file=None)
    nb = versions.node_boxes(topology.load(), catalog_with_default("nativeha-ubuntu", "ubuntu:26"))
    assert nb["nha-ubuntu-a1"].name == "mq-nativeha-ubuntu24"


def test_unrecorded_stack_follows_the_default_change(catalog_with_default):
    """The contrast to Review Focus 3: with no record, the default flip does apply."""
    nb = versions.node_boxes(topology.load(), catalog_with_default("nativeha-ubuntu", "ubuntu:26"))
    assert nb["nha-ubuntu-a1"].name == "mq-nativeha-ubuntu26"


def test_render_combines_records_across_stacks(catalog_24_26):  # Review Focus 1
    instances.write_record(InstanceRecord("nativeha-ubuntu", U24, None, "t"))
    instances.write_record(InstanceRecord("pcmk-ubuntu", U26, None, "t"))
    nb = versions.node_boxes(topology.load(), catalog_24_26)
    assert (nb["nha-ubuntu-a1"].name, nb["pcmk-a1"].name) == (
        "mq-nativeha-ubuntu24",
        "pcmk-ubuntu26",
    )
    assert nb["obs"].name == "obs-ubuntu24"  # the shared nodes get the infra boxes
    assert nb["svc-sim"].name == "mq-client-ubuntu24"
