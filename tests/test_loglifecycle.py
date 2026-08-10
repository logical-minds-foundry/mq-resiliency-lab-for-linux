from __future__ import annotations

import subprocess

from mqlab import loglifecycle


def test_parse_disk_from_df_posix():
    # `df -P <logpath>` — POSIX one-line-per-fs format; blocks are 1024-byte units.
    out = (
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        "/dev/vda2 51200000 20480000 30720000 40% /var/mqm\n"
    )
    d = loglifecycle.parse_disk(out)
    assert d["used_bytes"] == 20480000 * 1024
    assert d["total_bytes"] == 51200000 * 1024


def test_parse_disk_logpath_on_root_filesystem():
    # S3: LogPath may sit on `/` (/dev/vda3) — a mount path of a single token still parses.
    out = (
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        "/dev/vda3 41943040 10485760 31457280 25% /\n"
    )
    d = loglifecycle.parse_disk(out)
    assert d["used_bytes"] == 10485760 * 1024
    assert d["total_bytes"] == 41943040 * 1024


def test_parse_disk_header_only_is_none_not_zero():
    # A truncated df (header only, no data line) must read None — never a false 0 (fail-loud).
    d = loglifecycle.parse_disk("Filesystem 1024-blocks Used Available Capacity Mounted on\n")
    assert d["used_bytes"] is None
    assert d["total_bytes"] is None


def test_parse_disk_nonnumeric_fields_are_none():
    # A malformed/garbled data line must not crash the render; it reads None, not a crash.
    d = loglifecycle.parse_disk("Filesystem blocks\n/dev/vda2 not-a-number also-bad x y z\n")
    assert d["used_bytes"] is None
    assert d["total_bytes"] is None


def test_count_extents_splits_standard_and_reserved():
    # S3: extents are S…LOG (standard -> active) and R…LOG (reserved/recycled -> inactive);
    # count BOTH. Non-extent files (amqhlctl.lfh, nativeha.ini) are ignored.
    listing = [
        "S0000000.LOG",
        "S0000001.LOG",
        "R0000009.LOG",
        "amqhlctl.lfh",
        "nativeha.ini",
        "",
    ]
    c = loglifecycle.count_extents(listing)
    assert c["active"] == 2
    assert c["inactive"] == 1


def test_count_extents_empty_listing_is_zero():
    c = loglifecycle.count_extents([])
    assert c == {"active": 0, "inactive": 0}


def test_instance_role_active_and_replica_from_dspmq_nativeha():
    # thin wrapper over nativehastate.parse_nativeha_x — role lowercased for the metric label.
    out = (
        "QMNAME(QM1) INSTANCE(nha-rhel-a1) ROLE(Active) INSYNC(yes) QUORUM(3/3) "
        "HASTATUS(Normal) GRPROLE(Live)\n"
        " INSTANCE(nha-rhel-a1) ROLE(Active) INSYNC(yes) HASTATUS(Normal)\n"
        " INSTANCE(nha-rhel-a2) ROLE(Replica) INSYNC(yes) HASTATUS(Normal)\n"
    )
    assert loglifecycle.instance_role(out, "nha-rhel-a1") == "active"
    assert loglifecycle.instance_role(out, "nha-rhel-a2") == "replica"


def test_instance_role_unknown_when_instance_absent():
    out = " INSTANCE(nha-rhel-a1) ROLE(Active) INSYNC(yes) HASTATUS(Normal)\n"
    assert loglifecycle.instance_role(out, "nha-rhel-zz") == "unknown"


def test_render_prom_is_node_exporter_textfile():
    row = {
        "qm": "QM1",
        "instance": "nha-rhel-a1",
        "role": "active",
        "used_bytes": 100,
        "total_bytes": 200,
        "active": 2,
        "inactive": 1,
        "stale": 0,
    }
    text = loglifecycle.render_prom(row)
    labels = 'qm="QM1",instance="nha-rhel-a1",role="active"'
    assert f"mqlab_log_disk_used_bytes{{{labels}}} 100" in text
    assert f"mqlab_log_disk_total_bytes{{{labels}}} 200" in text
    assert f"mqlab_log_extents_active{{{labels}}} 2" in text
    assert f"mqlab_log_extents_inactive{{{labels}}} 1" in text
    assert f"mqlab_log_sample_stale{{{labels}}} 0" in text


def test_render_prom_stale_omits_values_but_always_emits_stale_flag():
    # On a timed-out filesystem sample the values read None -> omitted (never a false 0);
    # only the labeled stale flag is emitted, set to 1, so the cell reads STALE (not 'no data').
    row = {
        "qm": "QM1",
        "instance": "nha-rhel-a3",
        "role": "replica",
        "used_bytes": None,
        "total_bytes": None,
        "active": None,
        "inactive": None,
        "stale": 1,
    }
    text = loglifecycle.render_prom(row)
    assert "mqlab_log_disk_used_bytes" not in text
    assert "mqlab_log_extents_active" not in text
    assert 'mqlab_log_sample_stale{qm="QM1",instance="nha-rhel-a3",role="replica"} 1' in text


def test_render_prom_defaults_stale_to_zero_when_absent():
    row = {"qm": "QM1", "instance": "nha-rhel-a1", "role": "active"}
    text = loglifecycle.render_prom(row)
    assert 'mqlab_log_sample_stale{qm="QM1",instance="nha-rhel-a1",role="active"} 0' in text


def test_probe_returns_stdout_then_none_on_failure(monkeypatch):
    monkeypatch.setattr(
        loglifecycle.subprocess,
        "run",
        lambda c, **k: subprocess.CompletedProcess(c, 0, "out\n", ""),
    )
    assert loglifecycle.probe(["df"], timeout=3) == "out\n"
    monkeypatch.setattr(
        loglifecycle.subprocess,
        "run",
        lambda c, **k: subprocess.CompletedProcess(c, 2, "", "boom"),
    )
    assert loglifecycle.probe(["df"], timeout=3) is None
    monkeypatch.setattr(
        loglifecycle.subprocess,
        "run",
        lambda c, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired(c, k["timeout"])),
    )
    assert loglifecycle.probe(["df"], timeout=3) is None
    monkeypatch.setattr(
        loglifecycle.subprocess,
        "run",
        lambda c, **k: (_ for _ in ()).throw(OSError("no df")),
    )
    assert loglifecycle.probe(["df"], timeout=3) is None


def _fresh_probe(cmd, timeout):
    # route the three sources by their argv shape (mirrors the nativehastate test's routing)
    if cmd[0] == "df":
        return (
            "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
            "/dev/vda3 41943040 10485760 31457280 25% /\n"
        )
    if cmd[0] == "ls":
        return "S0000000.LOG\nS0000001.LOG\nR0000009.LOG\namqhlctl.lfh\n"
    return (  # the dspmq -o nativeha -x role source
        "QMNAME(NHARAPP) INSTANCE(nha-rhel-a2) ROLE(Active) QUORUM(3/3) GRPROLE(Live)\n"
        " INSTANCE(nha-rhel-a2) ROLE(Active) INSYNC(yes) HASTATUS(Normal)\n"
    )


def test_collect_renders_fresh_sample_tagged_by_instance_and_role(monkeypatch):
    monkeypatch.setattr(loglifecycle, "probe", _fresh_probe)
    text = loglifecycle.collect("nha-rhel-a2", "NHARAPP")
    labels = 'qm="NHARAPP",instance="nha-rhel-a2",role="active"'
    assert f"mqlab_log_disk_used_bytes{{{labels}}} {10485760 * 1024}" in text
    assert f"mqlab_log_disk_total_bytes{{{labels}}} {41943040 * 1024}" in text
    assert f"mqlab_log_extents_active{{{labels}}} 2" in text
    assert f"mqlab_log_extents_inactive{{{labels}}} 1" in text
    assert f"mqlab_log_sample_stale{{{labels}}} 0" in text


def test_collect_marks_stale_and_role_unknown_when_all_sources_time_out(monkeypatch):
    monkeypatch.setattr(loglifecycle, "probe", lambda cmd, timeout: None)
    text = loglifecycle.collect("nha-rhel-a3", "NHARAPP")
    labels = 'qm="NHARAPP",instance="nha-rhel-a3",role="unknown"'
    assert "mqlab_log_disk_used_bytes" not in text  # df stale -> omitted
    assert "mqlab_log_extents_active" not in text  # ls stale -> omitted
    assert f"mqlab_log_sample_stale{{{labels}}} 1" in text


def test_main_writes_textfile_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(loglifecycle, "probe", _fresh_probe)
    out = tmp_path / "lab_loglifecycle_state.prom"
    loglifecycle.main(["--qm", "NHARAPP", "--instance", "nha-rhel-a2", "--out", str(out)])
    text = out.read_text()
    assert 'mqlab_log_extents_active{qm="NHARAPP",instance="nha-rhel-a2",role="active"} 2' in text
    assert not (tmp_path / "lab_loglifecycle_state.prom.tmp").exists()  # atomic move cleaned up


def test_main_defaults_instance_to_hostname(tmp_path, monkeypatch):
    monkeypatch.setattr(loglifecycle, "probe", _fresh_probe)
    monkeypatch.setattr(
        loglifecycle.os, "uname", lambda: type("U", (), {"nodename": "nha-rhel-a2"})()
    )
    out = tmp_path / "c.prom"
    loglifecycle.main(["--qm", "NHARAPP", "--out", str(out)])
    assert 'instance="nha-rhel-a2",role="active"' in out.read_text()
