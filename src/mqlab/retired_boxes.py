"""Box names retired by the ``<role>-<os><major>`` rename (epic .github#280, #1274).

Every box name changed when box names became generated from the catalog
(lab/versions.yaml). These are the names that existed before. They are kept here
only so the one-time migration can find and remove them:

- ``mqlab box gc`` deregisters them from Vagrant, deletes their dead cache
  artifacts and reclaims their libvirt base-image volumes;
- bootstrap's box_meta reconcile forgets any guest whose cached Vagrant metadata
  still names one, so a retired box is never booted;
- ``mqlab build migrate`` renames the retired RHEL base-box cache to its new name.

This module stands alone on purpose: the version-token guardrail exempts this whole
file, because it is the one place the old version-bearing names stay written out.
"""

from __future__ import annotations

RETIRED_BOX_NAMES: tuple[str, ...] = (
    "obs-ubuntu2404",
    "infra-ubuntu2404",
    "mq-ubuntu2404",
    "mq-nativeha-ubuntu",
    "pcmk-ubuntu",
    "rhel/9.6-x86_64",
)

# The retired RHEL base box -> (its old cache filename under build/state/boxes/, the
# new base box it became). A base box carries no manifest hash, so its cached image is
# still good under the new name: `mqlab build migrate` renames the file rather than
# forcing a 45-90 minute rebuild from the DVD. The retired FAT boxes' caches are dead
# (their manifest hash names the box, so they can never be REUSEd under a new name);
# `mqlab box gc` deletes those.
RETIRED_BASE_ARTIFACTS: dict[str, tuple[str, str]] = {
    "rhel/9.6-x86_64": ("rhel-9.6-x86_64-libvirt.box", "rhel/9-x86_64"),
}
