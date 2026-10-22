"""List every CIB change made by a client that is not a Pacemaker daemon (#1666).

Usage: python3 cibwriters.py <pacemaker-window.log> [--year YYYY]

pacemaker-based logs each applied change as a `Diff: --- / +++` block of `cib_perform_op`
lines, then a `cib_process_request ... Completed <op> ... origin=<node>/<client>/<n>` line.
This prints, for every block whose origin client is not a Pacemaker daemon, the completion
line followed by the block's change lines (`++`, `--`, `+ `). Daemon-originated blocks
(pacemaker-attrd, -controld, -based, -fenced, -execd, and `local/client`, the controller's
own status writes) are skipped; transient-attribute changes reach the CIB through
pacemaker-attrd, so they are listed separately from the journal's attrd "Setting" lines.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DAEMONS = ("pacemaker-attrd", "pacemaker-controld", "pacemaker-based", "pacemaker-fenced", "pacemaker-execd")
DONE = re.compile(r"cib_process_request\).*Completed (\S+) operation for section (\S+?):? .*origin=([^/,]+)/([^/,]+)/")
CHANGE = re.compile(r"cib_perform_op\)\s+info: ((?:\+\+|--|\+ ) .*)$")
DIFF = re.compile(r"cib_perform_op\)\s+info: Diff: ---")


def main(argv: list[str]) -> int:
    block: list[str] = []
    for raw in Path(argv[1]).read_text(errors="replace").splitlines():
        if DIFF.search(raw):
            block = []
            continue
        hit = CHANGE.search(raw)
        if hit:
            block.append(hit.group(1).strip())
            continue
        done = DONE.search(raw)
        if done:
            node, client = done.group(3), done.group(4)
            if client not in DAEMONS and node != "local":
                print(f"{raw[:19]} {done.group(1)} {done.group(2)} by {node}/{client}")
                for line in block:
                    if not line.startswith("+  /cib:"):
                        print(f"    {line}")
            block = []
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
