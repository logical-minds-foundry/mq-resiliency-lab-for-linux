"""Assemble the epic #294 T2 spike bundle (throwaway): pinned runtimes + hash-pinned offline deps."""
import hashlib
import json
import pathlib
import sys
import urllib.request

B = pathlib.Path(sys.argv[1])
REL = "https://github.com/astral-sh/python-build-standalone/releases/download/20261003"
PIN = {
    "x86_64": "371b6c281bbb09b29279e9e3a2996bab4ae2ea03cca52bf869f8bd89286b0ae8",
    "aarch64": "abc0c8dd54a144909a5e4905737bc33f244cb17ce8afc4a7b0791ee1e006ae23",
}


def fetch(url: str, dest: pathlib.Path, sha: str) -> None:
    if not dest.exists():
        urllib.request.urlretrieve(url, dest)
    got = hashlib.sha256(dest.read_bytes()).hexdigest()
    if got != sha:
        dest.unlink()
        sys.exit(f"sha256 mismatch for {dest.name}: expected {sha}, got {got}")


(B / "runtime").mkdir(parents=True, exist_ok=True)
(B / "deps").mkdir(parents=True, exist_ok=True)
for arch, sha in PIN.items():
    name = f"cpython-3.14.8+20261003-{arch}-unknown-linux-gnu-install_only.tar.gz"
    fetch(f"{REL}/{name}", B / "runtime" / name, sha)
    print(f"runtime {arch}: {name} sha256 matches the pin")

groups: dict[str, list[str]] = {"build": [], "run": []}
for name, ver, kind, group in [
    ("setuptools", None, "bdist_wheel", "build"),
    ("wheel", None, "bdist_wheel", "build"),
    ("pymqi", "1.12.13", "sdist", "run"),
]:
    url = f"https://pypi.org/pypi/{name}/{ver + '/' if ver else ''}json"
    data = json.load(urllib.request.urlopen(url))
    version = data["info"]["version"]
    art = next(u for u in data["urls"] if u["packagetype"] == kind)
    fetch(art["url"], B / "deps" / art["filename"], art["digests"]["sha256"])
    groups[group].append(f"{name}=={version} --hash=sha256:{art['digests']['sha256']}")
    print(f"{name}=={version}: {art['filename']} (uploaded {art['upload_time']}) sha256 ok")

(B / "build-requirements.txt").write_text("\n".join(groups["build"]) + "\n")
(B / "requirements.txt").write_text("\n".join(groups["run"]) + "\n")
