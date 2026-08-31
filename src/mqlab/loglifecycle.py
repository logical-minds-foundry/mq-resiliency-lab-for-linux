"""Non-MQI log-health collector for the Native HA log-lifecycle cockpit band (#810).

Stdlib-only so this exact file deploys verbatim to the nha nodes as
/usr/local/bin/lab-loglifecycle-state and runs on the existing nativeha-state 5s timer,
AND is imported by the repo's unit tests — the nativehastate.py invariant. It polls the
filesystem (no MQI, no PyMQI, no compiled deps): `df` for disk used/total, an `ls` of the
per-QM `active/` extent dir for the S/R extent split, and reuses nativehastate's
`dspmq -o nativeha -x` role detection to tag every metric by instance + role (active and
replica genuinely diverge — spike #808, S3). Pure parse functions turn CLI output into a
row; render_prom turns the row into a node_exporter textfile; probe() runs each source
bounded + non-blocking (timeout -> no fresh filesystem sample -> mqlab_log_sample_stale=1).

Spike S3 corrections applied: the extents live under `/var/mqm/log/<QM>/active/` (NOT
`/var/mqm/qmgrs/<QM>/active`, which is a small control file); extents are named `S…`.LOG
(standard) and `R…`.LOG (reserved/recycled) and BOTH are counted; the LogPath filesystem
may sit on `/`.

Emits (the contract Task 4's cockpit band wires to verbatim):
  mqlab_log_disk_used_bytes{qm,instance,role}
  mqlab_log_disk_total_bytes{qm,instance,role}
  mqlab_log_extents_active{qm,instance,role}      (S… standard extents)
  mqlab_log_extents_inactive{qm,instance,role}    (R… reserved/recycled extents)
  mqlab_log_sample_stale{qm,instance,role}        (1 when the filesystem sample timed out)
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mqlab import nativehastate
else:
    # The type checkers only ever see the package import above; at runtime we pick the import
    # by context. In tests / an installed package it is mqlab.nativehastate; deployed as a
    # standalone script, lab-loglifecycle-state puts /usr/local/bin on sys.path[0], so the
    # sibling nativehastate.py (deployed by the same role) resolves as a bare import (#810).
    try:
        from mqlab import nativehastate
    except ImportError:  # pragma: no cover - deployed context: sibling script on sys.path[0]
        import nativehastate

# Extent files: S<digits>.LOG are standard (in-use) extents; R<digits>.LOG are
# reserved/recycled (spike S3). Everything else in active/ (amqhlctl.lfh, nativeha.ini)
# is not an extent and is ignored.
_EXTENT = re.compile(r"^([SR])\d+\.LOG$")

# Default IBM MQ log root; the per-QM path is <LOGROOT>/<QM>/ with extents in ./active/.
_LOG_ROOT = "/var/mqm/log"


def parse_disk(df_out: str) -> dict[str, int | None]:
    """Parse `df -P <logpath>` into {used_bytes, total_bytes}.

    POSIX `-P` guarantees one line per filesystem (no wrapping), columns:
    Filesystem 1024-blocks Used Available Capacity Mounted-on. Blocks are 1024-byte units.
    A truncated/garbled reading yields None (never a false 0) so one bad sample degrades the
    cell to STALE instead of aborting the whole textfile render (the nativehastate #390
    fail-loud-without-crashing discipline)."""
    lines = [ln for ln in df_out.splitlines() if ln.strip()]
    if len(lines) < 2:  # header only, or empty -> no data row
        return {"used_bytes": None, "total_bytes": None}
    fields = lines[-1].split()
    # Index from the right so a mount path of a single token (e.g. `/`) still lines up:
    # [-5]=1024-blocks [-4]=Used [-3]=Available [-2]=Capacity [-1]=Mounted-on.
    try:
        total_blocks = int(fields[-5])
        used_blocks = int(fields[-4])
    except IndexError, ValueError:
        return {"used_bytes": None, "total_bytes": None}
    return {"used_bytes": used_blocks * 1024, "total_bytes": total_blocks * 1024}


def count_extents(listing: list[str]) -> dict[str, int]:
    """Split an `active/` dir listing into {active: <S… count>, inactive: <R… count>}.

    S… = standard/in-use extents (the primary reclaim-health signal — a monotonic rise with
    no reuse is the 'automation not reclaiming' pattern, spike S2); R… = reserved/recycled.
    Non-extent entries are ignored."""
    active = inactive = 0
    for name in listing:
        m = _EXTENT.match(name.strip())
        if m is None:
            continue
        if m.group(1) == "S":
            active += 1
        else:
            inactive += 1
    return {"active": active, "inactive": inactive}


def instance_role(dspmq_x_out: str, instance: str) -> str:
    """Role of `instance` from `dspmq -m <qm> -o nativeha -x`, lowercased for the metric label.

    A thin wrapper over nativehastate.parse_nativeha_x (the shared role-detection helper — no
    duplicate dspmq parsing). An instance absent from the output reads 'unknown' rather than
    raising."""
    inst = nativehastate.parse_nativeha_x(dspmq_x_out)["instances"].get(instance)
    return inst["role"].lower() if inst is not None else "unknown"


def _m(name: str, labels: Mapping[str, object], value: object) -> str:
    """Format one Prometheus sample line: name{k="v",...} value."""
    rendered = ",".join(f'{k}="{v}"' for k, v in labels.items())
    return f"{name}{{{rendered}}} {value}"


def render_prom(row: Mapping[str, object]) -> str:
    """Project one collected row into node_exporter textfile lines, tagged qm+instance+role.

    Value metrics are emitted only when present (None -> omitted, never a false 0); the
    labeled stale flag is always emitted so a timed-out sample reads STALE, not 'no data'."""
    labels = {"qm": row["qm"], "instance": row["instance"], "role": row["role"]}
    lines: list[str] = []
    for name, key in (
        ("mqlab_log_disk_used_bytes", "used_bytes"),
        ("mqlab_log_disk_total_bytes", "total_bytes"),
        ("mqlab_log_extents_active", "active"),
        ("mqlab_log_extents_inactive", "inactive"),
    ):
        value = row.get(key)
        if value is not None:
            lines.append(_m(name, labels, value))
    lines.append(_m("mqlab_log_sample_stale", labels, row.get("stale", 0)))
    return "\n".join(lines) + "\n"


def probe(cmd: list[str], timeout: int) -> str | None:
    """Run cmd bounded; return stdout on success, None on timeout/nonzero/OSError (-> STALE)."""
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except subprocess.TimeoutExpired, OSError:
        return None
    return cp.stdout if cp.returncode == 0 else None


def _commands(qm: str, log_dir: str, active_dir: str) -> dict[str, tuple[list[str], int]]:
    """source -> (argv, timeout). Role reuses nativehastate's `su - mqm -c dspmq …` invocation
    (single source of the dspmq role probe); disk/extents are plain filesystem reads. All
    timeouts are well under the 5s tick."""
    role_cmd, role_timeout = nativehastate._commands(qm)["nativeha_x"]
    return {
        "role": (role_cmd, role_timeout),
        "disk": (["df", "-P", log_dir], 3),
        "extents": (["ls", "-1", active_dir], 3),
    }


def collect(instance: str, qm: str, log_root: str = _LOG_ROOT) -> str:
    """Run the three probes and render the textfile body for this instance.

    The filesystem sample (disk + extents) is the log-health payload; a timeout on either
    marks the sample STALE and omits the affected values. The role probe is a label only — if
    it times out the metrics are tagged role='unknown' (itself a visible signal), which does
    not by itself mark the sample stale."""
    log_dir = f"{log_root}/{qm}"
    active_dir = f"{log_dir}/active"
    cmds = _commands(qm, log_dir, active_dir)

    role_out = probe(*cmds["role"])
    role = instance_role(role_out, instance) if role_out is not None else "unknown"

    row: dict[str, object] = {
        "qm": qm,
        "instance": instance,
        "role": role,
        "used_bytes": None,
        "total_bytes": None,
        "active": None,
        "inactive": None,
        "stale": 0,
    }
    stale = False

    df_out = probe(*cmds["disk"])
    if df_out is not None:
        disk = parse_disk(df_out)
        row["used_bytes"] = disk["used_bytes"]
        row["total_bytes"] = disk["total_bytes"]
    else:
        stale = True

    ls_out = probe(*cmds["extents"])
    if ls_out is not None:
        counts = count_extents(ls_out.splitlines())
        row["active"] = counts["active"]
        row["inactive"] = counts["inactive"]
    else:
        stale = True

    row["stale"] = 1 if stale else 0
    return render_prom(row)


def main(argv: list[str] | None = None) -> None:
    """Entry point for the deployed collector: `lab-loglifecycle-state --qm NHARAPP`."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--qm", required=True)
    ap.add_argument("--instance", default=os.uname().nodename)
    ap.add_argument("--log-root", default=_LOG_ROOT)
    ap.add_argument("--out", default="/var/lib/node_exporter/textfile/lab_loglifecycle_state.prom")
    args = ap.parse_args(argv)
    body = collect(args.instance, args.qm, log_root=args.log_root)
    tmp = Path(args.out + ".tmp")
    tmp.write_text(body)
    tmp.replace(args.out)


if __name__ == "__main__":
    main()
