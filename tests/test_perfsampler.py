"""Host-contention sampler (#1203): Sampler loop semantics + the real SampleSource's parsers.

No live calls: the Sampler is driven through a FakeSource and explicit `tick()`s, and the
real source is fed fixed `virsh` / `/proc/stat` output through an injected runner.
"""

from __future__ import annotations

import json
import sys
import threading
from typing import Any

import pytest

from mqlab import perfsampler
from mqlab.inventory import INSECURE_KEY
from mqlab.perf import PerfRecord
from mqlab.perfsampler import (
    CpuCounters,
    GuestSample,
    HostSample,
    RealSource,
    Sampler,
    SampleSource,
    parse_nodecpustats,
    parse_nodeinfo_cpus,
    parse_proc_stat_cpu,
    run_bounded,
    steal_pct,
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


class FakeSource:
    """Canned host/guest samples; guests listed in `failing` raise on every probe."""

    def __init__(self, steal: dict[str, list[float | None]], failing: set[str] | None = None):
        self._steal = {name: list(vals) for name, vals in steal.items()}
        self.failing = failing or set()
        self.host_fails = False

    def host(self) -> HostSample:
        if self.host_fails:
            raise RuntimeError("virsh exited 1: failed to connect")
        return HostSample(cpu_pct=12.5, iowait_pct=1.5, cpus=24)

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
    assert samples[0]["host"] == {"cpu": 12.5, "iowait": 1.5, "cpus": 24}
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
        {"obs": {"steal": 1.0}},
        {"obs": {"steal": 2.0}},
        {"obs": {"steal": 3.0}},
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
    assert _samples(rec)[-1]["guests"] == {"obs": {"steal": 4.0}}


def test_a_failing_host_probe_records_none_and_a_note():
    rec = PerfRecord(stack="s", started_at=0.0)
    src = FakeSource({"obs": [1.0]})
    src.host_fails = True
    Sampler(rec, src, ["obs"], interval=0, clock=_clock([5.0])).tick()
    assert _samples(rec)[0]["host"] == {"cpu": None, "iowait": None, "cpus": None}
    assert _samples(rec)[0]["guests"] == {"obs": {"steal": 1.0}}
    assert rec.notes == [
        "host: sample unavailable (RuntimeError: virsh exited 1: failed to connect)"
    ]


def test_no_guests_still_samples_the_host():
    rec = PerfRecord(stack="s", started_at=0.0)
    Sampler(rec, FakeSource({}), [], interval=0, clock=_clock([5.0])).tick()
    assert _samples(rec) == [
        {"t": 5.0, "host": {"cpu": 12.5, "iowait": 1.5, "cpus": 24}, "guests": {}}
    ]


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
        steal=0, total=31044 + 0 + 25511 + 6238486 + 1109 + 0 + 686 + 0
    )


def test_parse_proc_stat_cpu_reads_the_steal_column():
    assert parse_proc_stat_cpu("cpu  10 0 10 70 0 0 0 10 5 0").steal == 10


@pytest.mark.parametrize(
    "text",
    ["", "cpu0 1 2 3 4 5 6 7 8", "cpu  1 2 3", "intr 1 2 3 4 5 6 7 8"],
)
def test_parse_proc_stat_cpu_fails_loud_on_bad_input(text):
    with pytest.raises(ValueError, match="/proc/stat"):
        parse_proc_stat_cpu(text)


def test_steal_pct_is_the_delta_ratio():
    assert steal_pct(CpuCounters(steal=10, total=1000), CpuCounters(steal=30, total=1200)) == 10.0


@pytest.mark.parametrize(
    "later",
    [CpuCounters(steal=5, total=2000), CpuCounters(steal=10, total=1000)],
    ids=["counter-reset (guest rebooted)", "no elapsed ticks"],
)
def test_steal_pct_is_none_when_no_valid_delta(later):
    assert steal_pct(CpuCounters(steal=10, total=1000), later) is None


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
    run = FakeRun(
        {
            "ssh": [
                "cpu  100 0 100 700 0 0 0 100 0 0\n",
                "cpu  150 0 150 850 0 0 0 150 0 0\n",
            ]
        }
    )
    src = RealSource({"obs": "192.168.121.10"}, run=run)
    assert src.guest("obs") == GuestSample(steal_pct=None)
    assert src.guest("obs") == GuestSample(steal_pct=50 / 300 * 100)
    argv, _ = run.calls[0]
    assert argv[0] == "ssh"
    assert argv[-2] == "vagrant@192.168.121.10"
    assert argv[-1] == "head -n1 /proc/stat"
    # The same key + host-key opts the Ansible inventory uses for guests (reused, not new).
    assert ["-i", perfsampler.os.path.expanduser(INSECURE_KEY)] == argv[
        argv.index("-i") : argv.index("-i") + 2
    ]
    assert "StrictHostKeyChecking=no" in argv
    assert "BatchMode=yes" in argv


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
