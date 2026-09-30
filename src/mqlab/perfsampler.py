"""Host-contention sampler (#1203): vCPU steal per guest + host CPU/iowait, into a PerfRecord.

During a bootstrap a daemon thread snapshots, every `interval` seconds, the host's CPU and
iowait (`virsh nodecpustats --percent`, plus the host CPU count from `virsh nodeinfo`) and
each guest's vCPU steal% (deltas of the aggregate `cpu` line of the guest's `/proc/stat`,
read over ssh), and appends one sample to the run's `PerfRecord` (epic #275 spec §1).

Non-fatal, never silent: every probe runs in its own try/except, and a failure degrades
to a recorded `note` in the report (deduplicated — a guest that is not up yet is noted
once, then again when it recovers or at stop if it never did). Nothing here raises into
the bootstrap once the sampler is running.

The `SampleSource` Protocol is the seam: `Sampler` is exercised in tests with a fake
source and explicit `tick()`s; `RealSource` is the lab implementation, whose output
parsing is pure functions tested against fixed strings.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from mqlab.inventory import INSECURE_KEY, SSH_COMMON_ARGS, SSH_USER

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from mqlab.perf import PerfRecord

_VIRSH = ["virsh", "-c", "qemu:///system"]
# The aggregate `cpu` line is always first in /proc/stat.
_PROC_STAT_CMD = "head -n1 /proc/stat"
# Unattended probes must never prompt or hang on connect: fail fast into a note instead.
_SSH_PROBE_ARGS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "LogLevel=ERROR")
# /proc/stat cpu columns: user nice system idle iowait irq softirq steal guest guest_nice.
# guest/guest_nice are already included in user/nice, so the total is the first eight.
_STEAL_COL = 7
_TOTAL_COLS = 8
_MAX_PROBE_WORKERS = 16


@dataclass(frozen=True)
class HostSample:
    """One host reading: CPU busy % and iowait % (and the host's CPU count, if known)."""

    cpu_pct: float
    iowait_pct: float
    cpus: int | None = None


@dataclass(frozen=True)
class GuestSample:
    """One guest reading. `steal_pct` is None for a baseline reading (no delta yet)."""

    steal_pct: float | None


class SampleSource(Protocol):
    """Where the sampler gets its readings. Each call may raise; the Sampler notes it."""

    def host(self) -> HostSample: ...

    def guest(self, name: str) -> GuestSample: ...


class Sampler:
    """Periodically samples `source` into `record` on a daemon thread.

    `tick()` takes one sample synchronously (the unit tests drive it directly); `start()`
    runs `tick()` immediately and then every `interval` seconds until `stop()`, which
    wakes the wait, joins with `join_timeout`, and notes any probe still failing.
    """

    def __init__(
        self,
        record: PerfRecord,
        source: SampleSource,
        guests: Iterable[str],
        interval: float = 15.0,
        *,
        clock: Callable[[], float] = time.time,
        join_timeout: float = 30.0,
    ) -> None:
        self._record = record
        self._source = source
        self._guests = list(guests)
        self._interval = interval
        self._clock = clock
        self._join_timeout = join_timeout
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None
        # Per-probe failure tracking, so a persistently failing probe is noted once.
        self._fail_count: dict[str, int] = {}
        self._last_error: dict[str, str] = {}

    def _failed(self, key: str, exc: BaseException) -> None:
        self._fail_count[key] = self._fail_count.get(key, 0) + 1
        msg = f"{type(exc).__name__}: {exc}"
        if self._last_error.get(key) != msg:
            self._last_error[key] = msg
            self._record.note(f"{key}: sample unavailable ({msg})")

    def _succeeded(self, key: str) -> None:
        if key in self._last_error:
            del self._last_error[key]
            n = self._fail_count.pop(key)
            self._record.note(f"{key}: sample recovered after {n} failed probe(s)")

    def _probe_guest(self, name: str) -> GuestSample | Exception:
        try:
            return self._source.guest(name)
        except Exception as exc:  # noqa: BLE001 - degraded into a note by tick(), never lost
            return exc

    def tick(self) -> None:
        """Take one sample: host + every guest (probed concurrently), into the record."""
        t = self._clock() - self._record.started_at
        host: HostSample | None = None
        try:
            host = self._source.host()
        except Exception as exc:  # noqa: BLE001 - recorded as a note; sampling continues
            self._failed("host", exc)
        else:
            self._succeeded("host")
        guests: dict[str, GuestSample] = {}
        if self._guests:
            workers = min(len(self._guests), _MAX_PROBE_WORKERS)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(self._probe_guest, self._guests))
            for name, result in zip(self._guests, results, strict=True):
                key = f"guest {name}"
                if isinstance(result, Exception):
                    self._failed(key, result)
                else:
                    self._succeeded(key)
                    guests[name] = result
        self._record.add_sample(
            t,
            host_cpu=host.cpu_pct if host else None,
            host_iowait=host.iowait_pct if host else None,
            guests=guests,
            host_cpus=host.cpus if host else None,
        )

    def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - noted; one bad tick must not end sampling
                self._record.note(f"sampler: tick failed ({type(exc).__name__}: {exc}); continuing")
            if self._stop.wait(self._interval):
                return

    def start(self) -> None:
        """Start sampling on a daemon thread (so it can never hold the process open)."""
        if self.thread is not None:
            raise RuntimeError("Sampler already started")
        self.thread = threading.Thread(target=self._loop, name="perf-sampler", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """Stop sampling; join with a timeout; note a hung thread and still-failing probes."""
        if self.thread is not None:
            self._stop.set()
            self.thread.join(self._join_timeout)
            if self.thread.is_alive():
                self._record.note(
                    f"sampler: thread did not stop within {self._join_timeout}s; "
                    "abandoning it (daemon)"
                )
        # Snapshot: an abandoned (hung) thread may still be mutating the tracking dicts.
        still_failing = list(self._fail_count.items())
        self._fail_count.clear()
        self._last_error.clear()
        for key, n in still_failing:
            self._record.note(f"{key}: still unavailable at stop ({n} consecutive failed probe(s))")


# --- the real (lab) source --------------------------------------------------------------


@dataclass(frozen=True)
class CpuCounters:
    """The aggregate /proc/stat `cpu` counters the steal delta needs (in USER_HZ ticks)."""

    steal: int
    total: int


def parse_nodecpustats(text: str) -> tuple[float, float]:
    """`virsh nodecpustats --percent` -> (usage %, iowait %). Raises on a missing field."""
    fields: dict[str, float] = {}
    for line in text.splitlines():
        name, sep, value = line.partition(":")
        if sep and value.strip().endswith("%"):
            fields[name.strip()] = float(value.strip().rstrip("%"))
    missing = [f for f in ("usage", "iowait") if f not in fields]
    if missing:
        raise ValueError(f"virsh nodecpustats output lacks {', '.join(missing)}: {text!r}")
    return fields["usage"], fields["iowait"]


def parse_nodeinfo_cpus(text: str) -> int:
    """`virsh nodeinfo` -> the host's logical CPU count. Raises if the line is absent."""
    for line in text.splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip() == "CPU(s)":
            return int(value.strip())
    raise ValueError(f"virsh nodeinfo output lacks a CPU(s) line: {text!r}")


def parse_proc_stat_cpu(text: str) -> CpuCounters:
    """The aggregate `cpu` line of /proc/stat -> steal + total ticks. Raises if malformed."""
    parts = text.split()
    if len(parts) < _TOTAL_COLS + 1 or parts[0] != "cpu":
        raise ValueError(f"not an aggregate /proc/stat cpu line: {text!r}")
    values = [int(v) for v in parts[1 : _TOTAL_COLS + 1]]
    return CpuCounters(steal=values[_STEAL_COL], total=sum(values))


def steal_pct(prev: CpuCounters, cur: CpuCounters) -> float | None:
    """Steal % over the interval between two readings; None when there is no valid delta
    (no ticks elapsed, or the counters went backwards because the guest rebooted)."""
    d_total = cur.total - prev.total
    d_steal = cur.steal - prev.steal
    if d_total <= 0 or d_steal < 0:
        return None
    return d_steal / d_total * 100


def run_bounded(argv: list[str], timeout: float) -> str:
    """Run a probe command bounded by `timeout`; return stdout. FAIL-LOUD: a timeout or
    non-zero exit raises with the tool's own message (the Sampler turns it into a note)."""
    try:
        cp = subprocess.run(  # noqa: S603 - fixed internal argv; tools on PATH (lab)
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{argv[0]} timed out after {timeout}s") from exc
    if cp.returncode != 0:
        detail = cp.stderr.strip() or cp.stdout.strip()
        raise RuntimeError(f"{argv[0]} exited {cp.returncode}: {detail}")
    return cp.stdout


class RealSource:
    """The lab SampleSource: host via virsh, guest steal via ssh + /proc/stat deltas.

    Guests are reached at their net-mgmt address with the same login, key and host-key
    options the Ansible inventory uses (`mqlab.inventory`). The first reading of each
    guest is a baseline (`steal_pct=None`); every later one is the delta since the last.
    """

    def __init__(
        self,
        addresses: Mapping[str, str],
        *,
        run: Callable[[list[str], float], str] = run_bounded,
        timeout: float = 10.0,
    ) -> None:
        self.addresses = dict(addresses)
        self._run = run
        self._timeout = timeout
        self._cpus: int | None = None
        self._prev: dict[str, CpuCounters] = {}

    @classmethod
    def from_topology(cls, topo: Mapping[str, Any], **kwargs: Any) -> RealSource:
        """Map every topology node that has a net-mgmt NIC. A node without one is simply
        not addressable; probing it raises (and is noted), it is never guessed."""
        addresses: dict[str, str] = {}
        for name, spec in (topo.get("nodes") or {}).items():
            ip = ((spec or {}).get("nics") or {}).get("net-mgmt")
            if ip:
                addresses[name] = str(ip)
        return cls(addresses, **kwargs)

    def host(self) -> HostSample:
        if self._cpus is None:
            self._cpus = parse_nodeinfo_cpus(self._run([*_VIRSH, "nodeinfo"], self._timeout))
        usage, iowait = parse_nodecpustats(
            self._run([*_VIRSH, "nodecpustats", "--percent"], self._timeout)
        )
        return HostSample(cpu_pct=usage, iowait_pct=iowait, cpus=self._cpus)

    def guest(self, name: str) -> GuestSample:
        addr = self.addresses.get(name)
        if addr is None:
            raise LookupError(f"no net-mgmt address for guest {name!r} in topology")
        argv = [
            "ssh",
            *SSH_COMMON_ARGS,
            *_SSH_PROBE_ARGS,
            "-i",
            os.path.expanduser(INSECURE_KEY),  # noqa: PTH111 - ssh argv wants a str path
            f"{SSH_USER}@{addr}",
            _PROC_STAT_CMD,
        ]
        cur = parse_proc_stat_cpu(self._run(argv, self._timeout))
        prev = self._prev.get(name)
        self._prev[name] = cur
        return GuestSample(steal_pct=None if prev is None else steal_pct(prev, cur))
