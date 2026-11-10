from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mqlab.cli import app
from mqlab.parity import (
    MATRIX,
    VERBS,
    Support,
    render_markdown,
    supported,
)


def test_native_ha_arms_support_every_verb() -> None:
    for arm in ("nativeha-rhel-crr", "nativeha-ubuntu"):
        assert all(supported(arm, v) is Support.SUPPORTED for v in VERBS)


def test_rdqm_starts_not_yet_everywhere() -> None:
    assert all(supported("rdqm-rhel", v) is Support.NOT_YET for v in VERBS)


def test_supported_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown arm/verb"):
        supported("nope", "failover")
    with pytest.raises(KeyError, match="unknown arm/verb"):
        supported("nativeha-ubuntu", "nope")


def test_render_markdown_has_a_row_per_verb_and_arm_columns() -> None:
    text = render_markdown()
    assert "| verb | rdqm-rhel | nativeha-rhel-crr | nativeha-ubuntu |" in text
    for v in VERBS:
        assert f"| {v} |" in text
    assert "not_yet" in text
    assert "supported" in text


def test_matrix_covers_exactly_the_declared_arms() -> None:
    assert set(MATRIX) == {
        "rdqm-rhel",
        "nativeha-rhel-crr",
        "nativeha-ubuntu",
    }


def test_parity_command_prints_matrix() -> None:
    result = CliRunner().invoke(app, ["parity"])
    assert result.exit_code == 0
    assert "nativeha-ubuntu" in result.stdout
    assert "not_yet" in result.stdout
