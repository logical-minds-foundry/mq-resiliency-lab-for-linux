"""Assemble the epic #294 T2 spike bundle: pinned runtimes + a lock-derived offline set.

Usage: python3 bundle.py <bundle-dir>     (needs internet and `uv` on PATH)

Prototype of `mqlab component build`'s staging (spec §5.6): the dependency sets come
from a real resolver lock (`uv lock` on demo-pyproject.toml), exported with hashes,
and every artifact is fetched from the URL + hash recorded in uv.lock. A hand-picked
list is NOT used: the first spike run proved it misses transitive deps (wheel 0.48.0
needs packaging>=24.0), which `--no-index --require-hashes` then rightly refuses.
"""

import hashlib
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
B = pathlib.Path(sys.argv[1]).resolve()
REL = "https://github.com/astral-sh/python-build-standalone/releases/download/20261003"
PIN = {
    "x86_64": "371b6c281bbb09b29279e9e3a2996bab4ae2ea03cca52bf869f8bd89286b0ae8",
    "aarch64": "abc0c8dd54a144909a5e4905737bc33f244cb17ce8afc4a7b0791ee1e006ae23",
}
# A wheel is staged when a CPython 3.14 Linux guest (either arch) could install it.
PY_TAGS = re.compile(r"^(py3|py2\.py3|cp314|cp3\d+-abi3)$")


def fetch(url: str, dest: pathlib.Path, sha: str) -> None:
    if not dest.exists():
        urllib.request.urlretrieve(url, dest)
    got = hashlib.sha256(dest.read_bytes()).hexdigest()
    if got != sha:
        dest.unlink()
        sys.exit(f"sha256 mismatch for {dest.name}: expected {sha}, got {got}")


def wheel_ok(filename: str) -> bool:
    # name-ver(-build)?-pytag-abitag-plattag.whl
    parts = filename[:-4].split("-")
    py, abi, plat = parts[-3], parts[-2], parts[-1]
    pys = py.split(".")
    py_ok = any(PY_TAGS.match(p) for p in pys) or (abi == "abi3" and any(p.startswith("cp3") for p in pys))
    plat_ok = plat == "any" or (
        plat.startswith("manylinux") and (plat.endswith("x86_64") or plat.endswith("aarch64"))
    )
    return py_ok and plat_ok


def run(*cmd: str, cwd: pathlib.Path) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> None:
    (B / "runtime").mkdir(parents=True, exist_ok=True)
    deps = B / "deps"
    if deps.exists():
        shutil.rmtree(deps)
    deps.mkdir()

    for arch, sha in PIN.items():
        name = f"cpython-3.14.8+20261003-{arch}-unknown-linux-gnu-install_only.tar.gz"
        fetch(f"{REL}/{name}", B / "runtime" / name, sha)
        print(f"runtime {arch}: {name} sha256 matches the pin")

    proj = B / "demo"
    if proj.exists():
        shutil.rmtree(proj)
    proj.mkdir()
    shutil.copy(HERE / "demo-pyproject.toml", proj / "pyproject.toml")
    run("uv", "lock", cwd=proj)
    common = ["uv", "export", "--frozen", "--no-emit-project", "--format", "requirements-txt", "--no-header"]
    run(*common, "--no-dev", "--all-extras", "-o", str(B / "requirements.txt"), cwd=proj)
    run(*common, "--only-group", "sdist-build", "-o", str(B / "build-requirements.txt"), cwd=proj)

    wanted: set[str] = set()
    for req in ("requirements.txt", "build-requirements.txt"):
        for line in (B / req).read_text().splitlines():
            m = re.match(r"^([A-Za-z0-9_.-]+)==", line)
            if m:
                wanted.add(m.group(1).lower().replace("_", "-"))

    lock = tomllib.loads((proj / "uv.lock").read_text())
    for pkg in lock["package"]:
        if pkg["name"] not in wanted:
            continue
        arts = []
        if "sdist" in pkg:
            arts.append(pkg["sdist"])
        arts += [w for w in pkg.get("wheels", []) if wheel_ok(w["url"].rsplit("/", 1)[1])]
        if not arts:
            sys.exit(f"{pkg['name']}: no stageable artifact in uv.lock")
        for art in arts:
            fname = art["url"].rsplit("/", 1)[1]
            fetch(art["url"], deps / fname, art["hash"].split(":", 1)[1])
            print(f"{pkg['name']}=={pkg['version']}: {fname} sha256 ok")
        wanted.discard(pkg["name"])
    if wanted:
        sys.exit(f"exported but not found in uv.lock: {sorted(wanted)}")


if __name__ == "__main__":
    main()
