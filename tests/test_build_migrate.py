"""`mqlab build migrate` — per-host box-cache rename to the arch-suffixed scheme
(#103 D5, T4).

The pre-#103 cache keyed boxes purely by name (`<box>.box`); the arch-native
scheme keys them `<box>-<arch>.box`. Because the cache is per-host single-arch and
every legacy fat box was x86_64-pinned, the migration maps each legacy name to its
`-x86_64` form (the `.box` and its `.manifest-hash`), idempotently. These tests
seed a tmp boxes/ dir directly — no real cache is touched.
"""

from __future__ import annotations

from pathlib import Path

from mqlab import box

_LEGACY_BOX = "mq-ubuntu2404.box"
_LEGACY_HASH = "mq-ubuntu2404.manifest-hash"
_NEW_BOX = "mq-ubuntu2404-x86_64.box"
_NEW_HASH = "mq-ubuntu2404-x86_64.manifest-hash"


def test_migrate_renames_legacy_box_and_hash(tmp_path):
    (tmp_path / _LEGACY_BOX).write_text("b")
    (tmp_path / _LEGACY_HASH).write_text("h")
    renamed = box.migrate_box_cache(tmp_path)
    assert not (tmp_path / _LEGACY_BOX).exists()
    assert not (tmp_path / _LEGACY_HASH).exists()
    assert (tmp_path / _NEW_BOX).read_text() == "b"
    assert (tmp_path / _NEW_HASH).read_text() == "h"
    pairs = {(Path(s).name, Path(d).name) for s, d in renamed}
    assert (_LEGACY_BOX, _NEW_BOX) in pairs
    assert (_LEGACY_HASH, _NEW_HASH) in pairs


def test_migrate_is_idempotent(tmp_path):
    (tmp_path / _LEGACY_BOX).write_text("b")
    (tmp_path / _LEGACY_HASH).write_text("h")
    box.migrate_box_cache(tmp_path)
    assert box.migrate_box_cache(tmp_path) == []  # second run: nothing left to move


def test_migrate_dry_run_plans_without_renaming(tmp_path):
    (tmp_path / _LEGACY_BOX).write_text("b")
    planned = box.migrate_box_cache(tmp_path, dry_run=True)
    assert (tmp_path / _LEGACY_BOX).exists()  # left in place
    assert not (tmp_path / _NEW_BOX).exists()
    assert any(Path(s).name == _LEGACY_BOX for s, _ in planned)


def test_migrate_skips_when_target_already_present(tmp_path):
    # Mixed old/new cache: the arch-suffixed target already exists. Leave the legacy
    # file untouched (no clobber, no error) — a hand-resolvable state, not a rename.
    (tmp_path / _LEGACY_BOX).write_text("legacy")
    (tmp_path / _NEW_BOX).write_text("new")
    renamed = box.migrate_box_cache(tmp_path)
    assert (tmp_path / _LEGACY_BOX).read_text() == "legacy"
    assert (tmp_path / _NEW_BOX).read_text() == "new"
    assert all(Path(s).name != _LEGACY_BOX for s, _ in renamed)


def test_migrate_ignores_base_box_and_empty_dir(tmp_path):
    # Only the base box artifact (already arch-tagged, no manifest-hash) is present:
    # migrate leaves it alone and reports nothing. Covers the base-box skip and the
    # no-legacy-file branches.
    (tmp_path / "rhel-9.6-x86_64-libvirt.box").write_text("x")
    assert box.migrate_box_cache(tmp_path) == []
    assert (tmp_path / "rhel-9.6-x86_64-libvirt.box").exists()


def test_migrate_defaults_to_the_boxes_cache_dir(monkeypatch, tmp_path):
    # With no dir argument it targets the durable box cache dir (build/state/boxes).
    monkeypatch.setattr(box, "_boxes_cache_dir", lambda: tmp_path)
    (tmp_path / _LEGACY_BOX).write_text("b")
    renamed = box.migrate_box_cache()
    assert (tmp_path / _NEW_BOX).exists()
    assert any(Path(d).name == _NEW_BOX for _, d in renamed)
