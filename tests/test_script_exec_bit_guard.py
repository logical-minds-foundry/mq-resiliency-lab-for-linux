"""Script exec-bit guardrail (#1333).

Every git-tracked ``*.sh`` that starts with a ``#!`` shebang is an executable script
and must be tracked as mode ``100755``. Sourced helpers (e.g.
``lab/boxes/_box-register.sh``) carry no shebang and are exempt.

``lab/boxes/rhel/await-install.sh`` lost its exec bit in #1298. ``build-box.sh`` runs
it as ``./await-install.sh``, so every fresh RHEL base bake died with exit 126
(``Permission denied``). Its behavioural tests run it as ``bash <script>``, which
ignores the mode, and the cached base hid it on every host until D1 (#1332) rebuilt
it. This guard checks the git INDEX mode, the one CI sees, rather than a checkout's
filesystem bits.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from mqlab.paths import repo_root

if TYPE_CHECKING:
    from pathlib import Path


def non_executable_scripts(repo: Path) -> list[str]:
    """Tracked ``*.sh`` files with a ``#!`` shebang whose index mode is not 100755."""
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(repo), "ls-files", "-s", "-z", "--", "*.sh"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = f"git ls-files failed in {repo} (exit {result.returncode}): {result.stderr.strip()}"
        raise RuntimeError(msg)
    bad: list[str] = []
    for entry in filter(None, result.stdout.split("\0")):
        meta, rel = entry.split("\t", 1)
        mode = meta.split()[0]
        path = repo / rel
        if not path.is_file():  # tracked but deleted in the working tree: nothing to read
            continue
        with path.open("rb") as fh:
            shebang = fh.read(2) == b"#!"
        if shebang and mode != "100755":
            bad.append(f"{rel} ({mode})")
    return sorted(bad)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)  # noqa: S603, S607


def test_every_shebang_script_is_tracked_executable():
    bad = non_executable_scripts(repo_root())
    assert bad == [], (
        "shebang scripts tracked without the exec bit (#1333). Fix with "
        "`chmod +x <path> && git add <path>` (or `git update-index --chmod=+x <path>`):\n"
        + "\n".join(bad)
    )


def test_guard_flags_shebang_scripts_only(tmp_path: Path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "run.sh").write_text("#!/usr/bin/env bash\necho hi\n")
    (tmp_path / "ok.sh").write_text("#!/usr/bin/env bash\necho ok\n")
    (tmp_path / "_sourced.sh").write_text("# sourced helper\nFOO=1\n")
    _git(tmp_path, "add", "run.sh", "ok.sh", "_sourced.sh")
    _git(tmp_path, "update-index", "--chmod=-x", "run.sh", "_sourced.sh")
    _git(tmp_path, "update-index", "--chmod=+x", "ok.sh")

    assert non_executable_scripts(tmp_path) == ["run.sh (100644)"]
