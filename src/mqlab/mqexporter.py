"""Build & cache the mq_prometheus exporter binary in the Go container (#1065).

Fallout of the epic-198 cold-boot work: the `obs` and `mq-ubuntu` fat-box bakes
compiled `mq_prometheus` *inside the build guest* — installing `golang-go` and
running a cgo `go build`. When upstream `mq-metric-samples` moved its `go.mod` to
a newer Go, the build auto-downloaded the entire Go toolchain into the guest and
overflowed its disk, failing every bake.

Instead we build the binary **once, on the host, in `ghcr.io/vergil-project/dev-go`**
(which already ships the required Go — nothing auto-downloads), against the MQ SDK
extracted from the already-cached MQ tarball, and cache the artifact. The
`mq-exporter` role then just copies it in. This module is the host-side
orchestration, modelled on the MQ-tarball `ensure` in `artifact.py`: a cache-hit is
a no-op; a miss runs the container build. The container invocation is injected as a
seam so it is never exercised under unit test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING

from mqlab import paths

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

# The mq-metric-samples git ref the binary is built from. MUST stay in sync with the
# `mq_exporter_ref` role default (which the version manifest reports) — asserted in
# tests/test_mqexporter.py so the two declarations never drift.
MQ_EXPORTER_REF = "v5.7.1"
DEV_GO_IMAGE = "ghcr.io/vergil-project/dev-go:1.25"
BINARY_NAME = "mq_prometheus-x64"
# The fat boxes whose bake installs mq_prometheus: obs (the observability probe) and
# the mq-ubuntu commons (mon-probe). Only these need the prebuilt binary present.
EXPORTER_BOXES = frozenset({"obs-ubuntu2404", "mq-ubuntu2404"})
_BUILD_SCRIPT = "lab/scripts/build-mq-exporter-binary.sh"


def needs_exporter_binary(box_names: Iterable[str]) -> bool:
    """True iff any box being built bakes in the mq_prometheus exporter."""
    return bool(EXPORTER_BOXES.intersection(box_names))


def exporter_binary_path(cache_root: Path) -> Path:
    """Durable-cache location of the prebuilt exporter binary."""
    return cache_root / "mq-exporter" / BINARY_NAME


def _runtime() -> str:  # pragma: no cover
    """The container runtime to build with: an explicit override, else nerdctl/docker."""
    override = os.environ.get("VRG_CONTAINER_RUNTIME")
    if override:
        return override
    for candidate in ("nerdctl", "docker"):
        if shutil.which(candidate):
            return candidate
    msg = "no container runtime (nerdctl/docker) found to build the mq_prometheus binary"
    raise RuntimeError(msg)


def _container_build(cache_root: Path, out_dir: Path) -> None:  # pragma: no cover
    """Run the dev-go container to build the binary into out_dir. amd64 always — the
    lab's guests are x86, so the artifact must be linux/amd64 regardless of host."""
    out_dir.mkdir(parents=True, exist_ok=True)
    script = paths.repo_root() / _BUILD_SCRIPT
    subprocess.run(  # noqa: S603 - fixed argv; trusted repo/cache paths
        [
            _runtime(),
            "run",
            "--rm",
            "--platform=linux/amd64",
            "-v",
            f"{cache_root}:/cache",
            "-v",
            f"{out_dir}:/out",
            "-v",
            f"{script}:/build.sh:ro",
            "-e",
            f"MQ_EXPORTER_REF={MQ_EXPORTER_REF}",
            DEV_GO_IMAGE,
            "bash",
            "/build.sh",
        ],
        check=True,
    )


def ensure_mq_exporter_binary(
    cache_root: Path,
    *,
    exists: Callable[[Path], bool] = os.path.isfile,
    build: Callable[[Path, Path], None] = _container_build,
) -> Path:
    """Ensure the prebuilt mq_prometheus binary is in the durable cache; return its path.

    Cache-hit -> return untouched. Miss -> build it once in the Go container, then
    return it. `exists`/`build` are injected in tests so no real container runs.
    """
    binary = exporter_binary_path(cache_root)
    if exists(binary):
        return binary
    build(cache_root, binary.parent)
    return binary
