"""Host-contention sampler (#1203): Sampler loop semantics + the real SampleSource's parsers.

No live calls: the Sampler is driven through a FakeSource and explicit `tick()`s, and the
real source is fed fixed `virsh` / `/proc/stat` output through an injected runner.
"""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import astuple
from typing import Any

import pytest

from mqlab import perfsampler
from mqlab.inventory import INSECURE_KEY
from mqlab.perf import PerfRecord
from mqlab.perfsampler import (
    CpuCounters,
    CpuPcts,
    GuestSample,
    HostSample,
    LoadAvg,
    LocalSample,
    RealSource,
    Sampler,
    SampleSource,
    cpu_pcts,
    parse_guest_probe,
    parse_loadavg,
    parse_nodecpustats,
    parse_nodeinfo_cpus,
    parse_proc_stat_cpu,
    read_local_proc_stat,
    run_bounded,
)

# Real output captured from `virsh -c qemu:///system nodecpustats --percent` / `nodeinfo`
# and `head -n1 /proc/stat` on the lab host.
NODECPUSTATS = """usage:            0.9%
user:             0.1%
system:           0.8%
idle:            99.1%
iowait:           0.0%
"""
NODEINFO = """CPU model:           aarch64
CPU(s):              24
CPU socket(s):       1
Core(s) per socket:  24
Thread(s) per core:  1
NUMA cell(s):        1
Memory size:         65710000 KiB
"""
PROC_STAT = "cpu  31044 0 25511 6238486 1109 0 686 0 0 0\n"
# `cat /proc/loadavg` on a lab guest.
LOADAVG = "0.52 0.58 0.59 1/467 12345\n"


def _g(steal: float | None) -> dict[str, float | None]:
    """A guest's serialised sample as the FakeSource produces it (steal only)."""
    return {
        "steal": steal,
        "busy": None,
        "iowait": None,
        "load1": None,
        "load5": None,
        "load15": None,
    }


# The FakeSource's host + local readings, serialised.
HOST = {
    "cpu": 12.5,
    "iowait": 1.5,
    "cpus": 24,
    "self_steal": 1.0,
    "self_busy": 30.0,
    "self_iowait": 2.0,
}


class FakeSource:
    """Canned host/guest samples; guests listed in `failing` raise on every probe."""

    def __init__(self, steal: dict[str, list[float | None]], failing: set[str] | None = None):
        self._steal = {name: list(vals) for name, vals in steal.items()}
        self.failing = failing or set()
        self.host_fails = False
        self.local_fails = False

    def host(self) -> HostSample:
        if self.host_fails:
            raise RuntimeError("virsh exited 1: failed to connect")
        return HostSample(cpu_pct=12.5, iowait_pct=1.5, cpus=24)

    def local(self) -> LocalSample:
        if self.local_fails:
            raise OSError("/proc/stat unreadable")
        return LocalSample(steal_pct=1.0, busy_pct=30.0, iowait_pct=2.0)

    def guest(self, name: str) -> GuestSample:
        if name in self.failing:
            raise RuntimeError(f"ssh exited 255: connect to {name}: No route to host")
        return GuestSample(steal_pct=self._steal[name].pop(0))


def _clock(values: list[float]):
    it = iter(values)
    return lambda: next(it)


def _samples(rec: PerfRecord) -> list[dict[str, Any]]:
    return json.loads(rec.to_json())["samples"]


def test_fake_source_satisfies_the_protocol():
    src: SampleSource = FakeSource({})
    assert src.host().cpus == 24


def test_ticked_n_times_records_n_samples_with_expected_steal():
    rec = PerfRecord(stack="s", started_at=100.0)
    src = FakeSource({"obs": [None, 2.0, 7.5], "qm-a": [None, 0.5, 1.0]})
    sampler = Sampler(rec, src, ["obs", "qm-a"], interval=0, clock=_clock([115.0, 130.0, 145.0]))
    for _ in range(3):
        sampler.tick()
    samples = _samples(rec)
    assert [s["t"] for s in samples] == [15.0, 30.0, 45.0]
    assert [s["guests"]["obs"]["steal"] for s in samples] == [None, 2.0, 7.5]
    assert [s["guests"]["qm-a"]["steal"] for s in samples] == [None, 0.5, 1.0]
    assert samples[0]["host"] == HOST
    assert rec.notes == []


def test_a_failing_guest_records_one_note_but_sampling_continues():
    rec = PerfRecord(stack="s", started_at=0.0)
    src = FakeSource({"obs": [1.0, 2.0, 3.0]}, failing={"qm-a"})
    sampler = Sampler(rec, src, ["obs", "qm-a"], interval=0, clock=_clock([1.0, 2.0, 3.0]))
    for _ in range(3):
        sampler.tick()
    samples = _samples(rec)
    assert len(samples) == 3
    assert [s["guests"] for s in samples] == [
        {"obs": _g(1.0)},
        {"obs": _g(2.0)},
        {"obs": _g(3.0)},
    ]
    # Repeated identical failures are noted once (not once per tick) ...
    assert len(rec.notes) == 1
    assert "guest qm-a: sample unavailable" in rec.notes[0]
    assert "No route to host" in rec.notes[0]
    assert "RuntimeError" in rec.notes[0]


def test_a_changed_failure_message_is_noted_again():
    rec = PerfRecord(stack="s", started_at=0.0)

    class Flaky(FakeSource):
        def __init__(self) -> None:
            super().__init__({})
            self.n = 0

        def guest(self, name: str) -> GuestSample:
            self.n += 1
            raise TimeoutError(f"attempt {self.n}")

    sampler = Sampler(rec, Flaky(), ["obs"], interval=0, clock=_clock([1.0, 2.0]))
    sampler.tick()
    sampler.tick()
    assert rec.notes == [
        "guest obs: sample unavailable (TimeoutError: attempt 1)",
        "guest obs: sample unavailable (TimeoutError: attempt 2)",
    ]


def test_a_recovered_probe_is_noted_with_its_failure_count():
    rec = PerfRecord(stack="s", started_at=0.0)
    src = FakeSource({"obs": [4.0]}, failing={"obs"})
    sampler = Sampler(rec, src, ["obs"], interval=0, clock=_clock([1.0, 2.0, 3.0]))
    sampler.tick()
    sampler.tick()
    src.failing = set()
    sampler.tick()
    assert rec.notes[-1] == "guest obs: sample recovered after 2 failed probe(s)"
    assert _samples(rec)[-1]["guests"] == {"obs": _g(4.0)}


def test_a_failing_host_probe_records_none_and_a_note():
    rec = PerfRecord(stack="s", started_at=0.0)
    src = FakeSource({"obs": [1.0]})
    src.host_fails = True
    Sampler(rec, src, ["obs"], interval=0, clock=_clock([5.0])).tick()
    assert _samples(rec)[0]["host"] == {
        **HOST,
        "cpu": None,
        "iowait": None,
        "cpus": None,
    }
    assert _samples(rec)[0]["guests"] == {"obs": _g(1.0)}
    assert rec.notes == [
        "host: sample unavailable (RuntimeError: virsh exited 1: failed to connect)"
    ]


def test_a_failing_local_probe_records_none_and_a_note_but_keeps_the_host():
    """#1215: the Vergil VM's own /proc/stat is its own probe — its failure is noted and
    blanks only the self_* fields, never the virsh host reading or the guests."""
    rec = PerfRecord(stack="s", started_at=0.0)
    src = FakeSource({"obs": [1.0, 2.0]})
    src.local_fails = True
    sampler = Sampler(rec, src, ["obs"], interval=0, clock=_clock([5.0, 6.0]))
    sampler.tick()
    assert _samples(rec)[0]["host"] == {
        **HOST,
        "self_steal": None,
        "self_busy": None,
        "self_iowait": None,
    }
    assert rec.notes == ["vergil-vm: sample unavailable (OSError: /proc/stat unreadable)"]
    src.local_fails = False
    sampler.tick()
    assert _samples(rec)[1]["host"] == HOST
    assert rec.notes[-1] == "vergil-vm: sample recovered after 1 failed probe(s)"


def test_no_guests_still_samples_the_host():
    rec = PerfRecord(stack="s", started_at=0.0)
    Sampler(rec, FakeSource({}), [], interval=0, clock=_clock([5.0])).tick()
    assert _samples(rec) == [{"t": 5.0, "host": HOST, "guests": {}}]


def test_start_stop_runs_the_loop_on_a_daemon_thread():
    rec = PerfRecord(stack="s", started_at=0.0)
    src = FakeSource({"obs": [1.0]})
    sampler = Sampler(rec, src, ["obs"], interval=3600, clock=lambda: 1.0)
    sampler.start()
    assert sampler.thread is not None
    assert sampler.thread.daemon
    sampler.stop()
    assert not sampler.thread.is_alive()
    # The loop ticks immediately, then the stop wakes the long interval wait.
    assert len(_samples(rec)) == 1
    assert rec.notes == []


def test_start_twice_is_refused():
    sampler = Sampler(PerfRecord(stack="s", started_at=0.0), FakeSource({}), [], interval=3600)
    sampler.start()
    try:
        with pytest.raises(RuntimeError, match="already started"):
            sampler.start()
    finally:
        sampler.stop()


def test_stop_without_start_is_a_no_op():
    rec = PerfRecord(stack="s", started_at=0.0)
    Sampler(rec, FakeSource({}), []).stop()
    assert rec.notes == []


def test_stop_notes_probes_still_failing():
    rec = PerfRecord(stack="s", started_at=0.0)
    src = FakeSource({}, failing={"qm-a"})
    sampler = Sampler(rec, src, ["qm-a"], interval=0, clock=_clock([1.0, 2.0, 3.0]))
    sampler.tick()
    sampler.tick()
    sampler.stop()
    assert rec.notes[-1] == "guest qm-a: still unavailable at stop (2 consecutive failed probe(s))"
    # Reported once: a second stop does not repeat it.
    n = len(rec.notes)
    sampler.stop()
    assert len(rec.notes) == n


def test_the_loop_keeps_ticking_every_interval_until_stopped():
    rec = PerfRecord(stack="s", started_at=0.0)
    ticked_twice = threading.Event()

    class Counting(FakeSource):
        calls = 0

        def host(self) -> HostSample:
            Counting.calls += 1
            if Counting.calls >= 2:
                ticked_twice.set()
            return super().host()

    sampler = Sampler(rec, Counting({}), [], interval=0.001, clock=lambda: 1.0)
    sampler.start()
    assert ticked_twice.wait(5)
    sampler.stop()
    assert len(_samples(rec)) >= 2


def test_stop_notes_a_thread_that_will_not_join():
    rec = PerfRecord(stack="s", started_at=0.0)
    release = threading.Event()
    entered = threading.Event()

    class Hung(FakeSource):
        def host(self) -> HostSample:
            entered.set()
            release.wait(5)
            return super().host()

    sampler = Sampler(rec, Hung({}), [], interval=3600, join_timeout=0.01)
    sampler.start()
    assert entered.wait(5)
    sampler.stop()
    assert rec.notes == ["sampler: thread did not stop within 0.01s; abandoning it (daemon)"]
    release.set()
    assert sampler.thread is not None
    sampler.thread.join(5)


def test_a_tick_that_blows_up_is_noted_and_the_loop_continues(monkeypatch):
    rec = PerfRecord(stack="s", started_at=0.0)
    calls = 0

    def boom(*_a: object, **_k: object) -> None:
        nonlocal calls
        calls += 1
        raise ValueError("bad sample")

    monkeypatch.setattr(rec, "add_sample", boom)
    sampler = Sampler(rec, FakeSource({}), [], interval=3600, clock=lambda: 1.0)
    sampler.start()
    sampler.stop()
    assert calls == 1
    assert rec.notes == ["sampler: tick failed (ValueError: bad sample); continuing"]


# --- pure parsers -----------------------------------------------------------------------


def test_parse_nodecpustats_returns_usage_and_iowait():
    assert parse_nodecpustats(NODECPUSTATS) == (0.9, 0.0)


def test_parse_nodecpustats_ignores_non_percentage_lines():
    text = "CPU: all\n" + NODECPUSTATS
    assert parse_nodecpustats(text) == (0.9, 0.0)


def test_parse_nodecpustats_fails_loud_on_missing_field():
    with pytest.raises(ValueError, match="iowait"):
        parse_nodecpustats("usage:  3.0%\nidle:  97.0%\n")


def test_parse_nodeinfo_cpus():
    assert parse_nodeinfo_cpus(NODEINFO) == 24


def test_parse_nodeinfo_fails_loud_without_cpu_line():
    with pytest.raises(ValueError, match=r"CPU\(s\)"):
        parse_nodeinfo_cpus("CPU model: aarch64\n")


def test_parse_proc_stat_cpu():
    assert parse_proc_stat_cpu(PROC_STAT) == CpuCounters(
        steal=0,
        total=31044 + 0 + 25511 + 6238486 + 1109 + 0 + 686 + 0,
        busy=31044 + 0 + 25511 + 0 + 686,
        iowait=1109,
    )


def test_parse_proc_stat_cpu_reads_the_steal_busy_and_iowait_columns():
    # user nice system idle iowait irq softirq steal guest guest_nice
    c = parse_proc_stat_cpu("cpu  10 1 20 70 7 2 3 11 5 0")
    assert c == CpuCounters(steal=11, total=124, busy=10 + 1 + 20 + 2 + 3, iowait=7)


def test_parse_loadavg():
    assert parse_loadavg(LOADAVG) == LoadAvg(0.52, 0.58, 0.59)


@pytest.mark.parametrize("text", ["", "0.52 0.58", "0.52 x 0.59 1/467 12345"])
def test_parse_loadavg_fails_loud_on_bad_input(text):
    with pytest.raises(ValueError, match="/proc/loadavg"):
        parse_loadavg(text)


def test_parse_guest_probe_reads_both_lines_of_the_one_ssh_call():
    assert parse_guest_probe(PROC_STAT + LOADAVG) == (
        parse_proc_stat_cpu(PROC_STAT),
        LoadAvg(0.52, 0.58, 0.59),
    )


def test_parse_guest_probe_fails_loud_without_the_loadavg_line():
    with pytest.raises(ValueError, match="lacks the /proc/stat"):
        parse_guest_probe(PROC_STAT)


@pytest.mark.parametrize(
    "text",
    ["", "cpu0 1 2 3 4 5 6 7 8", "cpu  1 2 3", "intr 1 2 3 4 5 6 7 8"],
)
def test_parse_proc_stat_cpu_fails_loud_on_bad_input(text):
    with pytest.raises(ValueError, match="/proc/stat"):
        parse_proc_stat_cpu(text)


def test_cpu_pcts_are_the_delta_ratios_of_one_total():
    prev = CpuCounters(steal=10, total=1000, busy=100, iowait=5)
    cur = CpuCounters(steal=30, total=1200, busy=180, iowait=15)
    assert cpu_pcts(prev, cur) == CpuPcts(steal=10.0, busy=40.0, iowait=5.0)


@pytest.mark.parametrize(
    "later",
    [
        CpuCounters(steal=5, total=2000, busy=200, iowait=10),
        CpuCounters(steal=20, total=2000, busy=50, iowait=10),
        CpuCounters(steal=20, total=2000, busy=200, iowait=1),
        CpuCounters(steal=10, total=1000, busy=100, iowait=5),
    ],
    ids=["steal reset", "busy reset", "iowait reset", "no elapsed ticks"],
)
def test_cpu_pcts_is_none_when_no_valid_delta(later):
    assert cpu_pcts(CpuCounters(steal=10, total=1000, busy=100, iowait=5), later) is None


def test_read_local_proc_stat_reads_the_aggregate_line():
    """A local file read of this (Linux) machine's /proc/stat — not a lab call."""
    assert parse_proc_stat_cpu(read_local_proc_stat()).total > 0


# --- run_bounded -------------------------------------------------------------------------


def test_run_bounded_returns_stdout():
    assert run_bounded([sys.executable, "-c", "print('hi')"], 10) == "hi\n"


def test_run_bounded_raises_on_nonzero_with_stderr():
    argv = [sys.executable, "-c", "import sys; sys.stderr.write('nope'); sys.exit(3)"]
    with pytest.raises(RuntimeError, match="exited 3: nope"):
        run_bounded(argv, 10)


def test_run_bounded_falls_back_to_stdout_when_stderr_empty():
    argv = [sys.executable, "-c", "import sys; print('out'); sys.exit(2)"]
    with pytest.raises(RuntimeError, match="exited 2: out"):
        run_bounded(argv, 10)


def test_run_bounded_raises_on_timeout():
    argv = [sys.executable, "-c", "import time; time.sleep(5)"]
    with pytest.raises(RuntimeError, match=r"timed out after 0\.2s"):
        run_bounded(argv, 0.2)


# --- RealSource --------------------------------------------------------------------------


class FakeRun:
    """Records argv; answers from a queue of (predicate-free) outputs keyed by argv[0]."""

    def __init__(self, outputs: dict[str, list[str]]):
        self.outputs = {k: list(v) for k, v in outputs.items()}
        self.calls: list[tuple[list[str], float]] = []

    def __call__(self, argv: list[str], timeout: float) -> str:
        self.calls.append((argv, timeout))
        key = argv[3] if argv[0] == "virsh" else argv[0]
        return self.outputs[key].pop(0)


def test_real_source_host_parses_virsh_and_caches_cpu_count():
    run = FakeRun({"nodecpustats": [NODECPUSTATS, NODECPUSTATS], "nodeinfo": [NODEINFO]})
    src = RealSource({}, run=run, timeout=7.0)
    assert src.host() == HostSample(cpu_pct=0.9, iowait_pct=0.0, cpus=24)
    assert src.host().cpus == 24
    argvs = [argv for argv, _ in run.calls]
    assert argvs.count(["virsh", "-c", "qemu:///system", "nodeinfo"]) == 1
    assert ["virsh", "-c", "qemu:///system", "nodecpustats", "--percent"] in argvs
    assert all(t == 7.0 for _, t in run.calls)


def test_real_source_guest_first_reading_is_a_baseline_then_deltas():
    # user nice system idle iowait irq softirq steal guest guest_nice
    run = FakeRun(
        {
            "ssh": [
                "cpu  100 0 100 700 0 0 0 100 0 0\n" + LOADAVG,
                "cpu  150 0 170 850 30 0 0 150 0 0\n2.00 1.50 1.25 3/470 12400\n",
            ]
        }
    )
    src = RealSource({"obs": "192.168.121.10"}, run=run)
    assert src.guest("obs") == GuestSample(
        steal_pct=None, busy_pct=None, iowait_pct=None, load1=0.52, load5=0.58, load15=0.59
    )
    # delta total = 50 + 70 + 150 + 30 + 50 = 350 ticks
    # astuple order: steal, busy, iowait, then the three load averages
    assert astuple(src.guest("obs")) == pytest.approx(
        (50 / 350 * 100, 120 / 350 * 100, 30 / 350 * 100, 2.0, 1.5, 1.25)
    )
    assert len(run.calls) == 2  # exactly ONE ssh round trip per guest per reading
    argv, _ = run.calls[0]
    assert argv[0] == "ssh"
    assert argv[-2] == "vagrant@192.168.121.10"
    assert argv[-1] == "head -n1 /proc/stat && cat /proc/loadavg"
    # The same key + host-key opts the Ansible inventory uses for guests (reused, not new).
    assert ["-i", perfsampler.os.path.expanduser(INSECURE_KEY)] == argv[
        argv.index("-i") : argv.index("-i") + 2
    ]
    assert "StrictHostKeyChecking=no" in argv
    assert "BatchMode=yes" in argv


def test_real_source_local_reads_the_sampling_vms_own_proc_stat():
    """#1215: baseline first, then steal/busy/iowait deltas; a counter reset (reboot)
    yields another all-None reading rather than a negative number."""
    lines = iter(
        [
            "cpu  100 0 100 700 0 0 0 100 0 0\n",
            "cpu  150 0 150 850 20 0 0 130 0 0\n",  # +300 total: 30 steal, 100 busy, 20 io
            "cpu  1 0 1 7 0 0 0 1 0 0\n",  # counters went backwards
        ]
    )
    src = RealSource({}, run=FakeRun({}), read_local=lambda: next(lines))
    assert src.local() == LocalSample(steal_pct=None, busy_pct=None, iowait_pct=None)
    assert astuple(src.local()) == pytest.approx((10.0, 100 / 300 * 100, 20 / 300 * 100))
    assert src.local() == LocalSample(steal_pct=None, busy_pct=None, iowait_pct=None)


def test_real_source_unknown_guest_fails_loud():
    src = RealSource({}, run=FakeRun({}))
    with pytest.raises(LookupError, match="no net-mgmt address for guest 'ghost'"):
        src.guest("ghost")


def test_real_source_from_topology_maps_net_mgmt_addresses():
    topo = {
        "nodes": {
            "obs": {"nics": {"net-mgmt": "192.168.121.10", "net-data-a": "10.0.0.1"}},
            "qm-a": {"nics": {"net-mgmt": "192.168.121.11"}},
            "odd": {"nics": {"net-data-a": "10.0.0.9"}},
            "bare": {},
            "unset": None,
        }
    }
    src = RealSource.from_topology(topo)
    assert src.addresses == {"obs": "192.168.121.10", "qm-a": "192.168.121.11"}


def test_real_source_from_empty_topology():
    assert RealSource.from_topology({}).addresses == {}
