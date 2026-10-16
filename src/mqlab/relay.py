"""Grafana access URLs + discovery of the vergil port-forward relay (#170/#264/#1450).

Isolated here so BOTH the obs CLI (cli.py) and the import-pure observe phase
(phases.py) can reference them — phases.py must not import cli.py (circular), so
these shared observability facts live in their own leaf module.

The relay is NOT a constant (#1450). vergil-vm provisions one
``vergil-portforward-<vm-port>.service`` per ``port_forwards`` record
(``"<vm-port>|<host:port>"``) in the active ``[vm.<identity>]`` profile of
vergil.toml, and describes it ``Vergil port-forward relay (:<vm-port> -> <host:port>)``
(vergil-vm ``templates/provision/80-port-forwards.sh``). The vm-port differs per
profile — ``vergil-user`` forwards 3000, ``anonymous`` 3001 — so the unit name and the
workstation URL are discovered from the units actually installed on this VM, selecting
the one(s) whose target is the obs Grafana backend. The discovery is one read-only,
human-runnable command (``RELAY_LIST_ARGV``); its absence is a loud failure, never a
silent skip that would hide a broken forward.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from urllib.parse import urlsplit

# Grafana on the obs guest's net-mgmt IP, reached directly from inside the VM.
GRAFANA_URL = "http://10.50.0.2:3000"
# The relay target that identifies the Grafana forward — derived from GRAFANA_URL, the
# one in-tree statement of where Grafana lives, never a second literal.
GRAFANA_BACKEND = urlsplit(GRAFANA_URL).netloc

# The glass-box discovery command: list every vergil port-forward relay unit with its
# description (which names the listen port and the forward target).
RELAY_UNIT_GLOB = "vergil-portforward-*.service"
RELAY_LIST_ARGV = (
    "systemctl",
    "list-units",
    "--all",
    "--plain",
    "--no-legend",
    "--no-pager",
    RELAY_UNIT_GLOB,
)

# `<unit> <load> <active> <sub> Vergil port-forward relay (:<port> -> <target>)`
_DESCRIPTION = re.compile(r"\(:(?P<port>\d+) -> (?P<target>\S+)\)\s*$")

RELAY_REMEDY = (
    f"declare a '<vm-port>|{GRAFANA_BACKEND}' record in this VM profile's port_forwards "
    "(vergil.toml [vm.<identity>]), rebuild the VM so vergil-vm installs the relay, "
    "then re-run: mqlab obs relay-heal"
)


class RelayError(RuntimeError):
    """The Grafana port-forward relay could not be discovered (none, or unreadable)."""


@dataclass(frozen=True)
class Relay:
    """One installed vergil port-forward relay unit."""

    unit: str
    port: int
    target: str

    @property
    def workstation_url(self) -> str:
        """What the workstation browses: Lima auto-forwards the VM's listen port to the
        same port on the workstation's localhost."""
        return f"http://localhost:{self.port}"


def parse_relays(listing: str) -> list[Relay]:
    """Parse ``RELAY_LIST_ARGV`` output into relays, failing loud on a line whose
    description no longer carries the ``(:<port> -> <target>)`` shape — a vergil-vm
    format drift must not be mistaken for "no Grafana relay"."""
    relays: list[Relay] = []
    for line in listing.splitlines():
        if not line.strip():
            continue
        unit = line.split()[0]
        match = _DESCRIPTION.search(line)
        if match is None:
            msg = (
                f"cannot read the forward of relay unit {unit!r} from "
                f"`{' '.join(RELAY_LIST_ARGV)}` (expected a '(:<port> -> <host:port>)' "
                f"description): {line!r}"
            )
            raise RelayError(msg)
        relays.append(Relay(unit=unit, port=int(match["port"]), target=match["target"]))
    return sorted(relays, key=lambda r: r.port)


def list_relay_units() -> str:
    """Run the discovery command and return its stdout (the I/O seam tests replace)."""
    try:
        done = subprocess.run(  # noqa: S603 — fixed argv, no shell
            RELAY_LIST_ARGV, check=True, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or exc
        msg = f"`{' '.join(RELAY_LIST_ARGV)}` failed: {detail}"
        raise RelayError(msg) from exc
    return done.stdout


def relays_to(target: str) -> list[Relay]:
    """The installed relay unit(s) whose forward target is ``target``, sorted by port.

    An empty list means no such relay is installed; the caller decides whether that is
    fatal (it is for Grafana, and for the MQ Console on a local Lima VM).
    """
    relays = [r for r in parse_relays(list_relay_units()) if r.target == target]
    return sorted(relays, key=lambda r: r.port)


def grafana_relays() -> list[Relay]:
    """The installed relay unit(s) forwarding to the obs Grafana, sorted by port.

    Raises RelayError, naming the remedy, when none is installed — the caller must
    fail loud rather than skip the heal and leave a broken forward unnoticed.
    """
    relays = relays_to(GRAFANA_BACKEND)
    if not relays:
        msg = f"no vergil port-forward relay to Grafana ({GRAFANA_BACKEND}) is installed; "
        raise RelayError(msg + RELAY_REMEDY)
    return relays
