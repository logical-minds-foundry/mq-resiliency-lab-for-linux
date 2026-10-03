"""The OS version catalog + resolver (epic .github#280, T1)."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from mqlab import instances, topology, versions
from mqlab.hostfacts import AARCH64, X86_64, HostFacts
from mqlab.instances import InstanceRecord
from mqlab.paths import versions_catalog_path
from mqlab.versions import (
    INFRA_ROLES,
    BuildFile,
    OsRef,
    VersionError,
    load_build_file,
    load_catalog,
    node_boxes,
    stack_roles,
)

if TYPE_CHECKING:
    from pathlib import Path

X86 = HostFacts(arch=X86_64, kvm=True, distro_family="apt", in_vergil=True)
ARM = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)

_DELETE = object()


def _committed() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(versions_catalog_path().read_text())
    return data


def _write(tmp_path: Path, data: Any) -> Path:
    path = tmp_path / "versions.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def _mutated(keys: tuple[Any, ...], value: Any) -> dict[str, Any]:
    data = copy.deepcopy(_committed())
    node = data
    for key in keys[:-1]:
        node = node[key]
    if value is _DELETE:
        del node[keys[-1]]
    else:
        node[keys[-1]] = value
    return data


def _with_ubuntu26(*, unsupported: bool, default: str = "ubuntu:24") -> dict[str, Any]:
    """The committed catalog plus an ubuntu:26 entry offered to nativeha-ubuntu."""
    data = copy.deepcopy(_committed())
    entry: dict[str, Any] = {"base_box": "cloud-image/ubuntu-26.04"}
    if unsupported:
        entry["ibm_support"] = {"status": "unsupported", "source": "https://example.invalid"}
    data["os"]["ubuntu"][26] = entry
    data["stacks"]["nativeha-ubuntu"] = {
        "supported": ["ubuntu:24", "ubuntu:26"],
        "default": default,
    }
    return data


# --- OsRef ----------------------------------------------------------------------------


def test_osref_parse_and_token():
    ref = OsRef.parse("rhel:9")
    assert (ref.family, ref.major, ref.token, str(ref)) == ("rhel", 9, "rhel9", "rhel:9")


@pytest.mark.parametrize("bad", ["rhel", "rhel:", "rhel:nine", "fedora:40", ":9", "rhel:9:1"])
def test_osref_parse_rejects(bad):
    with pytest.raises(VersionError, match="expected <family>:<major>"):
        OsRef.parse(bad)


def test_osref_orders_by_family_then_major():
    assert sorted([OsRef("ubuntu", 26), OsRef("rhel", 10), OsRef("ubuntu", 24)]) == [
        OsRef("rhel", 10),
        OsRef("ubuntu", 24),
        OsRef("ubuntu", 26),
    ]


# --- The committed catalog ------------------------------------------------------------


def test_committed_catalog_is_at_todays_versions():
    cat = load_catalog()
    assert cat.infra == OsRef("ubuntu", 24)
    assert set(cat.oses) == {OsRef("ubuntu", 24), OsRef("rhel", 9)}
    assert {s: spec["default"] for s, spec in cat.stacks.items()} == {
        "nativeha-ubuntu": OsRef("ubuntu", 24),
        "pcmk-ubuntu": OsRef("ubuntu", 24),
        "nativeha-rhel-crr": OsRef("rhel", 9),
        "rdqm-rhel": OsRef("rhel", 9),
    }
    rhel9 = cat.oses[OsRef("rhel", 9)]
    assert (rhel9.base_box, rhel9.point, rhel9.iso, rhel9.arch_pin) == (
        "rhel/9-x86_64",
        "9.6",
        "rhel-9.6-x86_64-dvd.iso",
        "x86_64",
    )
    ubuntu24 = cat.oses[OsRef("ubuntu", 24)]
    assert ubuntu24.base_box == "cloud-image/ubuntu-24.04"
    assert ubuntu24.arch_pin is None
    assert ubuntu24.requires == ()
    assert ubuntu24.ibm_unsupported_source is None


def test_bake_stems_name_real_playbooks():
    cat = load_catalog()
    ansible = versions_catalog_path().parent.parent / "ansible"
    for spec in cat.roles.values():
        for stem in spec["bake"].values():
            assert (ansible / f"bake-{stem}.yml").is_file(), stem


# --- Catalog.box ----------------------------------------------------------------------


def test_box_name_is_role_plus_token():
    cat = load_catalog()
    nha = cat.box("mq-nativeha", OsRef("ubuntu", 24))
    assert (nha.name, nha.role, nha.bake_stem, nha.mq_bearing) == (
        "mq-nativeha-ubuntu24",
        "mq-nativeha",
        "nativeha-ubuntu",
        True,
    )
    assert nha.os is cat.oses[OsRef("ubuntu", 24)]
    assert cat.box("mq-rdqm", OsRef("rhel", 9)).bake_stem == "mq-rdqm"


def test_pcmk_is_not_mq_bearing():
    assert load_catalog().box("pcmk", OsRef("ubuntu", 24)).mq_bearing is False


def test_box_unknown_family_for_role():
    with pytest.raises(VersionError, match="role 'mq-rdqm' has no bake for ubuntu"):
        load_catalog().box("mq-rdqm", OsRef("ubuntu", 24))


def test_box_unknown_role():
    with pytest.raises(VersionError, match="unknown box role 'nope'.*add it under roles:"):
        load_catalog().box("nope", OsRef("ubuntu", 24))


def test_box_unknown_os():
    with pytest.raises(VersionError, match=r"OS rhel:10 is not in the catalog \(known: rhel:9"):
        load_catalog().box("mq-rdqm", OsRef("rhel", 10))


# --- Catalog.stack_os -----------------------------------------------------------------


def test_stack_default_when_no_build_file():
    assert load_catalog().stack_os("rdqm-rhel", None, X86) == OsRef("rhel", 9)


def test_stack_default_when_build_file_omits_os():
    assert load_catalog().stack_os("pcmk-ubuntu", BuildFile(os=None), ARM) == OsRef("ubuntu", 24)


def test_stack_takes_build_file_os(tmp_path):
    cat = load_catalog(_write(tmp_path, _with_ubuntu26(unsupported=False)))
    build = BuildFile(os=OsRef("ubuntu", 26))
    assert cat.stack_os("nativeha-ubuntu", build, X86) == OsRef("ubuntu", 26)


def test_stack_rejects_unsupported_version():
    cat = load_catalog()
    with pytest.raises(VersionError, match=r"rdqm-rhel supports \[rhel:9\]; got rhel:10 — edit"):
        cat.stack_os("rdqm-rhel", BuildFile(os=OsRef("rhel", 10)), X86)


def test_stack_rejects_family_mismatch():
    with pytest.raises(VersionError, match="nativeha-ubuntu is an ubuntu stack; got rhel:9"):
        load_catalog().stack_os("nativeha-ubuntu", BuildFile(os=OsRef("rhel", 9)), X86)


def test_stack_rejects_family_mismatch_rhel_article():
    with pytest.raises(VersionError, match="rdqm-rhel is a rhel stack; got ubuntu:24"):
        load_catalog().stack_os("rdqm-rhel", BuildFile(os=OsRef("ubuntu", 24)), X86)


def test_rhel_refused_on_aarch64():
    with pytest.raises(VersionError, match="RHEL needs an x86_64 host; this host is aarch64"):
        load_catalog().stack_os("rdqm-rhel", None, ARM)


def test_stack_unknown():
    with pytest.raises(VersionError, match="unknown stack 'nope'"):
        load_catalog().stack_os("nope", None, X86)


# --- Support gate ---------------------------------------------------------------------


def test_default_must_not_be_ibm_unsupported(tmp_path):
    data = copy.deepcopy(_committed())
    data["os"]["ubuntu"][24]["ibm_support"] = {
        "status": "unsupported",
        "source": "https://example.invalid",
    }
    with pytest.raises(
        VersionError, match="default ubuntu:24 for nativeha-ubuntu is IBM-unsupported"
    ):
        load_catalog(_write(tmp_path, data))


def test_unsupported_default_via_new_entry_refused(tmp_path):
    with pytest.raises(VersionError, match="default ubuntu:26 for nativeha-ubuntu"):
        load_catalog(_write(tmp_path, _with_ubuntu26(unsupported=True, default="ubuntu:26")))


def test_unsupported_entry_is_selectable_with_warning(tmp_path):
    cat = load_catalog(_write(tmp_path, _with_ubuntu26(unsupported=True)))
    ref = cat.stack_os("nativeha-ubuntu", BuildFile(os=OsRef("ubuntu", 26)), X86)
    # Exact match (not a URL substring check, which CodeQL flags as
    # py/incomplete-url-substring-sanitization even in a test).
    assert cat.support_warning(ref) == (
        "WARNING: IBM does not support MQ on ubuntu:26 (https://example.invalid); "
        "it is selectable for lab use only"
    )
    assert cat.support_warning(OsRef("ubuntu", 24)) is None


def test_supported_status_with_source_is_accepted(tmp_path):
    data = copy.deepcopy(_committed())
    data["os"]["rhel"][9]["ibm_support"] = {"status": "supported", "source": "https://x"}
    cat = load_catalog(_write(tmp_path, data))
    assert cat.oses[OsRef("rhel", 9)].ibm_unsupported_source is None


# --- Catalog.all_boxes ----------------------------------------------------------------

_STACK_ROLES = {
    "nativeha-ubuntu": {"mq-nativeha"},
    "pcmk-ubuntu": {"pcmk", "mq-client"},
    "nativeha-rhel-crr": {"mq-nativeha"},
    "rdqm-rhel": {"mq-rdqm"},
}


def test_all_boxes_on_x86():
    names = [b.name for b in load_catalog().all_boxes(X86, _STACK_ROLES)]
    assert names == [
        "infra-ubuntu24",
        "obs-ubuntu24",
        "mq-client-ubuntu24",
        "san-ubuntu24",
        "mq-nativeha-ubuntu24",
        "pcmk-ubuntu24",
        "mq-nativeha-rhel9",
        "mq-rdqm-rhel9",
    ]


def test_all_boxes_skips_rhel_on_aarch64():
    names = {b.name for b in load_catalog().all_boxes(ARM, _STACK_ROLES)}
    assert names == {
        "infra-ubuntu24",
        "obs-ubuntu24",
        "mq-client-ubuntu24",
        "san-ubuntu24",
        "mq-nativeha-ubuntu24",
        "pcmk-ubuntu24",
    }


def test_all_boxes_covers_every_supported_version(tmp_path):
    cat = load_catalog(_write(tmp_path, _with_ubuntu26(unsupported=False)))
    names = {b.name for b in cat.all_boxes(X86, {"nativeha-ubuntu": {"mq-nativeha"}})}
    assert {"mq-nativeha-ubuntu24", "mq-nativeha-ubuntu26"} <= names


def test_all_boxes_unknown_stack():
    with pytest.raises(VersionError, match="unknown stack 'nope'"):
        load_catalog().all_boxes(X86, {"nope": {"infra"}})


# --- load_catalog: eager validation ---------------------------------------------------


def test_catalog_defaults_to_committed_path():
    assert load_catalog() == load_catalog(versions_catalog_path())


def test_catalog_missing_file(tmp_path):
    with pytest.raises(VersionError, match="OS version catalog .* not found — fix lab/versions"):
        load_catalog(tmp_path / "absent.yaml")


def test_catalog_invalid_yaml(tmp_path):
    path = tmp_path / "versions.yaml"
    path.write_text("os: [unclosed\n")
    with pytest.raises(VersionError, match="is not valid YAML"):
        load_catalog(path)


def test_catalog_not_a_mapping(tmp_path):
    with pytest.raises(VersionError, match="the catalog must be a mapping"):
        load_catalog(_write(tmp_path, ["os"]))


@pytest.mark.parametrize(
    ("keys", "value", "match"),
    [
        (("extra",), 1, r"unknown key 'extra' \(allowed: os, roles, infra, stacks\)"),
        (("stacks",), _DELETE, "missing required key stacks"),
        # os:
        (("os",), [], "os must be a mapping"),
        (("os",), {}, "os: declares no OS versions"),
        (("os", "fedora"), {40: {"base_box": "x"}}, "os: unknown family 'fedora'"),
        (("os", "ubuntu"), [], "os.ubuntu must be a mapping"),
        (("os", "ubuntu", "26"), {"base_box": "x"}, "major '26' must be an integer"),
        (("os", "ubuntu", True), {"base_box": "x"}, "major True must be an integer"),
        (("os", "ubuntu", 24), "x", "os.ubuntu.24 must be a mapping"),
        (("os", "ubuntu", 24, "colour"), "x", "os.ubuntu.24: unknown key 'colour'"),
        (("os", "ubuntu", 24, "base_box"), _DELETE, "missing required 'base_box'"),
        (("os", "ubuntu", 24, "base_box"), 24, "'base_box' must be a string"),
        (("os", "rhel", 9, "point"), 9.6, r"'point' must be a string \(quote it\)"),
        (("os", "rhel", 9, "point"), _DELETE, "os.rhel.9: missing required 'point'"),
        (("os", "rhel", 9, "iso"), _DELETE, "os.rhel.9: missing required 'iso'"),
        (("os", "rhel", 9, "arch"), "s390x", "arch must be one of aarch64, x86_64"),
        (("os", "ubuntu", 24, "requires"), "x86-64-v3", "requires must be a list of strings"),
        (("os", "ubuntu", 24, "requires"), [3], "requires must be a list of strings"),
        (("os", "ubuntu", 24, "requires"), ["x86-64-v3"], "unknown host requirement x86-64-v3"),
        (("os", "ubuntu", 24, "ibm_support"), "no", "ibm_support must be a mapping"),
        (("os", "ubuntu", 24, "ibm_support"), {"status": "unsupported", "url": "u"}, "key 'url'"),
        (("os", "ubuntu", 24, "ibm_support"), {"status": "maybe"}, "status must be 'supported'"),
        (
            ("os", "ubuntu", 24, "ibm_support"),
            {"status": "unsupported"},
            "ibm_support: missing required 'source'",
        ),
        (
            ("os", "ubuntu", 24, "ibm_support"),
            {"status": "supported", "source": 1},
            "'source' must be a string",
        ),
        # roles:
        (("roles",), [], "roles must be a mapping"),
        (("roles", "obs"), "obs", "roles.obs must be a mapping"),
        (("roles", "obs", "colour"), 1, "roles.obs: unknown key 'colour'"),
        (("roles", "obs", "bake"), _DELETE, "roles.obs: bake must map at least one family"),
        (("roles", "obs", "bake"), {}, "roles.obs: bake must map at least one family"),
        (("roles", "obs", "bake"), {"fedora": "obs"}, "roles.obs.bake: 'fedora'"),
        (("roles", "obs", "bake"), {"ubuntu": 1}, "roles.obs.bake: 'ubuntu': 1"),
        (("roles", "obs", "mq_bearing"), "yes", "mq_bearing must be true or false"),
        (("roles", "obs"), _DELETE, "unknown box role 'obs'"),
        (("roles", "obs", "bake"), {"rhel": "obs"}, "role 'obs' has no bake for ubuntu"),
        # infra:
        (("infra",), "ubuntu:26", r"infra: ubuntu:26 is not declared under os:"),
        (("infra",), 24, "infra: expected <family>:<major>"),
        # stacks:
        (("stacks",), [], "stacks must be a mapping"),
        (("stacks", "rdqm-rhel"), "x", "stacks.rdqm-rhel must be a mapping"),
        (("stacks", "rdqm-rhel", "colour"), 1, "stacks.rdqm-rhel: unknown key 'colour'"),
        (("stacks", "rdqm-rhel", "supported"), [], "supported must be a non-empty list"),
        (("stacks", "rdqm-rhel", "supported"), "rhel:9", "supported must be a non-empty list"),
        (("stacks", "rdqm-rhel", "supported"), ["rhel:x"], "bad OS reference 'rhel:x'"),
        (("stacks", "rdqm-rhel", "supported"), ["rhel:10"], "rhel:10 is not declared under os:"),
        (
            ("stacks", "rdqm-rhel", "supported"),
            ["rhel:9", "ubuntu:24"],
            "supported mixes OS families",
        ),
        (("stacks", "rdqm-rhel", "default"), _DELETE, r"default: expected <family>:<major>"),
        (
            ("stacks", "nativeha-ubuntu", "supported"),
            ["ubuntu:24"],
            None,
        ),
    ],
)
def test_catalog_validation(tmp_path, keys, value, match):
    path = _write(tmp_path, _mutated(keys, value))
    if match is None:
        load_catalog(path)  # control row: a valid mutation loads
        return
    with pytest.raises(VersionError, match=match) as exc:
        load_catalog(path)
    assert "lab/versions.yaml" in str(exc.value)  # every catalog error names the fix


def test_default_not_in_supported(tmp_path):
    data = _with_ubuntu26(unsupported=False)
    data["stacks"]["nativeha-ubuntu"] = {"supported": ["ubuntu:24"], "default": "ubuntu:26"}
    with pytest.raises(VersionError, match="default ubuntu:26 is not in its supported list"):
        load_catalog(_write(tmp_path, data))


# --- load_build_file ------------------------------------------------------------------


def _build(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "b.yaml"
    path.write_text(text)
    return path


def test_build_file_os(tmp_path):
    assert load_build_file(_build(tmp_path, "os: rhel:9\n")) == BuildFile(os=OsRef("rhel", 9))


def test_build_file_empty_means_defaults(tmp_path):
    assert load_build_file(_build(tmp_path, "")) == BuildFile(os=None)
    assert load_build_file(_build(tmp_path, "{}\n")) == BuildFile(os=None)


def test_build_file_unknown_key(tmp_path):
    with pytest.raises(VersionError, match=r"unknown key 'mq' \(allowed: os\) — edit your"):
        load_build_file(_build(tmp_path, "os: rhel:9\nmq: 9\n"))


def test_build_file_not_a_mapping(tmp_path):
    with pytest.raises(VersionError, match="must be a mapping"):
        load_build_file(_build(tmp_path, "- os\n"))


def test_build_file_os_not_a_string(tmp_path):
    with pytest.raises(VersionError, match="os must be <family>:<major>.*got 9"):
        load_build_file(_build(tmp_path, "os: 9\n"))


def test_build_file_bad_os(tmp_path):
    with pytest.raises(VersionError, match="bad OS reference 'fedora:40'.*--config"):
        load_build_file(_build(tmp_path, "os: fedora:40\n"))


def test_build_file_missing(tmp_path):
    with pytest.raises(VersionError, match="build file .* not found — pass an existing file"):
        load_build_file(tmp_path / "absent.yaml")


def test_build_file_invalid_yaml(tmp_path):
    with pytest.raises(VersionError, match="build file .* is not valid YAML"):
        load_build_file(_build(tmp_path, "os: [unclosed\n"))


# --- Topology roles -> boxes: the version layer in front of the render (T2) ----------

_SHARED = ("obs", "svc-sim", "app-client", "mon-probe", "infra-client", "infra-svc")


@pytest.fixture
def no_records(monkeypatch):
    """No stack has an instance record: every stack resolves to its catalog default."""
    monkeypatch.setattr(instances, "read_record", lambda stack: None)


def test_osref_label():
    assert (OsRef("ubuntu", 24).label, OsRef("rhel", 9).label) == ("Ubuntu 24", "RHEL 9")


def test_node_boxes_defaults(no_records):
    nb = node_boxes(topology.load(), load_catalog())
    assert nb["nha-ubuntu-a1"].name == "mq-nativeha-ubuntu24"
    assert nb["nha-rhel-crr-a1"].name == "mq-nativeha-rhel9"
    assert nb["rdqm-a1"].name == "mq-rdqm-rhel9"
    assert nb["pcmk-a1"].name == "pcmk-ubuntu24"
    assert nb["infra-svc"].name == "infra-ubuntu24"
    assert nb["svc-sim"].name == "mq-client-ubuntu24"


def test_node_boxes_covers_every_topology_node(no_records):
    topo = topology.load()
    assert set(node_boxes(topo, load_catalog())) == set(topo["nodes"])


def test_san_nodes_resolve_to_baked_san_box(no_records):
    """The SAN targets boot the baked `san` box on the infra OS (#1278, spec §4.7.1):
    bake stem `san`, not MQ-bearing, whatever stack lists them."""
    cat = load_catalog()
    nb = node_boxes(topology.load(), cat)
    assert nb["san-a"].name == nb["san-b"].name == f"san-{cat.infra.token}"
    for san in ("san-a", "san-b"):
        assert (nb[san].role, nb[san].os) == ("san", cat.oses[cat.infra])
        assert (nb[san].bake_stem, nb[san].mq_bearing) == ("san", False)


def test_san_is_an_infra_role():
    assert "san" in INFRA_ROLES


def test_commons_render_uses_infra_without_records(no_records):  # Review Focus 5
    cat = load_catalog()
    nb = node_boxes(topology.load(), cat)
    assert {nb[n].os.ref for n in _SHARED} == {cat.infra}


def test_shared_nodes_resolve_without_any_record_lookup(monkeypatch):  # Review Focus 5
    """`commons up` with no stack: a commons-only topology never consults a record."""

    def refuse(stack):
        raise AssertionError(f"record lookup for {stack}")

    monkeypatch.setattr(instances, "read_record", refuse)
    topo = topology.load()
    commons_only = {"nodes": {n: topo["nodes"][n] for n in _SHARED}}
    nb = node_boxes(commons_only, load_catalog())
    assert {e.role for e in nb.values()} <= set(INFRA_ROLES)


def test_node_boxes_reads_each_stack_record(monkeypatch, tmp_path):
    """A stack's nodes follow its instance record, not the default; records of different
    stacks combine in one render (two stacks at different majors)."""
    data = _with_ubuntu26(unsupported=False)
    data["stacks"]["pcmk-ubuntu"] = {
        "supported": ["ubuntu:24", "ubuntu:26"],
        "default": "ubuntu:24",
    }
    cat = load_catalog(_write(tmp_path, data))
    records = {"pcmk-ubuntu": InstanceRecord("pcmk-ubuntu", OsRef("ubuntu", 26), None, "t")}
    monkeypatch.setattr(instances, "read_record", records.get)
    nb = node_boxes(topology.load(), cat)
    assert (nb["nha-ubuntu-a1"].name, nb["pcmk-a1"].name) == (
        "mq-nativeha-ubuntu24",
        "pcmk-ubuntu26",
    )
    assert nb["obs"].os.ref == cat.infra  # shared nodes stay on the infra OS


def test_node_boxes_renders_rhel_nodes_on_any_host(no_records):
    """No host gate in the render: every node is described on every host (a RHEL node on
    an aarch64 host renders under TCG); the host gate is Catalog.stack_os at bring-up."""
    nb = node_boxes(topology.load(), load_catalog())
    assert nb["rdqm-a1"].os.arch_pin == "x86_64"


def _topo(nodes: dict[str, Any], stacks: dict[str, Any] | None = None) -> dict[str, Any]:
    groups = {f"g_{name}": hosts for name, (_, hosts) in (stacks or {}).items()}
    return {
        "nodes": nodes,
        "groups": groups,
        "stacks": {
            name: {"os_family": family, "groups": [f"g_{name}"]}
            for name, (family, _) in (stacks or {}).items()
        },
    }


@pytest.mark.parametrize("spec", [None, {}, {"box": ""}, {"box": 7}, "mq-rdqm"])
def test_node_without_a_box_role_fails_loud(no_records, spec):
    with pytest.raises(VersionError, match=r"node n1: declares no box role — give it `box: "):
        node_boxes({"nodes": {"n1": spec}}, load_catalog())


def test_node_with_an_unknown_role_fails_loud(no_records):
    with pytest.raises(VersionError, match=r"node n1: box role 'mystery' is not in "):
        node_boxes({"nodes": {"n1": {"box": "mystery"}}}, load_catalog())


def test_stack_role_node_outside_every_stack_fails_loud(no_records):
    with pytest.raises(VersionError, match=r"node n1: box role 'pcmk' .* \(it is in: none\)"):
        node_boxes({"nodes": {"n1": {"box": "pcmk"}}}, load_catalog())


def test_stack_role_node_in_two_stacks_fails_loud(no_records):
    topo = _topo(
        {"n1": {"box": "mq-nativeha"}},
        {"nativeha-ubuntu": ("ubuntu", ["n1"]), "nativeha-rhel-crr": ("rhel", ["n1"])},
    )
    with pytest.raises(VersionError, match=r"\(it is in: nativeha-ubuntu, nativeha-rhel-crr\)"):
        node_boxes(topo, load_catalog())


def test_node_listed_twice_in_one_stack_is_owned_once(no_records):
    topo = _topo({"n1": {"box": "pcmk"}}, {"pcmk-ubuntu": ("ubuntu", ["n1", "n1"])})
    topo["stacks"]["pcmk-ubuntu"]["groups"].append("g_pcmk-ubuntu")
    assert node_boxes(topo, load_catalog())["n1"].name == "pcmk-ubuntu24"


def test_stack_os_family_must_match_the_catalog(no_records):
    topo = _topo({"n1": {"box": "mq-rdqm"}}, {"rdqm-rhel": ("ubuntu", ["n1"])})
    with pytest.raises(
        VersionError, match=r"stack rdqm-rhel: os_family 'ubuntu' disagrees with lab/versions"
    ):
        node_boxes(topo, load_catalog())


def test_topology_stack_missing_from_the_catalog_fails_loud(no_records):
    topo = _topo({"n1": {"box": "pcmk"}}, {"ghost": ("ubuntu", ["n1"])})
    with pytest.raises(VersionError, match="unknown stack 'ghost'"):
        node_boxes(topo, load_catalog())


def test_stack_roles_from_node_box_roles():
    roles = stack_roles(topology.load(), load_catalog())
    assert roles == {
        "pcmk-ubuntu": {"pcmk"},
        "rdqm-rhel": {"mq-rdqm"},
        "nativeha-rhel-crr": {"mq-nativeha"},
        "nativeha-ubuntu": {"mq-nativeha"},
    }


def test_catalog_without_the_san_role_fails_loud(tmp_path):
    """san is an infra role, so a catalog that drops it cannot bake the SAN targets."""
    data = _mutated(("roles", "san"), _DELETE)
    with pytest.raises(VersionError, match=r"unknown box role 'san'"):
        load_catalog(_write(tmp_path, data))


def test_stack_ref_is_the_default_without_a_record(no_records):
    assert versions.stack_ref("rdqm-rhel", load_catalog()) == OsRef("rhel", 9)
