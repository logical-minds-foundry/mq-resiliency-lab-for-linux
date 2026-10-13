"""Run mqrc.ibmmq_refs against the REAL ibmmq Python layer, in a child interpreter.

Driven by tests/test_ibmmq_refs.py as ``python -I ibmmq_layer_probe.py <code> <src>``,
where ``<code>`` holds the ``ibmmq/`` package extracted from the pinned sdist and
``<src>`` is the component's ``src/``. Prints one JSON object: the reference and member
counts and every problem found.

The dev VM has no MQ client, so ibmmq's compiled extension (``ibmmqc``, which links
libmqm) cannot load. It is the ONLY thing stubbed: every class, structure field list,
method and constant is ibmmq's own Python. The stub reports each structure at a high
version, so version-gated fields are present -- the guest selfcheck, against the real
compiled extension on MQ 10.0.1, is the unstubbed authority. This runs in a child so
ibmmq's sys.path and sys.modules side effects never reach the test session.
"""

from __future__ import annotations

import importlib.metadata
import json
import sys
import types

_STRUCTS = ("bno", "cd", "cno", "csp", "gmo", "md", "od", "pmo", "sco", "sd", "sts")


def main(code_dir: str, src_dir: str, version: str) -> int:
    sys.path[:0] = [code_dir, src_dir]
    stub = types.ModuleType("ibmmqc")
    stub.__strucversions__ = dict.fromkeys(_STRUCTS, 99)
    stub.__version__ = version
    stub.__cmdlevel__ = 0
    stub.__mqbuild__ = "stub"
    stub.MQCBINIT = lambda _callback: None  # mqcallback registers its trampoline at import
    sys.modules["ibmmqc"] = sys.modules["ibmmq.ibmmqc"] = stub
    real_version = importlib.metadata.version
    importlib.metadata.version = lambda name: version if name == "ibmmq" else real_version(name)

    import ibmmq  # noqa: PLC0415 -- only after the stub is in place

    from mqrc import ibmmq_refs  # noqa: PLC0415 -- from <src>, put on sys.path above

    refs, members = ibmmq_refs.scan(ibmmq_refs.source_files())
    print(
        json.dumps(
            {
                "loaded_from": ibmmq.__file__,
                "refs": len(refs),
                "members": len(members),
                "problems": ibmmq_refs.problems(ibmmq, refs, members),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
