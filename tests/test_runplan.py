from __future__ import annotations

import pytest

from mqlab.runplan import baseline_run_plan
from mqlab.setups import QmConfig, Setup


def _setup(qm: QmConfig | None) -> Setup:
    return Setup(
        name="distributed",
        description="d",
        groups=["san_a", "pcmk_a", "dtcc", "app"],
        provision="ansible/site-distributed.yml",
        secrets=[],
        qm=qm,
    )


def test_baseline_plan_is_one_step_invoking_dr_run(tmp_path) -> None:
    qm = QmConfig(name="QMPCMK", vip="10.10.1.200", vip_ext="10.60.0.10", dtcc_conn="10.60.0.50")
    run_dir = tmp_path / "20260615T143000Z-distributed"
    steps = baseline_run_plan(_setup(qm), run_dir, seconds=30, rate=20)
    assert len(steps) == 1
    step = steps[0]
    assert "distributed" in step.label
    argv = step.command.argv
    assert argv[0] == "bash"
    assert "--qm" in argv
    assert "QMPCMK" in argv
    assert "--vip" in argv
    assert "10.10.1.200" in argv
    assert "--seconds" in argv
    assert "30" in argv
    assert str(run_dir / "firm.jsonl") in argv
    assert str(run_dir / "dtcc.jsonl") in argv


def test_baseline_plan_requires_a_qm(tmp_path) -> None:
    with pytest.raises(ValueError, match="no QM"):
        baseline_run_plan(_setup(None), tmp_path, seconds=30, rate=20)
