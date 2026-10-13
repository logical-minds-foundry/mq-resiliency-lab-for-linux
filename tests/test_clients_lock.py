"""The clients component's lock pins the MQI binding exactly (epic .github#300 spec §4.3).

x86_64 guests must install ibmmq's abi3 wheel and aarch64 guests compile its sdist,
so the lock must carry BOTH artifacts. No OpenTelemetry package may enter the lock:
ibmmq switches on OTel propagation automatically when OTel imports succeed, which is
a behaviour change this epic defers (spec §1.2, §3).
"""

from __future__ import annotations

import tomllib

from mqlab.paths import repo_root

LOCK = repo_root() / "components" / "mq-resiliency-clients" / "uv.lock"


def _packages() -> dict[str, dict]:
    return {p["name"]: p for p in tomllib.loads(LOCK.read_text())["package"]}


def test_clients_lock_pins_ibmmq_wheel_and_sdist_without_otel():
    pkgs = _packages()
    # The retired binding's absence from this lock is enforced by the repo-wide guard,
    # which scans every tracked file, uv.lock included (spec §4.5).
    ibmmq = pkgs["ibmmq"]
    assert ibmmq["version"] == "2.1.1"
    assert ibmmq["sdist"]["url"].endswith("/ibmmq-2.1.1.tar.gz")
    wheels = [w["url"].rsplit("/", 1)[1] for w in ibmmq.get("wheels", [])]
    assert "ibmmq-2.1.1-cp39-abi3-manylinux_2_28_x86_64.whl" in wheels
    assert not [name for name in pkgs if name.startswith("opentelemetry")]
