from __future__ import annotations

import pytest

from mqlab.arms import VerbImpl, arm_of, lab_arms, resolve_verb
from mqlab.parity import MATRIX

TOPO = (
    "nodes:\n  san-a: {}\n  pcmk-a1: {}\n  rdqm-a1: {}\n"
    "groups:\n  san_a: [san-a]\n  pcmk_a: [pcmk-a1]\n  rdqm_a: [rdqm-a1]\n"
    "arms:\n"
    "  pcmk-ubuntu:\n"
    "    mechanism: pacemaker-san\n"
    "    verbs:\n"
    "      qm-create: { playbook: site-pcmk-qm.yml }\n"
    "      qm-up: { pcs: resource enable mq_group }\n"
    "  rdqm-rhel:\n"
    "    mechanism: rdqm\n"
    "    verbs: {}\n"
    "setups:\n"
    "  pcmk_san_ha:\n"
    "    arm: pcmk-ubuntu\n"
    "    groups: [san_a, pcmk_a]\n"
    "  rdqm_ha:\n"
    "    arm: rdqm-rhel\n"
    "    groups: [rdqm_a]\n"
    "  monitoring:\n"
    "    groups: []\n"
)


def _seed(tmp_path):
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(TOPO)


def test_lab_arms_loads_pcmk_with_verbs(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    arms = lab_arms()
    assert arms["pcmk-ubuntu"].mechanism == "pacemaker-san"
    assert "qm-create" in arms["pcmk-ubuntu"].verbs
    assert arms["rdqm-rhel"].verbs == {}


def test_arm_of_returns_the_setups_arm(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    assert arm_of("pcmk_san_ha") == "pcmk-ubuntu"


def test_arm_of_raises_for_arm_agnostic_setup(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    with pytest.raises(ValueError, match="no arm"):
        arm_of("monitoring")


def test_resolve_verb_returns_kind_and_value(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    assert resolve_verb("pcmk_san_ha", "qm-create") == VerbImpl(kind="playbook", value="site-pcmk-qm.yml")
    assert resolve_verb("pcmk_san_ha", "qm-up") == VerbImpl(kind="pcs", value="resource enable mq_group")


def test_resolve_verb_unsupported_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    with pytest.raises(KeyError, match="does not implement"):
        resolve_verb("rdqm_ha", "qm-create")  # rdqm-rhel has no verbs yet


def test_registry_arms_match_the_capability_matrix() -> None:
    # the real topology registry and parity.MATRIX must agree on the arm set —
    # one source of arm truth (reads the real lab/topology.yaml, no seeding)
    assert set(lab_arms()) == set(MATRIX)
