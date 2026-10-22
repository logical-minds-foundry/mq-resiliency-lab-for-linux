"""Turn one scenario's raw recordings into the committed form (#1666).

Usage: python3 export.py <raw-scenario-dir> <dest-dir> [<extra-file> ...]

The raw recordings stay in the temp bucket; the committed copy is:

* window-trimmed: every timed source is cut to the scenario's [T0, T1] (`<raw>/window`);
* de-noised, with every dropped line COUNTED in a header at the top of the file, never
  silently lost. Dropped: the recording kit's own SSH/sudo/session lines, the lab's
  observability agents and timers (alloy, mq-events, node_exporter, lab-rdqm-state,
  lab-net-reach), and the 5 s poller's repeats (poll.log keeps a block only when it
  differs from the previous block of that node, ignoring the volatile CPU/Memory/
  "Last updated" lines). The `ibm-mq[...]` journal lines (MQ's JSON copy of its own
  error log, ~1 KB each) are compacted to `ibm-mq[pid]: <ibm_messageId> <message>`; the
  AMQERR*.LOG files, trimmed to the window, carry the full entries;
* redacted: DRBD `shared-secret` values, and any PRIVATE KEY / CERTIFICATE block, are
  replaced with markers. The IBM copyright comment block of `systemctl cat` output is
  replaced with a marker (the unit's directives are kept as data);
* guarded: the export fails if `PRIVATE KEY` or an unredacted `shared-secret` survives.

Files copied as-is (already window-scoped): actions.log, snaps.log, clocks-*.txt, window,
journal-prep.log, amqsbcg-*.txt, failback.log, s0-extra.txt, cib-writers.txt,
changed-files.txt, journal-boots.txt, fdc-headers.txt, and every snap/ file.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

NOISE = {
    "kit ssh/sudo/session": re.compile(
        r"sshd-session\[|sshd\[|sudo\[\d+\]:|pam_unix\(|systemd-logind\[|Received client request to sync journal"
        r"|session-\d+\.scope|user@1000|user-runtime-dir@1000|\(sd-pam\)|Created slice User|Removed slice User"
    ),
    "lab observability agents and timers": re.compile(
        r" (alloy|mq-events|node_exporter)\[\d+\]:|lab-rdqm-state\.service|lab-net-reach\.service"
    ),
    "lab monitor authority warnings (AMQ8245W/AMQ8009W)": re.compile(r'"ibm_messageId":"AMQ(8245W|8009W)"'),
}
# The lab's `mqmon` monitor user lacks display authority on this QM, so the QM logs
# AMQ8245W / AMQ8009W every few seconds; whole AMQERR entries carrying them are dropped
# (and counted), as lab observability noise unrelated to RDQM.
AMQ_NOISE = re.compile(r"^AMQ(8245W|8009W):")
ISO = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)(Z|[+-]\d\d:?\d\d)?")
PCMK = re.compile(r"^([A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d)")
RDQMLOG = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+):")
AMQ_TIME = re.compile(r"Time\((\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z)\)")
AMQ_HEAD = re.compile(r"^\d\d/\d\d/\d{2,4} \d\d:\d\d:\d\d")
IBM_JSON = re.compile(r"^(\S+ \S+ ibm-mq\[\d+\]: )(\{.*)$")
SECRET = re.compile(r"(shared-secret\s+)(\"[^\"]*\"|[^;\s]+)")
PEM = re.compile(r"-----BEGIN ([A-Z0-9 ]*(PRIVATE KEY|CERTIFICATE))-----.*?-----END \1-----", re.S)
COPYRIGHT = re.compile(r"(# <copyright.*?# </copyright>\n)", re.S)
VOLATILE = re.compile(r"(CPU:|Memory:|Last updated:|Queue manager file system:)")
COPY_AS_IS = (
    "actions.log",
    "snaps.log",
    "window",
    "journal-prep.log",
    "failback.log",
)
NODE_AS_IS = (
    "cib-writers.txt",
    "changed-files.txt",
    "journal-boots.txt",
    "fdc-headers.txt",
    "s0-extra.txt",
    "transient-attr-origin.txt",
    "cibattrs-baseline.txt",
)


def utc(s: str, zone: str | None = None) -> datetime:
    zone = "+00:00" if zone in (None, "Z") else zone
    if len(zone) == 5:
        zone = zone[:3] + ":" + zone[3:]
    frac = ""
    if "." in s:
        s, frac = s.split(".", 1)
        frac = "." + frac[:6].ljust(6, "0")
    return datetime.fromisoformat(s.replace(" ", "T") + frac + zone).astimezone(UTC)


def redact(text: str) -> str:
    text = SECRET.sub(r'\1"REDACTED"', text)
    text = PEM.sub(lambda m: f"[{m.group(1).lower()} block redacted at export (#1666)]", text)
    return COPYRIGHT.sub("# [IBM copyright comment block omitted at export (#1666)]\n", text)


def header(name: str, kept: int, dropped: dict[str, int], note: str = "") -> str:
    parts = ", ".join(f"{k}: {v}" for k, v in dropped.items() if v) or "none"
    line = f"# {name}: exported for #1666; {kept} lines kept; dropped lines by category: {parts}."
    return line + (f" {note}" if note else "") + "\n"


def compact_ibm(line: str) -> str:
    hit = IBM_JSON.match(line)
    if not hit:
        return line
    try:
        rec = json.loads(hit.group(2))
    except json.JSONDecodeError:
        return line  # a truncated or multi-line record stays verbatim
    msg_id, msg = rec.get("ibm_messageId", "?"), rec.get("message", "").strip()
    return f"{hit.group(1)}{msg if msg.startswith(msg_id) else f'{msg_id} {msg}'}"


def journal(src: Path, dst: Path, t0: datetime, t1: datetime) -> None:
    kept: list[str] = []
    dropped = dict.fromkeys(NOISE, 0) | {"outside window": 0}
    last_in = True
    for raw in src.read_text(errors="replace").splitlines():
        hit = ISO.match(raw)
        if hit:
            last_in = t0 <= utc(hit.group(1), hit.group(2)) <= t1
        if not last_in:
            dropped["outside window"] += 1
            continue
        cat = next((k for k, rx in NOISE.items() if rx.search(raw)), None)
        if cat:
            dropped[cat] += 1
            continue
        kept.append(compact_ibm(raw))
    note = "ibm-mq JSON lines compacted to '<ibm_messageId> <message>'."
    dst.write_text(header(src.name, len(kept), dropped, note) + redact("\n".join(kept)) + "\n")


def timed(src: Path, dst: Path, t0: datetime, t1: datetime, kind: str, year: int) -> None:
    """events2 (ISO), pacemaker.log (syslog-style, UTC), RDQM.0.LOG ('YYYY-MM-DD hh:mm:ss.fff:')."""
    kept: list[str] = []
    out = 0
    last_in = True
    for raw in src.read_text(errors="replace").splitlines():
        stamp = None
        if kind == "iso" and (hit := ISO.match(raw)):
            stamp = utc(hit.group(1), hit.group(2))
        elif kind == "pcmk" and (hit := PCMK.match(raw)):
            stamp = datetime.strptime(f"{year} {hit.group(1)}", "%Y %b %d %H:%M:%S").replace(tzinfo=UTC)
        elif kind == "rdqm" and (hit := RDQMLOG.match(raw)):
            stamp = utc(hit.group(1))
        if stamp is not None:
            last_in = t0 <= stamp <= t1
        if raw.startswith("# events2 recorder start"):
            last_in = True
        if last_in:
            kept.append(raw)
        else:
            out += 1
    dst.write_text(header(src.name, len(kept), {"outside window": out}) + redact("\n".join(kept)) + "\n")


def amqerr(src: Path, dst: Path, t0: datetime, t1: datetime) -> None:
    """AMQERR entries are blocks starting with a 'MM/DD/YY hh:mm:ss' header line."""
    blocks: list[list[str]] = [[]]
    for raw in src.read_text(errors="replace").splitlines():
        if AMQ_HEAD.match(raw):
            blocks.append([])
        blocks[-1].append(raw)
    kept, out, noise = [], 0, 0
    for b in blocks:
        stamp = next((AMQ_TIME.search(x) for x in b if AMQ_TIME.search(x)), None)
        if not (stamp and t0 <= utc(stamp.group(1)[:-1]) <= t1):
            out += len(b)
        elif any(AMQ_NOISE.match(x) for x in b):
            noise += len(b)
        else:
            kept += b
    dst.parent.mkdir(parents=True, exist_ok=True)
    dropped = {"outside window (whole entries)": out, "lab monitor authority warnings (AMQ8245W/AMQ8009W)": noise}
    dst.write_text(header(src.name, len(kept), dropped) + "\n".join(kept) + "\n")


def poll(src: Path, dst: Path) -> None:
    kept: list[str] = []
    repeats = 0
    prev: list[str] | None = None
    block: list[str] = []
    stamp = None

    def flush() -> None:
        nonlocal prev, repeats
        if not block:
            return
        key = [x.split(" ", 1)[1] if " " in x else "" for x in block]
        key = [k for k in key if not VOLATILE.search(k)]
        if key == prev:
            repeats += len(block)
        else:
            kept.extend(block)
            prev = key

    for raw in src.read_text(errors="replace").splitlines():
        s = raw.split(" ", 1)[0]
        if s != stamp:
            flush()
            block, stamp = [], s
        block.append(raw)
    flush()
    note = "A block is kept only when it differs from the node's previous block."
    dst.write_text(header(src.name, len(kept), {"unchanged 5 s repeats": repeats}, note) + "\n".join(kept) + "\n")


def guard(dest: Path) -> None:
    bad = []
    for f in dest.rglob("*"):
        if f.is_file():
            text = f.read_text(errors="replace")
            if "PRIVATE KEY" in text:
                bad.append(f"{f}: PRIVATE KEY")
            for m in SECRET.finditer(text):
                if m.group(2) != '"REDACTED"':
                    bad.append(f"{f}: shared-secret {m.group(2)[:4]}…")
    if bad:
        sys.exit("export guard FAILED:\n" + "\n".join(bad))


def main(argv: list[str]) -> int:
    raw, dest = Path(argv[1]), Path(argv[2])
    w0, w1 = (raw / "window").read_text().split()
    t0, t1 = utc(w0), utc(w1)
    dest.mkdir(parents=True, exist_ok=True)
    for f in [*COPY_AS_IS, *(p.name for p in raw.glob("clocks-*.txt")), *(p.name for p in raw.glob("amqsbcg-*.txt"))]:
        if (raw / f).exists():
            (dest / f).write_text(redact((raw / f).read_text(errors="replace")))
    for extra in argv[3:]:
        p = Path(extra)
        (dest / p.name).write_text(redact(p.read_text(errors="replace")))
    for node_dir in sorted(p for p in raw.iterdir() if p.is_dir() and p.name.startswith("rdqm-")):
        nd = dest / node_dir.name
        nd.mkdir(exist_ok=True)
        for name in NODE_AS_IS:
            if (node_dir / name).exists():
                (nd / name).write_text(redact((node_dir / name).read_text(errors="replace")))
        for name in ("journal.txt", "journal-boot-1.txt"):
            if (node_dir / name).exists():
                journal(node_dir / name, nd / name, t0, t1)
        if (node_dir / "events2.log").exists():
            timed(node_dir / "events2.log", nd / "events2.log", t0, t1, "iso", t0.year)
        if (node_dir / "pacemaker-window.log").exists():
            timed(node_dir / "pacemaker-window.log", nd / "pacemaker.log", t0, t1, "pcmk", t0.year)
        rl = node_dir / "files/var/mqm/errors/RDQM.0.LOG"
        if rl.exists():
            timed(rl, nd / "RDQM.0.LOG", t0, t1, "rdqm", t0.year)
        for log in sorted((node_dir / "files").rglob("AMQERR0*.LOG")):
            amqerr(log, nd / "amqerr" / log.relative_to(node_dir / "files"), t0, t1)
        for ini in sorted((node_dir / "files/var/mqm/rdqm").glob("*.ini")) if (node_dir / "files/var/mqm/rdqm").exists() else []:
            (nd / ini.name).write_text(ini.read_text(errors="replace"))
        if (node_dir / "poll.log").exists():
            poll(node_dir / "poll.log", nd / "poll.log")
        if (node_dir / "snap").exists():
            shutil.copytree(node_dir / "snap", nd / "snap", dirs_exist_ok=True)
            for f in (nd / "snap").rglob("*"):
                if f.is_file():
                    f.write_text(redact(f.read_text(errors="replace")))
    guard(dest)
    print(f"exported {raw.name} -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
