"""Every ibmmq name the clients use exists in the REAL ibmmq (#1478).

The client tests drive a fake ibmmq, and the fakes were written from the same belief as
the code, so ``ibmmq.CMQC.MQXPT_TCP`` (ibmmq defines it only in CMQXC) passed them all
and broke every client connect on the lab. These tests check the package's own source
(mqrc.ibmmq_refs) against the ibmmq that ``uv.lock`` pins, not against a fake:

- constants: read with ``ast`` straight from the pinned sdist's constant modules
  (pure assignments; nothing is executed and nothing is stubbed), for BOTH guest
  architectures' CMQC variants;
- names and class members (classes, structure fields, methods): ibmmq's real Python
  layer, loaded in a child interpreter with only its compiled extension stubbed
  (tests/ibmmq_layer_probe.py).

The sdist is fetched from the URL in ``uv.lock`` and verified against its sha256 --
the very artifact aarch64 guests compile. Set ``MQRC_IBMMQ_SDIST`` to a local copy to
avoid the download (it is still hash-verified). A failed fetch FAILS these tests; it
never skips them. The guest selfcheck repeats the full check against the compiled
binding on every install.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tomllib
import types
import urllib.request
from pathlib import Path

import pytest

from mqrc import ibmmq_refs

COMPONENT = Path(__file__).resolve().parent.parent
LOCK = COMPONENT / "uv.lock"
PROBE = Path(__file__).resolve().parent / "ibmmq_layer_probe.py"
# The CMQC variants ibmmq's CMQC.py selects on our guests (Linux x86_64 and aarch64).
CMQC_VARIANTS = ("_CMQC_linux_x64", "_CMQC_linux_arm64")
OTHER_CONSTANT_MODULES = ("CMQXC", "CMQCFC", "CMQSTRC")


def _pinned() -> dict:
    packages = {p["name"]: p for p in tomllib.loads(LOCK.read_text())["package"]}
    return packages["ibmmq"]


def _verified(path: Path, expected: str) -> bool:
    return hashlib.sha256(path.read_bytes()).hexdigest() == expected


@pytest.fixture(scope="session")
def ibmmq_sdist(request, tmp_path_factory) -> tuple[str, Path]:
    """(version, path) of the uv.lock-pinned ibmmq sdist, sha256-verified."""
    pkg = _pinned()
    url = pkg["sdist"]["url"]
    algo, _, expected = pkg["sdist"]["hash"].partition(":")
    assert algo == "sha256", f"uv.lock hashes the ibmmq sdist with {algo}, expected sha256"
    override = os.environ.get("MQRC_IBMMQ_SDIST")
    if override:
        path = Path(override)
        if not (path.is_file() and _verified(path, expected)):
            pytest.fail(f"MQRC_IBMMQ_SDIST={override} is not the uv.lock ibmmq sdist ({expected})")
        return pkg["version"], path
    cache = request.config.cache
    base = cache.mkdir("ibmmq-sdist") if cache is not None else tmp_path_factory.mktemp("sdist")
    path = base / url.rsplit("/", 1)[1]
    if not (path.is_file() and _verified(path, expected)):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 -- pinned https URL
                path.write_bytes(resp.read())
        except OSError as exc:
            pytest.fail(
                f"cannot fetch the pinned ibmmq sdist {url}: {exc!r} -- these tests check "
                "the clients against the REAL ibmmq and do not skip; set MQRC_IBMMQ_SDIST "
                "to a local copy"
            )
        if not _verified(path, expected):
            path.unlink()
            pytest.fail(f"{url}: sha256 does not match uv.lock {expected}")
    return pkg["version"], path


@pytest.fixture(scope="session")
def ibmmq_code(ibmmq_sdist, tmp_path_factory) -> Path:
    """The sdist's ``ibmmq/`` package sources, extracted (``.py`` files only)."""
    version, sdist = ibmmq_sdist
    prefix = f"ibmmq-{version}/code/"
    dest = tmp_path_factory.mktemp("ibmmq-code")
    with tarfile.open(sdist) as tar:
        members = [
            m
            for m in tar.getmembers()
            if m.isfile() and m.name.startswith(prefix + "ibmmq/") and m.name.endswith(".py")
        ]
        for m in members:
            m.name = m.name.removeprefix(prefix)
        tar.extractall(dest, members=members, filter="data")
    assert (dest / "ibmmq" / "CMQXC.py").is_file(), f"{sdist}: no {prefix}ibmmq/CMQXC.py"
    return dest


def _assigned_names(path: Path) -> set[str]:
    """Top-level names a constant module assigns, read with ast (never executed)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)):
            raise AssertionError(f"{path.name}: unexpected top-level {type(node).__name__}")
    return names


def _constants_binding(code: Path, cmqc_variant: str) -> types.SimpleNamespace:
    """An ibmmq-shaped namespace holding exactly the pinned constant modules' names."""
    pkg = code / "ibmmq"
    modules = {"CMQC": _assigned_names(pkg / f"{cmqc_variant}.py")}
    for name in OTHER_CONSTANT_MODULES:
        modules[name] = _assigned_names(pkg / f"{name}.py")
    return types.SimpleNamespace(
        **{mod: types.SimpleNamespace(**dict.fromkeys(names)) for mod, names in modules.items()}
    )


def _constant_refs() -> list[ibmmq_refs.Ref]:
    refs, _members = ibmmq_refs.scan(ibmmq_refs.source_files())
    return [r for r in refs if r.path[0].startswith("CMQ")]


# --- against the real pinned ibmmq --------------------------------------------------------


def test_the_scan_finds_the_clients_constant_references():
    # Guards against a vacuous pass: the clients use ~30 distinct constants.
    assert len({r.path for r in _constant_refs()}) >= 20


@pytest.mark.parametrize("variant", CMQC_VARIANTS)
def test_every_constant_reference_exists_in_the_pinned_ibmmq(ibmmq_code, variant):
    binding = _constants_binding(ibmmq_code, variant)
    assert ibmmq_refs.problems(binding, _constant_refs(), []) == []


def test_the_pinned_constants_reject_the_1478_reference(ibmmq_code):
    binding = _constants_binding(ibmmq_code, CMQC_VARIANTS[0])
    before = ibmmq_refs.Ref(("CMQC", "MQXPT_TCP"), "app_requester.py:203")
    after = ibmmq_refs.Ref(("CMQXC", "MQXPT_TCP"), "app_requester.py:203")
    assert ibmmq_refs.problems(binding, [before], []) == [
        "app_requester.py:203: ibmmq.CMQC.MQXPT_TCP does not exist"
    ]
    assert ibmmq_refs.problems(binding, [after], []) == []


def test_every_name_and_member_exists_on_the_pinned_ibmmq_python_layer(ibmmq_code, ibmmq_sdist):
    version, _sdist = ibmmq_sdist
    proc = subprocess.run(
        [sys.executable, "-I", str(PROBE), str(ibmmq_code), str(COMPONENT / "src"), version],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["loaded_from"].startswith(str(ibmmq_code)), report
    assert report["refs"] >= 20 and report["members"] >= 20, report
    assert report["problems"] == []


# --- the scanner ---------------------------------------------------------------------------


def _scan_source(tmp_path: Path, text: str):
    (tmp_path / "mod.py").write_text(text)
    return ibmmq_refs.scan(ibmmq_refs.source_files(tmp_path), tmp_path)


def test_scan_finds_chains_keywords_and_members_of_bound_locals(tmp_path):
    refs, members = _scan_source(
        tmp_path,
        "def f(x):\n"
        "    import ibmmq\n"
        "    cd = ibmmq.CD(ChannelName='C', TransportType=ibmmq.CMQXC.MQXPT_TCP)\n"
        "    cd.SSLCipherSpec = 'X'\n"
        "    self.cd = ibmmq.CD()\n"
        "    qmgr = ibmmq.QueueManager(None)\n"
        "    qmgr.connect_with_options('QM', cd=cd)\n"
        "    other.Thing = 1\n"
        "    x.ibmmq.CMQC.NOT_ROOTED = 1\n"
        "    return ibmmq.CMQC.MQGMO_WAIT, ibmmq.MD(**x)\n",
    )
    assert {r.path for r in refs} == {
        ("CD",),
        ("CMQXC",),
        ("CMQXC", "MQXPT_TCP"),
        ("QueueManager",),
        ("CMQC",),
        ("CMQC", "MQGMO_WAIT"),
        ("MD",),
    }
    assert ibmmq_refs.Ref(("CMQXC", "MQXPT_TCP"), "mod.py:3") in refs
    assert {(m.cls, m.member) for m in members} == {
        ("CD", "ChannelName"),
        ("CD", "TransportType"),
        ("CD", "SSLCipherSpec"),
        ("QueueManager", "connect_with_options"),
    }


def test_scan_reads_the_installed_package_by_default():
    refs, members = ibmmq_refs.scan(ibmmq_refs.source_files())
    assert any(r.where.startswith("app_requester.py:") for r in refs)
    assert any(m.cls == "CD" and m.member == "TransportType" for m in members)


# --- problems() ----------------------------------------------------------------------------


class _MQOpts:
    pass


class _CD(_MQOpts):
    def __init__(self) -> None:
        self.ChannelName = b""  # an instance field, as MQOpts sets them


class _QueueManager:
    def __init__(self, *_a) -> None:
        raise AssertionError("problems() must never instantiate a non-structure class")

    def connect_with_options(self) -> None:
        pass


_FAKE = types.SimpleNamespace(
    MQOpts=_MQOpts,
    CD=_CD,
    QueueManager=_QueueManager,
    CMQXC=types.SimpleNamespace(MQXPT_TCP=2),
)


def test_problems_reports_a_missing_module_and_a_missing_constant():
    refs = [
        ibmmq_refs.Ref(("CMQZC", "MQZ_X"), "a.py:1"),
        ibmmq_refs.Ref(("CMQXC", "MQXPT_LU62"), "a.py:2"),
        ibmmq_refs.Ref(("CMQXC", "MQXPT_TCP"), "a.py:3"),
    ]
    assert ibmmq_refs.problems(_FAKE, refs, []) == [
        "a.py:1: ibmmq.CMQZC does not exist",
        "a.py:2: ibmmq.CMQXC.MQXPT_LU62 does not exist",
    ]


def test_problems_checks_structure_fields_on_an_instance():
    members = [
        ibmmq_refs.MemberRef("CD", "ChannelName", "a.py:1"),
        ibmmq_refs.MemberRef("CD", "ChanelName", "a.py:2"),
    ]
    assert ibmmq_refs.problems(_FAKE, [], members) == [
        "a.py:2: ibmmq.CD has no member ChanelName"
    ]


def test_problems_checks_other_classes_on_the_class_without_instantiating():
    members = [
        ibmmq_refs.MemberRef("QueueManager", "connect_with_options", "a.py:1"),
        ibmmq_refs.MemberRef("QueueManager", "connectWithOpts", "a.py:2"),
    ]
    assert ibmmq_refs.problems(_FAKE, [], members) == [
        "a.py:2: ibmmq.QueueManager has no member connectWithOpts"
    ]


def test_problems_leaves_a_missing_class_to_its_ref():
    refs = [ibmmq_refs.Ref(("OD",), "a.py:1")]
    members = [ibmmq_refs.MemberRef("OD", "ObjectName", "a.py:1")]
    assert ibmmq_refs.problems(_FAKE, refs, members) == ["a.py:1: ibmmq.OD does not exist"]


def test_problems_without_mqopts_checks_every_class_on_the_class():
    binding = types.SimpleNamespace(CD=_CD)  # no MQOpts: CD is never instantiated
    members = [ibmmq_refs.MemberRef("CD", "ChannelName", "a.py:1")]
    assert ibmmq_refs.problems(binding, [], members) == [
        "a.py:1: ibmmq.CD has no member ChannelName"
    ]
