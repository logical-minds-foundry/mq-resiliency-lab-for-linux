"""Host-contention sampler (#1203): vCPU steal per guest + host CPU/iowait, into a PerfRecord.

During a bootstrap a daemon thread snapshots, every `interval` seconds, the host's CPU and
iowait (`virsh nodecpustats --percent`, plus the host CPU count from `virsh nodeinfo`),
the sampling (Vergil) VM's OWN steal/busy/iowait % (#1215: deltas of its local
`/proc/stat` aggregate line — it is itself a guest of the outer hypervisor), and each
guest's vCPU steal/busy/iowait % (deltas of the aggregate `cpu` line of the guest's
`/proc/stat`) plus its `/proc/loadavg` (#1215) — both read in ONE ssh round trip per guest
— and appends one sample to the run's `PerfRecord` (epic #275 spec §1).

One login per guest per run, not per tick (#1221): every Ubuntu ssh login runs PAM's
dynamic MOTD (`landscape-sysinfo`), which on a busy guest outlasts the tick interval and
piled up. So guest probes multiplex over ONE persistent OpenSSH master per guest
(`ControlMaster=auto` + `ControlPersist`); each tick's probe is still a single `ssh`
invocation, now a mux client that reuses the master without logging in again. The
control sockets live in `ssh_mux_dir()` — a per-user 0700 dir on a LOCAL filesystem
(`$XDG_RUNTIME_DIR`, else the system temp dir), deliberately NOT under `build/` (#1228:
on macOS `build/` is a virtiofs mount that cannot hold a Unix socket) — under a RELATIVE
`ControlPath=%C`, with the probe run from that directory, so the socket path is 40 hex
chars whatever the directory's depth (the Unix socket-path limit is ~104 bytes).
`Sampler.stop()` closes the masters (`ssh -O exit`); a master that will not close is
noted, and expires on its own after `ControlPersist` idle seconds. If the control dir
cannot be set up or cannot hold a socket, the source notes it ONCE and falls back to
plain non-multiplexed probes for the rest of the run (#1228) — never a failed probe.

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
import stat
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from mqlab.inventory import INSECURE_KEY, SSH_COMMON_ARGS, SSH_USER

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from mqlab.perf import PerfRecord

_VIRSH = ["virsh", "-c", "qemu:///system"]
# The aggregate `cpu` line is always first in /proc/stat; /proc/loadavg rides the SAME ssh
# round trip (#1215: one call per guest per tick, never more).
_GUEST_PROBE_CMD = "head -n1 /proc/stat && cat /proc/loadavg"
_LOCAL_PROC_STAT = Path("/proc/stat")
# Unattended probes must never prompt or hang on connect: fail fast into a note instead.
_SSH_PROBE_ARGS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "LogLevel=ERROR")
# Connection multiplexing (#1221): one master (one login, one MOTD run) per guest per run.
# - ControlMaster=auto: the first probe that authenticates becomes the master; later ones
#   reuse it. A first connect that FAILS creates no socket (ssh binds it only after auth),
#   so a guest that is not up yet just fails fast into a note and is retried next tick.
#   A socket whose master died is refused on connect, and `auto` removes it and becomes
#   the new master — a stale socket never blocks later ticks.
# - ControlPath=%C is RELATIVE (40 hex chars): the probe runs with cwd=ssh_mux_dir(), so
#   the directory's depth never counts against the Unix socket-path limit.
# - ControlPersist: the master backgrounds itself (stdio on /dev/null, so it does not
#   hold the probe's captured pipes open) and outlives each tick; one left behind (stop
#   could not close it) exits after this many idle seconds, well above the tick interval.
# - ServerAlive*: a master whose guest went away (rebooted/halted mid-run) dies in ~10s
#   instead of stalling every later tick's probe until its timeout.
_CONTROL_PATH = "%C"
CONTROL_PERSIST_S = 60
_SSH_MUX_ARGS = (
    "-o",
    "ControlMaster=auto",
    "-o",
    f"ControlPath={_CONTROL_PATH}",
    "-o",
    f"ControlPersist={CONTROL_PERSIST_S}",
    "-o",
    "ServerAliveInterval=5",
    "-o",
    "ServerAliveCountMax=2",
)
# The fallback when the mux cannot be set up (#1228): a plain one-shot connection that
# neither creates nor looks for a control socket.
_SSH_NO_MUX_ARGS = ("-o", "ControlMaster=no", "-o", "ControlPath=none")
_SSH_MUX_DIRNAME = "mqlab-ssh-mux"
# ssh exits 255 for its own errors (connect, auth AND mux setup); these stderr markers are
# the ones that mean "the control socket could not be created/bound", e.g. on virtiofs:
# `muxserver_listen: link mux listener ...: Bad file descriptor` (#1228).
_MUX_FAILURE_MARKERS = (
    "muxserver_listen",
    "mux_client",
    "ControlSocket",
    "ontrol socket",
    "unix_listener",
)
# /proc/stat cpu columns: user nice system idle iowait irq softirq steal guest guest_nice.
# guest/guest_nice are already included in user/nice, so the total is the first eight.
# Busy = user + nice + system + irq + softirq (#1215); idle, iowait and steal are not busy.
_STEAL_COL = 7
_IOWAIT_COL = 4
_BUSY_COLS = (0, 1, 2, 5, 6)
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
    """One guest reading. The /proc/stat-delta percentages (steal, busy, iowait) are None
    for a baseline reading (no delta yet); the load averages are point-in-time readings
    of /proc/loadavg (None only from a source that does not read it)."""

    steal_pct: float | None
    busy_pct: float | None = None
    iowait_pct: float | None = None
    load1: float | None = None
    load5: float | None = None
    load15: float | None = None


@dataclass(frozen=True)
class LocalSample:
    """The sampling (Vergil) VM's own CPU reading from its local /proc/stat (#1215):
    steal/busy/iowait %, all None for the baseline reading (no delta yet)."""

    steal_pct: float | None
    busy_pct: float | None
    iowait_pct: float | None


class SampleSource(Protocol):
    """Where the sampler gets its readings. Each call may raise; the Sampler notes it."""

    def host(self) -> HostSample: ...

    def local(self) -> LocalSample: ...

    def guest(self, name: str) -> GuestSample: ...

    # Messages queued by the source itself since the last call (#1228: e.g. "ssh
    # multiplexing unavailable, falling back"); the Sampler drains and notes each per tick.
    def notes(self) -> list[str]: ...

    # Release what the source holds open (#1221: ssh masters). Returns one message per
    # resource that did not release — the Sampler notes each; nothing is raised.
    def close(self) -> list[str]: ...


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
        local: LocalSample | None = None
        try:
            local = self._source.local()
        except Exception as exc:  # noqa: BLE001 - recorded as a note; sampling continues
            self._failed("vergil-vm", exc)
        else:
            self._succeeded("vergil-vm")
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
        self._drain_source_notes()
        self._record.add_sample(
            t,
            host_cpu=host.cpu_pct if host else None,
            host_iowait=host.iowait_pct if host else None,
            guests=guests,
            host_cpus=host.cpus if host else None,
            local=local,
        )

    def _drain_source_notes(self) -> None:
        try:
            messages = self._source.notes()
        except Exception as exc:  # noqa: BLE001 - recorded as a note; sampling continues
            self._record.note(f"sampler: source notes unavailable ({type(exc).__name__}: {exc})")
            return
        for message in messages:
            self._record.note(f"sampler: {message}")

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
        # Release the source's connections (#1221). Non-fatal, never silent.
        try:
            problems = self._source.close()
        except Exception as exc:  # noqa: BLE001 - recorded as a note; stop must not raise
            self._record.note(f"sampler: source cleanup failed ({type(exc).__name__}: {exc})")
        else:
            for problem in problems:
                self._record.note(f"sampler: {problem}")


# --- the real (lab) source --------------------------------------------------------------


@dataclass(frozen=True)
class CpuCounters:
    """The aggregate /proc/stat `cpu` counters the CPU deltas need (in USER_HZ ticks)."""

    steal: int
    total: int
    busy: int = 0
    iowait: int = 0


@dataclass(frozen=True)
class CpuPcts:
    """Steal / busy / iowait as % of all CPU ticks elapsed between two readings."""

    steal: float
    busy: float
    iowait: float


@dataclass(frozen=True)
class LoadAvg:
    """/proc/loadavg's 1/5/15-minute load averages."""

    load1: float
    load5: float
    load15: float


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
    """The aggregate `cpu` line of /proc/stat -> steal/busy/iowait/total ticks. Raises if
    malformed."""
    parts = text.split()
    if len(parts) < _TOTAL_COLS + 1 or parts[0] != "cpu":
        raise ValueError(f"not an aggregate /proc/stat cpu line: {text!r}")
    values = [int(v) for v in parts[1 : _TOTAL_COLS + 1]]
    return CpuCounters(
        steal=values[_STEAL_COL],
        total=sum(values),
        busy=sum(values[i] for i in _BUSY_COLS),
        iowait=values[_IOWAIT_COL],
    )


def parse_loadavg(text: str) -> LoadAvg:
    """A /proc/loadavg line (`0.52 0.58 0.59 1/467 12345`) -> 1/5/15-minute load averages.
    Raises if malformed."""
    parts = text.split()
    try:
        load1, load5, load15 = (float(v) for v in parts[:3])
    except ValueError as exc:  # too few fields (unpack) or a non-numeric one
        raise ValueError(f"not a /proc/loadavg line: {text!r}") from exc
    return LoadAvg(load1, load5, load15)


def parse_guest_probe(text: str) -> tuple[CpuCounters, LoadAvg]:
    """The guest probe's output (`_GUEST_PROBE_CMD`): the /proc/stat aggregate line, then
    the /proc/loadavg line. Raises if either is missing or malformed."""
    lines = text.splitlines()
    if len(lines) < 2:  # noqa: PLR2004 - exactly the probe's two lines
        raise ValueError(f"guest probe output lacks the /proc/stat + /proc/loadavg lines: {text!r}")
    return parse_proc_stat_cpu(lines[0]), parse_loadavg(lines[1])


def cpu_pcts(prev: CpuCounters, cur: CpuCounters) -> CpuPcts | None:
    """Steal/busy/iowait % over the interval between two readings; None when there is no
    valid delta (no ticks elapsed, or a counter went backwards because the machine
    rebooted)."""
    d_total = cur.total - prev.total
    d_steal = cur.steal - prev.steal
    d_busy = cur.busy - prev.busy
    d_iowait = cur.iowait - prev.iowait
    if d_total <= 0 or min(d_steal, d_busy, d_iowait) < 0:
        return None
    return CpuPcts(
        steal=d_steal / d_total * 100,
        busy=d_busy / d_total * 100,
        iowait=d_iowait / d_total * 100,
    )


def read_local_proc_stat() -> str:
    """The sampling VM's own /proc/stat aggregate line (a local file read — no ssh)."""
    with _LOCAL_PROC_STAT.open(encoding="ascii") as fh:
        return fh.readline()


def ssh_mux_dir() -> Path:
    """Where the guest ssh control sockets live: `$XDG_RUNTIME_DIR/mqlab-ssh-mux` when set,
    else `<system temp dir>/mqlab-ssh-mux-<uid>`.

    A DOCUMENTED EXCEPTION to "all working state under build/" (docs/development/
    build-layout.md, #1228): control sockets are runtime IPC endpoints, not build
    artifacts, and must sit on a local filesystem that supports Unix sockets — on the macOS
    dev VM `build/` is a virtiofs mount where ssh cannot bind one (every probe exited 255).
    Ansible does the same with `~/.ansible/cp`. The probe runs FROM here with a relative
    `ControlPath`, so this directory's own length is not bound by the socket-path limit."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / _SSH_MUX_DIRNAME
    return Path(tempfile.gettempdir()) / f"{_SSH_MUX_DIRNAME}-{os.getuid()}"


def ensure_private_dir(path: Path) -> None:
    """Create `path` 0700 if missing; FAIL-LOUD unless it is then a real directory owned by
    this user (a predictable name in a shared temp dir must not be someone else's, or a
    symlink to one). A group/other-accessible mode on our own dir is tightened to 0700."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode):
        raise RuntimeError(f"ssh control dir {path} is not a directory")
    if st.st_uid != os.getuid():
        raise RuntimeError(f"ssh control dir {path} is owned by uid {st.st_uid}, not us")
    if st.st_mode & 0o077:
        path.chmod(0o700)


class ProbeError(RuntimeError):
    """A probe command exited non-zero; keeps the exit code and stderr for callers that
    must tell one ssh failure from another (#1228: mux setup vs. guest unreachable)."""

    def __init__(self, argv0: str, returncode: int, stderr: str, detail: str) -> None:
        super().__init__(f"{argv0} exited {returncode}: {detail}")
        self.returncode = returncode
        self.stderr = stderr


_SSH_OWN_ERROR_RC = 255


def is_mux_failure(exc: BaseException) -> bool:
    """True when an ssh probe failed because its control socket could not be set up."""
    return (
        isinstance(exc, ProbeError)
        and exc.returncode == _SSH_OWN_ERROR_RC
        and any(marker in exc.stderr for marker in _MUX_FAILURE_MARKERS)
    )


def run_bounded(argv: list[str], timeout: float, *, cwd: Path | None = None) -> str:
    """Run a probe command (from `cwd`, if given) bounded by `timeout`; return stdout.
    FAIL-LOUD: a timeout or non-zero exit raises with the tool's own message (the Sampler
    turns it into a note); a non-zero exit raises `ProbeError`."""
    try:
        cp = subprocess.run(  # noqa: S603 - fixed internal argv; tools on PATH (lab)
            argv, capture_output=True, text=True, timeout=timeout, check=False, cwd=cwd
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{argv[0]} timed out after {timeout}s") from exc
    if cp.returncode != 0:
        detail = cp.stderr.strip() or cp.stdout.strip()
        raise ProbeError(argv[0], cp.returncode, cp.stderr, detail)
    return cp.stdout


class RealSource:
    """The lab SampleSource: host via virsh, the Vergil VM itself via its local
    /proc/stat, and guests via ONE ssh call each (/proc/stat deltas + /proc/loadavg).

    Guests are reached at their net-mgmt address with the same login, key and host-key
    options the Ansible inventory uses (`mqlab.inventory`). The first /proc/stat reading
    of each guest (and of the local VM) is a baseline (percentages None); every later one
    is the delta since the last.

    Guest probes multiplex over one ssh master per guest (#1221, see `_SSH_MUX_ARGS`),
    with control sockets in `mux_dir` (default `ssh_mux_dir()`); `close()` exits every
    master a probe may have opened. If the mux cannot be set up — the dir cannot be made
    private, or ssh cannot bind a socket in it — the source queues ONE note (drained by
    the Sampler through `notes()`) and probes without multiplexing for the rest of the run.
    """

    def __init__(
        self,
        addresses: Mapping[str, str],
        *,
        run: Callable[..., str] = run_bounded,
        read_local: Callable[[], str] = read_local_proc_stat,
        timeout: float = 10.0,
        mux_dir: Path | None = None,
    ) -> None:
        self.addresses = dict(addresses)
        self._run = run
        self._read_local = read_local
        self._timeout = timeout
        self._mux_dir = mux_dir if mux_dir is not None else ssh_mux_dir()
        self._cpus: int | None = None
        self._prev: dict[str, CpuCounters] = {}
        self._prev_local: CpuCounters | None = None
        # Guests whose probe authenticated at least once — each has (or had) a master.
        self._masters: set[str] = set()
        # Mux health (#1228): flipped off once, for the run, on the first setup failure.
        # Guest probes run concurrently, so the flip + its note are guarded.
        self._mux_ok = True
        self._mux_lock = threading.Lock()
        self._notes: list[str] = []

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

    def local(self) -> LocalSample:
        cur = parse_proc_stat_cpu(self._read_local())
        prev, self._prev_local = self._prev_local, cur
        pcts = None if prev is None else cpu_pcts(prev, cur)
        if pcts is None:
            return LocalSample(steal_pct=None, busy_pct=None, iowait_pct=None)
        return LocalSample(steal_pct=pcts.steal, busy_pct=pcts.busy, iowait_pct=pcts.iowait)

    def notes(self) -> list[str]:
        """Drain the messages queued since the last call (the Sampler records each)."""
        with self._mux_lock:
            out, self._notes = self._notes, []
        return out

    def _disable_mux(self, reason: str) -> None:
        with self._mux_lock:
            if not self._mux_ok:
                return
            self._mux_ok = False
            self._notes.append(
                f"ssh multiplexing unavailable ({reason}); guest probes fall back to plain "
                "non-multiplexed ssh (one login per probe) for the rest of the run"
            )

    def _ssh(self, *tail: str, mux: bool = True) -> list[str]:
        """The guest ssh argv: the inventory's login/key/host-key options, the unattended
        probe options, the multiplexing (or, `mux=False`, no-multiplexing) options, then
        `tail` (destination [+ command])."""
        return [
            "ssh",
            *SSH_COMMON_ARGS,
            *_SSH_PROBE_ARGS,
            *(_SSH_MUX_ARGS if mux else _SSH_NO_MUX_ARGS),
            "-i",
            os.path.expanduser(INSECURE_KEY),  # noqa: PTH111 - ssh argv wants a str path
            *tail,
        ]

    def guest(self, name: str) -> GuestSample:
        addr = self.addresses.get(name)
        if addr is None:
            raise LookupError(f"no net-mgmt address for guest {name!r} in topology")
        out = self._probe(name, f"{SSH_USER}@{addr}")
        cur, load = parse_guest_probe(out)
        prev = self._prev.get(name)
        self._prev[name] = cur
        pcts = None if prev is None else cpu_pcts(prev, cur)
        return GuestSample(
            steal_pct=pcts.steal if pcts else None,
            busy_pct=pcts.busy if pcts else None,
            iowait_pct=pcts.iowait if pcts else None,
            load1=load.load1,
            load5=load.load5,
            load15=load.load15,
        )

    def _probe(self, name: str, dest: str) -> str:
        """One guest probe: multiplexed while the mux works; on a mux setup failure, note it
        once and re-run THIS probe (and every later one) without multiplexing."""
        if self._mux_ok:
            try:
                # Re-ensured every probe (cheap), so a temp-dir cleanup mid-run self-heals.
                ensure_private_dir(self._mux_dir)
            except (OSError, RuntimeError) as exc:
                self._disable_mux(f"control dir {self._mux_dir}: {type(exc).__name__}: {exc}")
            else:
                try:
                    out = self._run(
                        self._ssh(dest, _GUEST_PROBE_CMD), self._timeout, cwd=self._mux_dir
                    )
                except ProbeError as exc:
                    if not is_mux_failure(exc):
                        raise
                    self._disable_mux(f"control socket in {self._mux_dir}: {exc}")
                else:
                    self._masters.add(name)
                    return out
        return self._run(self._ssh(dest, _GUEST_PROBE_CMD, mux=False), self._timeout)

    def _close_master(self, name: str) -> str | None:
        argv = self._ssh("-O", "exit", f"{SSH_USER}@{self.addresses[name]}")
        try:
            self._run(argv, self._timeout, cwd=self._mux_dir)
        except Exception as exc:  # noqa: BLE001 - returned as a note by close(), never lost
            return (
                f"guest {name}: ssh master not closed ({type(exc).__name__}: {exc}); "
                f"it exits on its own after {CONTROL_PERSIST_S}s idle (ControlPersist)"
            )
        return None

    def close(self) -> list[str]:
        """`ssh -O exit` every master a successful probe opened (concurrently, each bounded
        by the probe timeout). Returns one message per master that did not close — e.g. it
        already died with its guest — and never raises. A master opened by a probe that
        then timed out is not tracked; ControlPersist retires it after its idle period."""
        names = sorted(self._masters)
        self._masters.clear()
        if not names:
            return []
        with ThreadPoolExecutor(max_workers=min(len(names), _MAX_PROBE_WORKERS)) as pool:
            results = list(pool.map(self._close_master, names))
        return [r for r in results if r is not None]
