"""Flatten a `cibadmin -Q` dump into sorted, diffable attribute lines (#1666).

Usage: python3 cibattrs.py <cib.xml> [<cib.xml> ...]

One line per fact:
  crm_config <set-id> <name>=<value>
  rsc_defaults|op_defaults <set-id> <name>=<value>
  node-attr <node> <name>=<value>               (permanent, /cib/configuration/nodes)
  transient <node> <name>=<value>               (/cib/status/node_state/transient_attributes)
  rsc-meta <resource> <name>=<value>            (meta_attributes, e.g. target-role)
  constraint <tag> <id> <attr>=<value> ...
  node-state <node> <attr>=<value> ...          (in_ccm, crmd, join, expected)
With several files, each line is prefixed by the file's parent directory name.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def nvpairs(elem: ET.Element) -> list[tuple[str, str]]:
    return [(nv.get("name", "?"), nv.get("value", "")) for nv in elem.iter("nvpair")]


def flatten(path: Path) -> list[str]:
    if not path.exists():
        return ["NO-CIB (node unreachable at this snapshot)"]
    text = path.read_text(errors="replace")
    start = text.find("<cib")
    if start < 0:
        return [f"NO-CIB {text.strip()[:200]}"]
    root = ET.fromstring(text[start:])
    out: list[str] = []
    cfg = root.find("configuration")
    if cfg is not None:
        for tag in ("crm_config", "rsc_defaults", "op_defaults"):
            sec = cfg.find(tag)
            for s in sec if sec is not None else []:
                out += [f"{tag} {s.get('id')} {n}={v}" for n, v in nvpairs(s)]
        for node in cfg.iterfind("nodes/node"):
            out += [f"node-attr {node.get('uname')} {n}={v}" for n, v in nvpairs(node)]
        for res in cfg.iter():
            if res.tag in ("primitive", "clone", "master", "group"):
                for meta in res.findall("meta_attributes"):
                    out += [f"rsc-meta {res.get('id')} {n}={v}" for n, v in nvpairs(meta)]
        cons = cfg.find("constraints")
        for c in cons if cons is not None else []:
            attrs = " ".join(f"{k}={v}" for k, v in sorted(c.attrib.items()) if k != "id")
            out.append(f"constraint {c.tag} {c.get('id')} {attrs}")
    names = {n.get("id"): n.get("uname") for n in root.iterfind("configuration/nodes/node")}
    for ns in root.iterfind("status/node_state"):
        uname = ns.get("uname") or names.get(ns.get("id"), f"id={ns.get('id')}")
        keys = ("in_ccm", "crmd", "join", "expected")
        out.append(f"node-state {uname} " + " ".join(f"{k}={ns.get(k)}" for k in keys))
        for ta in ns.iterfind("transient_attributes"):
            out += [f"transient {uname} {n}={v}" for n, v in nvpairs(ta)]
    return sorted(out)


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv[1:]]
    for f in files:
        for line in flatten(f):
            print(f"{f.parent.name} {line}" if len(files) > 1 else line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
