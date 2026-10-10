"""MQLAB_ENV auto-detection from the platform (#1245, epic .github#275).

Precedence: an explicit MQLAB_ENV always wins (an unknown value fails loud); unset/empty
-> the detected platform (Apple Virtualization -> macos, Google Compute Engine -> cloud);
inconclusive -> the base topology, said visibly. The effective env + its source is
printed once per bootstrap/teardown/commons-up and recorded in the perf report.

conftest's autouse `_unknown_platform` makes detection inconclusive in every test; the
tests here point DMI at their own fake sysfs dir and stub the systemd-detect-virt
fallback. The real `run_detect_virt` is the one captured at import, before that stub.
"""

from __future__ import annotations

import copy
import subprocess
from typing import TYPE_CHECKING

import pytest

from mqlab import topology as t
from mqlab.topology import run_detect_virt as real_run_detect_virt
from tests import test_cli_hugepages as hp
from tests import test_cli_teardown as tear
from tests import test_env_profiles as prof

if TYPE_CHECKING:
    from pathlib import Path

# Observed on the macOS dev VM (issue #1245); GCE per Google's detect-compute-engine doc
# and systemd's virt.c dmi_vendor_table (product_name prefix "Google Compute Engine").
APPLE = ("Apple Inc.", "Apple Virtualization Generic Platform")
GCE = ("Google", "Google Compute Engine")


def _dmi(tmp_path: Path, vendor: str | None, product: str | None) -> Path:
    root = tmp_path / "dmi"
    root.mkdir(exist_ok=True)
    if vendor is not None:
        (root / "sys_vendor").write_text(vendor + "\n")
    if product is not None:
        (root / "product_name").write_text(product + "\n")
    return root


def _platform(monkeypatch, tmp_path, vendor, product, virt=(None, "detect-virt: none")):
    monkeypatch.setattr(t, "DMI_ROOT", _dmi(tmp_path, vendor, product))
    monkeypatch.setattr(t, "run_detect_virt", lambda: virt)


# --------------------------------------------------------------------------- #
# detect_platform
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("dmi", "env"), [(APPLE, "macos"), (GCE, "cloud")])
def test_dmi_maps_each_platform(monkeypatch, tmp_path, dmi, env):
    def no_fallback():
        pytest.fail("a conclusive DMI match must not shell systemd-detect-virt")

    monkeypatch.setattr(t, "run_detect_virt", no_fallback)
    got, evidence = t.detect_platform(_dmi(tmp_path, *dmi))
    assert got == env
    assert f"product_name={dmi[1]!r}" in evidence
    assert f"sys_vendor={dmi[0]!r}" in evidence


def test_default_dmi_root_is_module_attribute(monkeypatch, tmp_path):
    _platform(monkeypatch, tmp_path, *APPLE)
    assert t.detect_platform()[0] == "macos"


def test_real_mac_hardware_is_not_apple_virtualization(monkeypatch, tmp_path):
    """sys_vendor "Apple Inc." alone (bare-metal Linux on a Mac) is not the dev VM."""
    _platform(monkeypatch, tmp_path, "Apple Inc.", "MacBookPro16,1")
    env, evidence = t.detect_platform()
    assert env is None
    assert evidence == "DMI product_name='MacBookPro16,1'; detect-virt: none"


@pytest.mark.parametrize(
    ("virt", "env"),
    [(("macos", "systemd-detect-virt=apple"), "macos"), ((None, "unavailable"), None)],
)
def test_no_dmi_falls_back_to_detect_virt(monkeypatch, tmp_path, virt, env):
    _platform(monkeypatch, tmp_path, None, None, virt=virt)
    got, evidence = t.detect_platform()
    assert got == env
    assert evidence == f"DMI product_name=None; {virt[1]}"


def test_empty_product_name_falls_back(monkeypatch, tmp_path):
    _platform(monkeypatch, tmp_path, "", "", virt=("cloud", "systemd-detect-virt=google"))
    assert t.detect_platform()[0] == "cloud"


# --------------------------------------------------------------------------- #
# run_detect_virt (the real one) — bounded, never raises
# --------------------------------------------------------------------------- #
def _fake_run(monkeypatch, *, stdout="", returncode=0, exc=None):
    seen: dict[str, object] = {}

    def run(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(t.subprocess, "run", run)
    return seen


@pytest.mark.parametrize(("out", "env"), [("apple\n", "macos"), ("google\n", "cloud")])
def test_detect_virt_maps_ids(monkeypatch, out, env):
    seen = _fake_run(monkeypatch, stdout=out)
    assert real_run_detect_virt() == (env, f"systemd-detect-virt={out.strip()}")
    assert seen["argv"] == ["systemd-detect-virt"]  # bare name via PATH
    assert seen["timeout"] == t.DETECT_VIRT_TIMEOUT
    assert seen["check"] is False
    assert seen["stdin"] is subprocess.DEVNULL  # own stdin, never mqlab's fd 0 (#1420)


@pytest.mark.parametrize(
    ("out", "code", "evidence"),
    [
        ("none\n", 1, "systemd-detect-virt=none (exit 1)"),
        ("kvm\n", 0, "systemd-detect-virt=kvm (exit 0)"),
        ("", 1, "systemd-detect-virt=<no output> (exit 1)"),
    ],
)
def test_detect_virt_unmapped_is_inconclusive(monkeypatch, out, code, evidence):
    _fake_run(monkeypatch, stdout=out, returncode=code)
    assert real_run_detect_virt() == (None, evidence)


@pytest.mark.parametrize(
    "exc",
    [
        FileNotFoundError(2, "No such file or directory"),
        subprocess.TimeoutExpired(["systemd-detect-virt"], 5.0),
    ],
)
def test_detect_virt_failure_is_inconclusive_and_noted(monkeypatch, exc):
    _fake_run(monkeypatch, exc=exc)
    env, evidence = real_run_detect_virt()
    assert env is None
    assert evidence.startswith(f"systemd-detect-virt unavailable ({type(exc).__name__}: ")


# --------------------------------------------------------------------------- #
# resolve_env — precedence
# --------------------------------------------------------------------------- #
def test_explicit_wins_over_detection(monkeypatch, tmp_path):
    _platform(monkeypatch, tmp_path, *APPLE)
    res = t.resolve_env({t.ENV_VAR: "cloud"})
    assert (res.env, res.source) == ("cloud", t.EXPLICIT)
    assert res.describe() == "environment: cloud (explicit MQLAB_ENV=cloud)"


def test_explicit_reads_process_environment_by_default(monkeypatch):
    monkeypatch.setenv(t.ENV_VAR, "macos")
    assert t.resolve_env().source == t.EXPLICIT


def test_unknown_explicit_fails_loud_even_on_a_known_platform(monkeypatch, tmp_path):
    _platform(monkeypatch, tmp_path, *APPLE)
    with pytest.raises(ValueError, match="MQLAB_ENV='bogus' is not a known environment"):
        t.resolve_env({t.ENV_VAR: "bogus"})


@pytest.mark.parametrize(("dmi", "env"), [(APPLE, "macos"), (GCE, "cloud")])
@pytest.mark.parametrize("environ", [{}, {"MQLAB_ENV": ""}])
def test_unset_or_empty_uses_detected(monkeypatch, tmp_path, dmi, env, environ):
    _platform(monkeypatch, tmp_path, *dmi)
    res = t.resolve_env(environ)
    assert (res.env, res.source) == (env, t.DETECTED)
    assert res.describe().startswith(f"environment: {env} (detected: DMI product_name=")


def test_inconclusive_uses_base_and_says_so():
    res = t.resolve_env({})  # conftest's unknown platform
    assert (res.env, res.source) == (None, t.INCONCLUSIVE)
    assert res.describe().startswith(
        "MQLAB_ENV not set and platform not recognised — using base topology ("
    )
    assert "product_name='Unknown Test Platform'" in res.describe()


def test_detected_profile_applies_to_effective(monkeypatch, tmp_path):
    _platform(monkeypatch, tmp_path, *APPLE)
    assert t.effective(copy.deepcopy(prof.BASE))["boot_batch"] == 2  # macos profile


def test_inconclusive_effective_is_the_base():
    assert t.effective(copy.deepcopy(prof.BASE))["boot_batch"] == 4


# --------------------------------------------------------------------------- #
# CLI: printed once per run, recorded in the perf report, bootstrap/teardown agree
# --------------------------------------------------------------------------- #
PROFILE = "env_profiles:\n  macos:\n    memory_backing: hugepages\n  cloud: {}\n"


def test_bootstrap_prints_and_records_the_detected_env(monkeypatch, tmp_path):
    """No MQLAB_ENV on an Apple Virtualization host -> macos, and its huge-page lever."""
    path = hp._meminfo(monkeypatch, tmp_path)
    runner = hp._ArgvRunner(
        on_reserve=lambda: path.write_text(hp.MEMINFO.format(total=hp.NEEDED_ALL))
    )
    _platform(monkeypatch, tmp_path, *APPLE)
    result, buf = hp._bootstrap(monkeypatch, tmp_path, runner, topo=hp.boot.TOPO + PROFILE)
    assert result.exit_code == 0, result.output
    out = buf.getvalue()
    # printed once as a run note (the perf summary's Notes echo the recorded copy)
    assert out.count("· environment: macos (detected: DMI product_name=") == 1
    assert any(hp.hugepages.PLAYBOOK in c.argv for c in runner.recorded)  # lever applied
    report = hp._report(tmp_path)
    assert (report["env"], report["env_source"]) == ("macos", "detected")
    assert any(n.startswith("environment: macos (detected") for n in report["notes"])
    assert "env: macos (detected)" in out  # the perf summary line


def test_bootstrap_inconclusive_says_base(monkeypatch, tmp_path):
    hp._meminfo(monkeypatch, tmp_path)
    runner = hp._ArgvRunner()
    result, buf = hp._bootstrap(monkeypatch, tmp_path, runner, topo=hp.boot.TOPO + PROFILE)
    assert result.exit_code == 0, result.output
    assert "MQLAB_ENV not set and platform not recognised — using base topology" in (buf.getvalue())
    assert not any(hp.hugepages.PLAYBOOK in c.argv for c in runner.recorded)
    report = hp._report(tmp_path)
    assert (report["env"], report["env_source"]) == (None, "inconclusive")
    assert "env: base (inconclusive)" in buf.getvalue()


def test_teardown_on_detected_macos_releases_the_pages(monkeypatch, tmp_path):
    """The same detection in teardown -> the lever is seen -> the pages are released
    (the #1241 gap: an unset MQLAB_ENV used to skip the release)."""
    _platform(monkeypatch, tmp_path, *APPLE)
    result, runner, buf, _ = hp._teardown(
        monkeypatch, tmp_path, other_up=False, topo=tear.TOPO + PROFILE
    )
    assert result.exit_code == 0, result.output
    assert hp._released(runner)
    assert buf.getvalue().count("environment: macos (detected:") == 1


def test_commons_up_prints_the_env_once(monkeypatch, tmp_path):
    from tests import test_cli_commons as com

    monkeypatch.setattr(t, "run_detect_virt", lambda: (None, "stub"))
    monkeypatch.setenv(t.ENV_VAR, "cloud")
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lab" / "topology.yaml").write_text(com.TOPO + PROFILE)
    monkeypatch.setattr(hp.cli, "_ensure_prereqs_for_commons", lambda **k: None)
    deps, buf = hp._perf_deps(hp._ArgvRunner())
    monkeypatch.setattr(hp.cli, "build_deps", lambda v, ts: deps)
    result = hp.CliRunner().invoke(hp.cli.app, ["commons", "up"])
    assert result.exit_code == 0, result.output
    assert buf.getvalue().count("environment: cloud (explicit MQLAB_ENV=cloud)") == 1
