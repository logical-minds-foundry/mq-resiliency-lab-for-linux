"""Merge one scenario's recordings into a single UTC timeline (#1666).

Usage: python3 timeline.py <scenario-dir> <T0> <T1> [--year YYYY]

Reads, per node directory under <scenario-dir>:
  journal.txt          journalctl -o short-iso-precise (TZ=UTC)
  events2.log          drbdsetup events2 --timestamps
  files/var/log/pacemaker/pacemaker.log
  files/**/AMQERR0*.LOG
  poll.log             5 s poller, reduced here to a one-line digest emitted on change
and, at the scenario level, actions.log (operator actions) and snaps.log (snapshots).

Writes <scenario-dir>/timeline.txt, sorted by UTC, each line
`<UTC> <node> <source> <text>`, and, per node, pacemaker-window.log: pacemaker.log cut
to [T0, T1]. Every line that cannot be given a time is counted and reported, never
silently dropped from the count.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

ISO = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)(Z|[+-]\d\d:?\d\d)?\s?(.*)$")
PCMK = re.compile(r"^([A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d(?:\.\d+)?)\s+(.*)$")
AMQ_TIME = re.compile(r"Time\((\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z)\)")
AMQ_HEAD = re.compile(r"^(\d\d/\d\d/\d{4} \d\d:\d\d:\d\d [AP]M) - (.*)$")
AMQ_MSG = re.compile(r"^(AMQ\d{4}[IWEST]): (.*)$")


def parse_iso(stamp: str, zone: str | None) -> datetime:
    """An ISO stamp with an optional zone; no zone means UTC (every source is UTC)."""
    if zone in (None, "Z"):
        zone = "+00:00"
    if len(zone) == 5:  # +0000
        zone = zone[:3] + ":" + zone[3:]
    frac = ""
    if "." in stamp:
        stamp, frac = stamp.split(".", 1)
        frac = "." + frac[:6].ljust(6, "0")
    return datetime.fromisoformat(stamp + frac + zone).astimezone(UTC)


def fmt(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Merger:
    def __init__(self, t0: datetime, t1: datetime) -> None:
        self.t0, self.t1 = t0, t1
        self.rows: list[tuple[datetime, int, str]] = []
        self.untimed: dict[str, int] = {}
        self.seq = 0

    def add(self, t: datetime, node: str, source: str, text: str) -> None:
        if self.t0 <= t <= self.t1:
            self.seq += 1
            self.rows.append((t, self.seq, f"{fmt(t)} {node} {source} {text.rstrip()}"))

    def miss(self, key: str) -> None:
        self.untimed[key] = self.untimed.get(key, 0) + 1


def iso_lines(path: Path, m: Merger, node: str, source: str) -> None:
    """Sources whose every entry starts with an ISO stamp; other lines continue the last."""
    last: datetime | None = None
    for raw in path.read_text(errors="replace").splitlines():
        if raw.startswith("#"):  # export headers and events2 recorder-start markers
            continue
        hit = ISO.match(raw)
        if hit:
            last = parse_iso(hit.group(1), hit.group(2))
            m.add(last, node, source, hit.group(3))
        elif raw.strip() and last is not None:
            m.add(last, node, source, "  " + raw.strip())
        elif raw.strip():
            m.miss(f"{node}/{source}")


def pacemaker(path: Path, m: Merger, node: str, year: int, window: Path) -> None:
    """pacemaker.log stamps carry no year and no zone; the nodes run UTC (tz.txt)."""
    kept: list[str] = []
    last: datetime | None = None
    for raw in path.read_text(errors="replace").splitlines():
        hit = PCMK.match(raw)
        if hit:
            stamp = hit.group(1)
            frac = ""
            if "." in stamp:
                stamp, frac = stamp.split(".", 1)
            last = datetime.strptime(f"{year} {stamp}", "%Y %b %d %H:%M:%S").replace(tzinfo=UTC)
            if frac:
                last = last.replace(microsecond=int(frac[:6].ljust(6, "0")))
            m.add(last, node, "pacemaker.log", hit.group(2))
        elif last is not None and raw.strip():
            m.add(last, node, "pacemaker.log", "  " + raw.strip())
        elif raw.strip():
            m.miss(f"{node}/pacemaker.log")
            continue
        if last is not None and m.t0 <= last <= m.t1:
            kept.append(raw)
    window.write_text("\n".join(kept) + ("\n" if kept else ""))


def amqerr(path: Path, m: Merger, node: str, source: str) -> None:
    """One timeline line per AMQERR entry: its Time() stamp plus its AMQ message line."""
    entry_time: datetime | None = None
    head = ""
    for raw in path.read_text(errors="replace").splitlines():
        if AMQ_HEAD.match(raw):
            entry_time, head = None, AMQ_HEAD.match(raw).group(2)  # type: ignore[union-attr]
            continue
        hit = AMQ_TIME.search(raw)
        if hit:
            entry_time = parse_iso(hit.group(1)[:-1], "Z")
            continue
        msg = AMQ_MSG.match(raw)
        if msg:
            if entry_time is None:
                m.miss(f"{node}/{source}")
                continue
            m.add(entry_time, node, source, f"{msg.group(1)}: {msg.group(2)} [{head}]")


RDQM_LINE = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d\.\d+):\s+(\d+ .*)$")
# rdqmd -s polls `crm status bynode` every ~1.5 s at boot; its output is not timeline material.
RDQM_SKIP = re.compile(r"crm status bynode|^\d+ >>STDOUT:|^\d+ >>STDERR:")


def rdqmlog(path: Path, m: Merger, node: str) -> None:
    """RDQM.0.LOG: one line per command start (`---{`), end (`---} rc=`) or process banner."""
    for raw in path.read_text(errors="replace").splitlines():
        hit = RDQM_LINE.match(raw)
        if not hit or RDQM_SKIP.search(hit.group(3)):
            continue
        m.add(parse_iso(f"{hit.group(1)}T{hit.group(2)}", "Z"), node, "RDQM.0.LOG", hit.group(3))


DIGEST_KEYS = (
    "Queue manager status",
    "HA role",
    "HA status",
    "HA current location",
    "DR role",
    "DR status",
)


def poll_blocks(path: Path) -> Iterator[tuple[str, list[str]]]:
    current, lines = "", []
    for raw in path.read_text(errors="replace").splitlines():
        stamp, _, rest = raw.partition(" ")
        if stamp != current and lines:
            yield current, lines
            lines = []
        current = stamp
        lines.append(rest)
    if lines:
        yield current, lines


def digest(lines: list[str]) -> str:
    if lines and lines[0].startswith("POLL-FAILED"):
        return "unreachable"
    fields: dict[str, str] = {}
    drbd: list[str] = []
    crm: list[str] = []
    part = "rdqmstatus"
    for line in lines:
        if line.startswith("--- crm_mon"):
            part = "crm"
            continue
        if line.startswith("--- drbdsetup"):
            part = "drbd"
            continue
        if part == "rdqmstatus" and ":" in line:
            key, _, val = line.partition(":")
            if key.strip() in DIGEST_KEYS:
                fields.setdefault(key.strip(), val.strip())
        elif part == "drbd":
            res = re.match(r"^(\S+) node-id:\d+ role:(\S+)", line)
            vol = re.match(r"^\s+volume:\d+ minor:\d+ disk:(\S+)", line)
            peer = re.match(r"^\s+(\S+) node-id:\d+ connection:(\S+) role:(\S+)", line)
            pvol = re.match(r"^\s+volume:\d+ replication:(\S+) peer-disk:(\S+)", line)
            if res:
                drbd.append(f"{res.group(1)}:{res.group(2)}")
            elif peer:
                drbd.append(f"[{peer.group(1)} {peer.group(2)} {peer.group(3)}")
            elif pvol:
                drbd.append(f"{pvol.group(1)} {pvol.group(2)}]")
            elif vol:
                drbd.append(vol.group(1))
        elif part == "crm":
            hit = re.match(r"^\s+\* (p_\S+|rdqmapp)\s+\(\S+\):\s+(.*)$", line)
            if hit:
                crm.append(f"{hit.group(1)}={hit.group(2).strip()}")
            hit = re.match(r"^\s+\* (Promoted|Unpromoted|Stopped|Started): \[(.*)\]$", line)
            if hit:
                crm.append(f"{hit.group(1)}[{hit.group(2).strip()}]")
    short = {
        "Queue manager status": "qm",
        "HA role": "ha-role",
        "HA status": "ha-status",
        "HA current location": "ha-loc",
        "DR role": "dr-role",
        "DR status": "dr-status",
    }
    rdqm = " ".join(f"{short[k]}={v}" for k, v in fields.items())
    return f"{rdqm} | drbd {' '.join(drbd)} | crm {' '.join(crm)}"


def poll(path: Path, m: Merger, node: str) -> None:
    previous = None
    for stamp, lines in poll_blocks(path):
        hit = ISO.match(stamp)
        if not hit:
            m.miss(f"{node}/poll")
            continue
        now = digest(lines)
        if now != previous:
            m.add(parse_iso(hit.group(1), hit.group(2)), node, "poll-digest", now)
            previous = now


def main(argv: list[str]) -> int:
    scen = Path(argv[1])
    t0 = parse_iso(argv[2].rstrip("Z"), "Z")
    t1 = parse_iso(argv[3].rstrip("Z"), "Z")
    year = int(argv[5]) if len(argv) > 5 and argv[4] == "--year" else t0.year
    m = Merger(t0, t1)
    for name in ("actions.log", "snaps.log"):
        if (scen / name).exists():
            iso_lines(scen / name, m, "host", name.removesuffix(".log"))
    for node_dir in sorted(p for p in scen.iterdir() if p.is_dir() and p.name.startswith("rdqm-")):
        node = node_dir.name
        for name, source in (("journal.txt", "journal"), ("events2.log", "events2")):
            if (node_dir / name).exists():
                iso_lines(node_dir / name, m, node, source)
        # Raw layout (files/...) or the exported one (pacemaker.log, amqerr/..., RDQM.0.LOG).
        pl = node_dir / "files/var/log/pacemaker/pacemaker.log"
        if pl.exists():
            pacemaker(pl, m, node, year, node_dir / "pacemaker-window.log")
        elif (node_dir / "pacemaker.log").exists():
            pacemaker(node_dir / "pacemaker.log", m, node, year, Path("/dev/null"))
        logroot = node_dir / "files" if (node_dir / "files").exists() else node_dir / "amqerr"
        for log in sorted(logroot.rglob("AMQERR0*.LOG")):
            amqerr(log, m, node, "amqerr:" + str(log.relative_to(logroot)))
        rl = node_dir / "files/var/mqm/errors/RDQM.0.LOG"
        rl = rl if rl.exists() else node_dir / "RDQM.0.LOG"
        if rl.exists():
            rdqmlog(rl, m, node)
        if (node_dir / "poll.log").exists():
            poll(node_dir / "poll.log", m, node)
    m.rows.sort()
    out = scen / "timeline.txt"
    header = [
        f"# timeline {scen.name}: {fmt(t0)} .. {fmt(t1)}, {len(m.rows)} lines, all UTC",
        "# fields: <UTC> <node> <source> <text>; journal lines are the full journal of the node,",
        "# poll-digest lines are one-line digests of the 5 s poller, emitted only on change.",
    ]
    if m.untimed:
        header.append(f"# lines with no time (not placed): {m.untimed}")
    out.write_text("\n".join(header + [r[2] for r in m.rows]) + "\n")
    print(f"{out}: {len(m.rows)} lines; untimed {m.untimed or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
