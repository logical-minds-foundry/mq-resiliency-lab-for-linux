from __future__ import annotations

import pytest

from mqlab.parity import (
    MATRIX,
    VERBS,
    Support,
    provisional_arm,
    render_markdown,
    supported,
)


def test_pcmk_is_reference_backend_all_supported() -> None:
    assert all(supported("pcmk-ubuntu", v) is Support.SUPPORTED for v in VERBS)


def test_rdqm_starts_not_yet_everywhere() -> None:
    assert all(supported("rdqm-rhel", v) is Support.NOT_YET for v in VERBS)


def test_supported_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown arm/verb"):
        supported("nope", "failover")
    with pytest.raises(KeyError, match="unknown arm/verb"):
        supported("pcmk-ubuntu", "nope")


def test_render_markdown_has_a_row_per_verb_and_arm_columns() -> None:
    text = render_markdown()
    assert "| verb | pcmk-ubuntu | rdqm-rhel |" in text
    for v in VERBS:
        assert f"| {v} |" in text
    assert "not_yet" in text
    assert "supported" in text


def test_matrix_covers_exactly_the_declared_arms() -> None:
    assert set(MATRIX) == {"pcmk-ubuntu", "rdqm-rhel"}


@pytest.mark.parametrize(
    ("setup", "arm"),
    [
        ("distributed", "pcmk-ubuntu"),
        ("pcmk_san_ha", "pcmk-ubuntu"),
        ("pcmk_san_dr", "pcmk-ubuntu"),
        ("rdqm_ha", "rdqm-rhel"),
        ("rdqm_dr", "rdqm-rhel"),
    ],
)
def test_provisional_arm_maps_known_setups(setup: str, arm: str) -> None:
    assert provisional_arm(setup) == arm


def test_provisional_arm_unknown_raises() -> None:
    with pytest.raises(KeyError, match="no provisional arm"):
        provisional_arm("monitoring")
