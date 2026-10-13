"""The ibmmq API this package uses, read off its own source (#1478).

The clients import ibmmq lazily and their unit tests drive a fake ibmmq, so a name the
real binding does not have passes every test and fails on first use: the binding
migration (#1470) kept ``ibmmq.CMQC.MQXPT_TCP``, but ibmmq defines it only in CMQXC,
and every client connect raised AttributeError. A fake cannot catch that, because the
fake is written from the same wrong belief.

So this module parses every source file of the package (``ast``; nothing is imported)
for the ibmmq surface it touches:

- every ``ibmmq.<Name>`` / ``ibmmq.<Module>.<NAME>`` attribute chain (:class:`Ref`);
- every member it uses of an ibmmq class (:class:`MemberRef`): a keyword passed to a
  direct ``ibmmq.<Class>(...)`` call, and an attribute read, written or called on a
  local variable bound from one in the same function (``cd = ibmmq.CD(...)`` then
  ``cd.SSLCipherSpec = ...``; ``qmgr = ibmmq.QueueManager(None)`` then
  ``qmgr.connect_with_options(...)``). An unknown keyword raises AttributeError in
  MQOpts, but an unknown attribute WRITE silently adds a Python attribute the MQI
  never sees.

:func:`problems` checks them against a binding. Two real bindings are used:

- ``mq-resiliency-clients-selfcheck``, on every guest install, checks names AND
  members against the imported compiled ibmmq (a miss fails the install);
- ``tests/test_ibmmq_refs.py``, on the dev VM (no MQ client), checks the constant
  references against the constant modules of the ibmmq sdist pinned in ``uv.lock``.

:func:`connect_calls` lists every queue-manager connect in the source, so
``tests/test_mqconn.py`` can require that the package connects only through
mqrc.mqconn.connect_client, which forces ``MQCNO_CLIENT_BINDING`` (#1480).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mqrc

BINDING = "ibmmq"


@dataclass(frozen=True, order=True)
class Ref:
    """``ibmmq.<path[0]>.<path[1]>...`` at ``where`` (``file.py:line``)."""

    path: tuple[str, ...]
    where: str


@dataclass(frozen=True, order=True)
class MemberRef:
    """Member ``member`` of class ``ibmmq.<cls>``, used at ``where`` (``file.py:line``)."""

    cls: str
    member: str
    where: str


def package_dir() -> Path:
    return Path(mqrc.__file__).parent


def source_files(root: Path | None = None) -> list[Path]:
    """Every ``.py`` file of the package (or under ``root``), sorted."""
    return sorted((root if root is not None else package_dir()).rglob("*.py"))


def _chain(node: ast.expr) -> tuple[str, ...] | None:
    """``ibmmq.A.B`` -> ("A", "B"); anything not rooted at the bare name ibmmq -> None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name) and node.id == BINDING and parts:
        return tuple(reversed(parts))
    return None


def _class_call(node: ast.expr) -> str | None:
    """``ibmmq.X(...)`` -> "X"; anything else -> None."""
    if isinstance(node, ast.Call):
        chain = _chain(node.func)
        if chain is not None and len(chain) == 1:
            return chain[0]
    return None


def _local_members(func: ast.FunctionDef | ast.AsyncFunctionDef, rel: str) -> set[MemberRef]:
    """Attributes used on locals of ``func`` bound by ``name = ibmmq.X(...)``."""
    bound: dict[str, str] = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            cls = _class_call(node.value)
            if cls is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bound[target.id] = cls
    return {
        MemberRef(bound[node.value.id], node.attr, f"{rel}:{node.lineno}")
        for node in ast.walk(func)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in bound
    }


def scan(files: list[Path], root: Path | None = None) -> tuple[list[Ref], list[MemberRef]]:
    """Every ibmmq attribute chain and class member in ``files``, deduplicated, sorted.

    ``where`` is relative to ``root`` (default: the package directory).
    """
    base = root if root is not None else package_dir()
    refs: set[Ref] = set()
    members: set[MemberRef] = set()
    for path in files:
        tree = ast.parse(path.read_text(), filename=str(path))
        rel = path.relative_to(base).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                chain = _chain(node)
                if chain is not None:
                    refs.add(Ref(chain, f"{rel}:{node.lineno}"))
            elif isinstance(node, ast.Call):
                cls = _class_call(node)
                if cls is not None:
                    members.update(
                        MemberRef(cls, kw.arg, f"{rel}:{node.lineno}")
                        for kw in node.keywords
                        if kw.arg is not None
                    )
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                members |= _local_members(node, rel)
    return sorted(refs), sorted(members)


# Every ibmmq call that issues an MQCONN(X) (#1480): the QueueManager methods and the
# module-level ibmmq.connect(). Matched by attribute name alone, whatever the receiver,
# so a new connect cannot hide behind an alias; a non-MQ ``.connect()`` would be flagged
# too, which is the loud side to err on.
CONNECT_METHODS = frozenset({"connect", "connect_with_options", "connect_tcp_client"})
CLIENT_BINDING = "MQCNO_CLIENT_BINDING"


@dataclass(frozen=True, order=True)
class ConnectCall:
    """An MQCONN(X) issued at ``where`` (``file.py:line``) by ``how``.

    ``client_binding`` is True only for ``connect_with_options(..., opts=<expr>)``
    whose ``opts`` expression names ``MQCNO_CLIENT_BINDING``.
    """

    where: str
    how: str
    client_binding: bool


def _names_client_binding(node: ast.expr) -> bool:
    return any(
        (isinstance(n, ast.Attribute) and n.attr == CLIENT_BINDING)
        or (isinstance(n, ast.Name) and n.id == CLIENT_BINDING)
        for n in ast.walk(node)
    )


def _connect_call(node: ast.Call) -> tuple[str, bool] | None:
    """(how, client_binding) if ``node`` connects to a queue manager, else None.

    Connecting calls: any ``<x>.<method>(...)`` for a method in :data:`CONNECT_METHODS`,
    and ``ibmmq.QueueManager(...)`` unless its name is a literal None (any other name,
    including the default ``''``, connects at construction with the default binding).
    """
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in CONNECT_METHODS:
        opts = next((kw.value for kw in node.keywords if kw.arg == "opts"), None)
        bound = func.attr == "connect_with_options" and opts is not None
        return func.attr, bound and _names_client_binding(opts)
    if _chain(func) == ("QueueManager",):
        name = node.args[0] if node.args else next(
            (kw.value for kw in node.keywords if kw.arg == "name"), None
        )
        if not (isinstance(name, ast.Constant) and name.value is None):
            return "QueueManager", False
    return None


def connect_calls(files: list[Path], root: Path | None = None) -> list[ConnectCall]:
    """Every queue-manager connect in ``files``, sorted (``where`` relative to ``root``)."""
    base = root if root is not None else package_dir()
    found: list[ConnectCall] = []
    for path in files:
        tree = ast.parse(path.read_text(), filename=str(path))
        rel = path.relative_to(base).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                hit = _connect_call(node)
                if hit is not None:
                    found.append(ConnectCall(f"{rel}:{node.lineno}", *hit))
    return sorted(found)


def problems(binding: Any, refs: list[Ref], members: list[MemberRef]) -> list[str]:
    """What ``binding`` lacks of ``refs`` / ``members``; empty when it has everything.

    A member of an ``binding.MQOpts`` structure is looked up on an instance made with
    no arguments (its fields are instance attributes, as MQOpts.set() checks). Any
    other class is never instantiated (a QueueManager would connect): its member is
    looked up on the class, where methods live.
    """
    found: list[str] = []
    for ref in refs:
        obj = binding
        for depth, name in enumerate(ref.path, start=1):
            if not hasattr(obj, name):
                missing = ".".join((BINDING, *ref.path[:depth]))
                found.append(f"{ref.where}: {missing} does not exist")
                break
            obj = getattr(obj, name)
    struct_base = getattr(binding, "MQOpts", None)
    owners: dict[str, object] = {}
    for ref in members:
        cls = getattr(binding, ref.cls, None)
        if cls is None:
            continue  # the missing class itself is already reported as a Ref
        if ref.cls not in owners:
            is_struct = (
                isinstance(struct_base, type)
                and isinstance(cls, type)
                and issubclass(cls, struct_base)
            )
            owners[ref.cls] = cls() if is_struct else cls
        if not hasattr(owners[ref.cls], ref.member):
            found.append(f"{ref.where}: {BINDING}.{ref.cls} has no member {ref.member}")
    return found
