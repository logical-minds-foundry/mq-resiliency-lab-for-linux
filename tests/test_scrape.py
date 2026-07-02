from __future__ import annotations

import json

import pytest

from mqlab.scrape import ScrapeError, render_scrape_targets

TOPO = {
    "nodes": {
        "obs": {"nics": {"net-mgmt": "10.50.0.2"}},
        "qm-main": {"nics": {"net-mgmt": "10.50.0.10"}},
        "rdqm-a1": {"nics": {"net-mgmt": "10.50.0.31"}},
    },
    "groups": {
        "obs": ["obs"],
        "qm": ["qm-main"],
        "rdqm_a": ["rdqm-a1"],
    },
}


def test_render_emits_file_sd_one_entry_per_node_on_9100():
    from mqlab.scrape import HYPERVISOR_MGMT_IP

    out = json.loads(render_scrape_targets(TOPO))
    assert out == [
        {"targets": ["10.50.0.2:9100"], "labels": {"host": "obs", "groups": "obs"}},
        {"targets": ["10.50.0.10:9100"], "labels": {"host": "qm-main", "groups": "qm"}},
        {"targets": ["10.50.0.31:9100"], "labels": {"host": "rdqm-a1", "groups": "rdqm_a"}},
        {
            "targets": [f"{HYPERVISOR_MGMT_IP}:9100"],
            "labels": {"host": "hypervisor", "groups": "hypervisor"},
        },
    ]


def test_includes_the_hypervisor_host_target():
    from mqlab.scrape import HYPERVISOR_MGMT_IP

    out = json.loads(render_scrape_targets({"nodes": {}, "groups": {}}))
    assert {
        "targets": [f"{HYPERVISOR_MGMT_IP}:9100"],
        "labels": {"host": "hypervisor", "groups": "hypervisor"},
    } in out


def test_node_in_multiple_groups_joins_labels_sorted():
    topo = {
        "nodes": {"n1": {"nics": {"net-mgmt": "10.50.0.9"}}},
        "groups": {"z_grp": ["n1"], "a_grp": ["n1"]},
    }
    out = json.loads(render_scrape_targets(topo))
    assert out[0]["labels"]["groups"] == "a_grp,z_grp"


def test_node_in_no_group_gets_empty_groups_label():
    topo = {"nodes": {"lonely": {"nics": {"net-mgmt": "10.50.0.8"}}}, "groups": {}}
    out = json.loads(render_scrape_targets(topo))
    assert out[0]["labels"] == {"host": "lonely", "groups": ""}


def test_missing_mgmt_ip_raises():
    topo = {"nodes": {"bad": {"nics": {}}}, "groups": {}}
    with pytest.raises(ScrapeError, match="no net-mgmt IP: bad"):
        render_scrape_targets(topo)


# --- MQ exporter / ibmmq scrape (#423) ---------------------------------------

# A minimal per-stack topology: a VIP arm (pcmk), a VIP-less Native HA arm (whose
# app conn is its site-A instances' data-plane IPs), and a reserved stack with no
# alloc ports (skipped).
MQ_TOPO = {
    "nodes": {
        "mon-probe": {"nics": {"net-mgmt": "10.50.0.3"}},
        "nha-x-a1": {"nics": {"net-data-a": "10.10.1.11"}},
        "nha-x-a2": {"nics": {"net-data-a": "10.10.1.12"}},
        "nha-x-a3": {"nics": {"net-data-a": "10.10.1.13"}},
    },
    "groups": {"probe": ["mon-probe"], "nha_x_a": ["nha-x-a1", "nha-x-a2", "nha-x-a3"]},
    "svc": {"short": "SVC", "conn": "10.60.0.50", "exporter_port": 9158},
    "stacks": {
        "pcmk-ubuntu": {
            "short": "PCMK",
            "qm": {"vip": "10.10.1.200"},
            "alloc": {"exporter_app_port": 9157},
        },
        "nha-x": {
            "short": "NHAX",
            "cluster_group": "nha_x_a",
            "qm": {},
            "alloc": {"exporter_app_port": 9163},
        },
        "reserved": {"short": "RSVD", "qm": {}, "alloc": {}},  # no app port -> skipped
    },
}


def test_mq_exporter_instances_app_per_stack_plus_one_shared_svc():
    from mqlab.scrape import mq_exporter_instances

    insts = mq_exporter_instances(MQ_TOPO)
    # reserved stack (no app port) is skipped -> one app per real stack + ONE shared
    # SVCQM svc instance total (#446), not a per-stack {short}SVC.
    assert [(i["qm"], i["role"], i["port"]) for i in insts] == [
        ("PCMKAPP", "app", 9157),
        ("NHAXAPP", "app", 9163),
        ("SVCQM", "svc", 9158),
    ]
    svc = [i for i in insts if i["role"] == "svc"]
    assert len(svc) == 1 and svc[0]["stack"] == "commons"
    assert all(i["channel"] == "MON.SVRCONN" for i in insts)


def test_svc_exporter_conn_matches_its_qm_name_446_regression():
    """The svc instance's conn must resolve to the QM it names — the #446 bug was
    name (e.g. NHAUSVC) vs address (svc-sim answering as another QM) inconsistency."""
    from mqlab.scrape import mq_exporter_instances

    svc = next(i for i in mq_exporter_instances(MQ_TOPO) if i["role"] == "svc")
    assert svc["qm"] == "SVCQM"
    assert svc["conn"] == "10.60.0.50(1414)"


def test_mq_exporter_app_conn_is_vip_or_native_ha_instance_list():
    from mqlab.scrape import mq_exporter_instances

    by_qm = {i["qm"]: i for i in mq_exporter_instances(MQ_TOPO)}
    # VIP arm: the app conn is the VIP; the single shared svc conn is svc-sim's address
    assert by_qm["PCMKAPP"]["conn"] == "10.10.1.200(1414)"
    assert by_qm["SVCQM"]["conn"] == "10.60.0.50(1414)"
    # VIP-less Native HA: the app conn lists every site-A instance (data plane)
    assert by_qm["NHAXAPP"]["conn"] == "10.10.1.11(1414),10.10.1.12(1414),10.10.1.13(1414)"


def test_render_mq_scrape_targets_file_sd_on_the_probe():
    from mqlab.scrape import render_mq_scrape_targets

    out = json.loads(render_mq_scrape_targets(MQ_TOPO))
    assert out[0] == {
        "targets": ["10.50.0.3:9157"],
        "labels": {"host": "mon-probe", "stack": "pcmk-ubuntu", "role": "app"},
    }
    # two app targets + one shared svc target (9158), no per-stack svc ports (#446)
    assert {t["targets"][0] for t in out} == {
        "10.50.0.3:9157",
        "10.50.0.3:9163",
        "10.50.0.3:9158",
    }


def test_render_mq_exporters_wraps_the_instance_list():
    from mqlab.scrape import render_mq_exporters

    out = json.loads(render_mq_exporters(MQ_TOPO))
    assert list(out) == ["mq_exporters"]
    assert len(out["mq_exporters"]) == 3  # 2 app + 1 shared svc (#446)


def test_app_conn_without_vip_or_cluster_group_is_fail_loud():
    from mqlab.scrape import mq_exporter_instances

    _ports = {"exporter_app_port": 1}
    topo = {
        "nodes": {"mon-probe": {"nics": {"net-mgmt": "10.50.0.3"}}},
        "groups": {"probe": ["mon-probe"]},
        "stacks": {"broken": {"short": "BAD", "qm": {}, "alloc": _ports}},
    }
    with pytest.raises(ScrapeError, match="no QM VIP and no cluster_group"):
        mq_exporter_instances(topo)


def test_native_ha_conn_needs_data_plane_ips():
    from mqlab.scrape import mq_exporter_instances

    _ports = {"exporter_app_port": 1}
    topo = {
        "nodes": {"mon-probe": {"nics": {"net-mgmt": "10.50.0.3"}}, "n1": {"nics": {}}},
        "groups": {"probe": ["mon-probe"], "g": ["n1"]},
        "stacks": {"s": {"short": "S", "cluster_group": "g", "qm": {}, "alloc": _ports}},
    }
    with pytest.raises(ScrapeError, match="no net-data-a IP"):
        mq_exporter_instances(topo)


def test_missing_cluster_group_hosts_is_fail_loud():
    from mqlab.scrape import mq_exporter_instances

    _ports = {"exporter_app_port": 1}
    topo = {
        "nodes": {"mon-probe": {"nics": {"net-mgmt": "10.50.0.3"}}},
        "groups": {"probe": ["mon-probe"]},
        "stacks": {"s": {"short": "S", "cluster_group": "ghost", "qm": {}, "alloc": _ports}},
    }
    with pytest.raises(ScrapeError, match="has no hosts"):
        mq_exporter_instances(topo)


def test_svc_exporter_instance_fail_loud_on_incomplete_svc_block():
    from mqlab.scrape import mq_exporter_instances

    # a valid (stackless) topology whose svc: block omits conn/exporter_port must fail
    # loud rather than silently emit a half-configured svc target (#446).
    topo = {"nodes": {}, "groups": {"probe": ["mon-probe"]}, "stacks": {}, "svc": {"short": "SVC"}}
    with pytest.raises(ScrapeError, match="svc"):
        mq_exporter_instances(topo)


def test_render_mq_scrape_targets_needs_a_probe_host():
    from mqlab.scrape import render_mq_scrape_targets

    with pytest.raises(ScrapeError, match="no 'probe' group host"):
        render_mq_scrape_targets({"nodes": {}, "groups": {}, "stacks": {}})


def test_lab_mq_renderers_read_the_real_topology():
    from mqlab.scrape import lab_mq_exporters, lab_mq_scrape_targets

    # the real topology renders without error and covers all four canonical stacks
    exporters = json.loads(lab_mq_exporters())["mq_exporters"]
    qms = {e["qm"] for e in exporters}
    assert {"PCMKAPP", "RDQMAPP", "NHARAPP", "NHAUAPP"} <= qms
    # exactly one shared svc QM, and no per-stack {short}SVC survivors (#446)
    assert "SVCQM" in qms
    assert not any(q.endswith("SVC") for q in qms)
    assert sum(1 for e in exporters if e["role"] == "svc") == 1
    assert json.loads(lab_mq_scrape_targets())  # non-empty file_sd list
