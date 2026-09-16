"""Version-sync guardrail: obs role defaults must match the shared manifest (#1127).

The shared obs manifest (``manifests/_shared/observability.yaml``) and the per-role
Ansible ``defaults/main.yml`` are two copies of the same version pins — the bake
consumes the role defaults (overlaid by the manifest), while the manifest is what the
version report advertises. A bump to one that does not move the other diverges what we
ship from what we report. This guardrail locks the two in lockstep so drift fails the
pipeline instead of shipping silently.

The exporter ref is triple-locked: the ``mq_exporter_ref`` role default, the manifest's
``mq_metric_samples_ref``, and the container-build constant ``MQ_EXPORTER_REF`` must all
name the same pin, so a bump moves together (this extends the two-way check in
``tests/test_mqexporter.py`` to include the manifest, the third copy).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mqlab import manifest, mqexporter

_ROLES = Path(__file__).resolve().parents[1] / "ansible" / "roles"


def _role_default(role_dir: str, var: str) -> str:
    """The value of ``var`` in a role's ``defaults/main.yml`` ("" if the key is absent).

    An absent pin ("") is meaningful for the apt-float stack (grafana): both the role
    and the manifest must agree on *un*pinned, so pinning one side without the other
    (as a future step-function will) trips this guard until both move together.
    """
    defaults = yaml.safe_load((_ROLES / role_dir / "defaults" / "main.yml").read_text())
    return defaults.get(var, "")


# (role directory, role-default var) for the obs stack. The manifest side is reached
# through the production loader: obs_overlay() is keyed by these same role-var names and
# its value is the shared manifest's pin, so the two are compared apples-to-apples.
# grafana is included though it is apt-float today ("" both sides) — the guard is exactly
# what makes a future one-sided grafana pin fail loudly.
_OBS_PINS = [
    ("prometheus", "prometheus_version"),
    ("node-exporter", "node_exporter_version"),
    ("loki", "loki_version"),
    ("alloy", "alloy_version"),
    ("grafana", "grafana_version"),
]


@pytest.mark.parametrize(("role_dir", "role_var"), _OBS_PINS)
def test_obs_role_default_matches_manifest(role_dir: str, role_var: str) -> None:
    manifest_value = manifest.obs_overlay()[role_var]
    assert _role_default(role_dir, role_var) == manifest_value, (
        f"{role_dir} role default {role_var!r} != shared manifest value {manifest_value!r}"
    )


def test_exporter_ref_agrees_three_ways() -> None:
    # role default == manifest mq_metric_samples_ref == container-build MQ_EXPORTER_REF.
    role_ref = _role_default("mq-exporter", "mq_exporter_ref")
    manifest_ref = manifest.obs_overlay()["mq_exporter_ref"]
    assert role_ref == manifest_ref == mqexporter.MQ_EXPORTER_REF, (
        f"exporter ref drift: role={role_ref!r} manifest={manifest_ref!r} "
        f"MQ_EXPORTER_REF={mqexporter.MQ_EXPORTER_REF!r}"
    )
