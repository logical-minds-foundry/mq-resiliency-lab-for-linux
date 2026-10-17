"""Two-hop human access to the live MQ Console (epic .github#341 spec §2.3).

Hop 2 (this module) runs on the dev VM: a socat forwarder from the VM's
``127.0.0.1:19443`` to the live instance's data-plane ``:9443``, re-pointed on every
``mqlab console open``. TLS passes straight through, so the browser sees the real
``mqweb`` certificate.

Hop 1 takes the workstation into the dev VM. It is a static vergil-vm relay, declared
exactly like the Grafana one: each ``vergil.toml`` ``port_forwards`` record
``"<vm-port>|<host:port>"`` becomes a ``vergil-portforward-<vm-port>`` unit listening on
the VM's ``0.0.0.0:<vm-port>`` and forwarding to ``<host:port>`` (vergil-vm
``provision/80-port-forwards.sh``). The lab profiles declare ``9443|127.0.0.1:19443``
(``vergil-user``) and ``9444|127.0.0.1:19443`` (``anonymous``), beside Grafana's
``3000`` / ``3001``. The port is discovered from the installed units, never assumed.
On a local Lima VM, Lima forwards that port to the Mac's ``localhost``. On a cloud VM,
the human browses the VM's external IP (shown by ``vrg-vm list``) on the same port,
but the vergil-vm GCP firewall currently admits only tcp:3000, so the Console port
needs a vergil-vm firewall rule first (a cross-repo change, outside this module).

Accepted quirks (documented for users): getting into the ephemeral lab cannot be
transparent, so the user re-runs ``mqlab console open`` after a failover or DR cutover;
and the browser warns once about the certificate (the lab CA is deliberately not
trusted by the workstation, issue #1503).
"""

from __future__ import annotations

import socket
import time
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Callable

    from mqlab.relay import Relay

FORWARD_PORT = 19443
FORWARD_BACKEND = f"127.0.0.1:{FORWARD_PORT}"  # the vergil relay target of the Console forward
UNIT = "mqlab-console-forward"
LOGIN_PATH = "/ibmmq/console/login.html"
# The canonical "how the workstation reaches lab services" section (#1592).
ACCESS_DOCS = "docs/site/docs/operate/index.md#reaching-lab-services-from-your-workstation"
# socat -T: reap a connection idle this long in both directions (the vergil relay's
# lesson, vergil-vm #298: without it, a vanished client strands its fds forever).
IDLE_TIMEOUT = 120
# The three human logins and their IBM roles (spec §2.2). Lab convention: password =
# username; these are lab-only logins, never real secrets.
HUMAN_USERS = (
    ("mqadmin", "MQWebAdmin: full administration"),
    ("mqreadonly", "MQWebAdminRO: read-only administration"),
    ("mqoperator", "MQWebUser: what the mqoperator MQ authority allows"),
)

RELAY_REMEDY = (
    f"declare a '9443|{FORWARD_BACKEND}' record in this VM profile's port_forwards "
    "(vergil.toml [vm.<identity>]) and rebuild the VM (vrg-vm rebuild …) so vergil-vm "
    "installs the relay"
)


class _Closeable(Protocol):
    def close(self) -> None: ...


class ConsoleError(RuntimeError):
    """The Console forward could not be set up (fail loud, name the remedy)."""


def load_state_argv() -> list[str]:
    """Read the forwarder unit's LoadState: ``loaded`` when a previous forwarder exists.

    An absent unit reads ``not-found`` with exit 0, so the caller stops only a unit that
    exists and need not tolerate a failing stop (#1605).
    """
    return ["systemctl", "show", "-p", "LoadState", "--value", UNIT]


def stop_argv() -> list[str]:
    """Stop the previous forwarder (re-run after a failover)."""
    return ["sudo", "systemctl", "stop", UNIT]


def forward_argv(target_url: str, user: str) -> list[str]:
    """Start the forwarder as a transient system unit, run as ``user``.

    A system unit, like vergil-vm's own relays, so it outlives the shell that started it
    without depending on user-session lingering.
    """
    parts = urlsplit(target_url)
    port = parts.port or 9443
    return [
        "sudo",
        "systemd-run",
        f"--unit={UNIT}",
        "--collect",
        f"--uid={user}",
        "socat",
        "-T",
        str(IDLE_TIMEOUT),
        f"TCP4-LISTEN:{FORWARD_PORT},bind=127.0.0.1,reuseaddr,fork",
        f"TCP:{parts.hostname}:{port}",
    ]


def wait_listening(
    *,
    timeout: float = 5.0,
    interval: float = 0.25,
    connect: Callable[[tuple[str, int], float], _Closeable] = socket.create_connection,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Wait until the forwarder accepts on ``127.0.0.1:19443``; ConsoleError if it never does.

    ``systemd-run`` returns once the unit is started, before socat has bound (or failed
    to bind) its port, so a bind failure would otherwise pass silently.
    """
    attempts = max(1, int(timeout / interval))
    last: OSError | None = None
    for attempt in range(attempts):
        try:
            connect(("127.0.0.1", FORWARD_PORT), interval).close()
        except OSError as exc:
            last = exc
            if attempt < attempts - 1:
                sleep(interval)
            continue
        return
    msg = (
        f"the forwarder {UNIT} is not accepting on {FORWARD_BACKEND} after {timeout:g}s "
        f"({last}); see: sudo journalctl -u {UNIT}"
    )
    raise ConsoleError(msg)


def workstation_access(relay: Relay) -> str:
    """Hop 1: the workstation URLs through the discovered vergil relay, for both hosts."""
    return (
        f"From your workstation, through this VM's port {relay.port} (relay {relay.unit}):\n"
        f"  local Lima VM:  https://localhost:{relay.port}{LOGIN_PATH}\n"
        f"  cloud VM:       https://<the VM's external IP, from `vrg-vm list`>:{relay.port}"
        f"{LOGIN_PATH}\n"
        f"Cloud VM caveat: its firewall currently admits only tcp:3000 (Grafana), so port\n"
        f"{relay.port} answers there only once vergil-vm adds a firewall rule for it. See\n"
        f"{ACCESS_DOCS}"
    )


def rerun_command(stack: str, site: str) -> str:
    """The command that re-points the forward (after a failover or a DR cutover)."""
    return f"mqlab console open {stack}" + (f" --site {site}" if site != "a" else "")


def banner(stack: str, qm: str, live_url: str, access: str, site: str = "a") -> str:
    """What the user reads after a successful `mqlab console open`."""
    target = urlsplit(live_url).netloc
    width = max(len(u) for u, _ in HUMAN_USERS) * 2 + 3
    logins = "\n".join(f"  {f'{u} / {u}':<{width}}  ({role})" for u, role in HUMAN_USERS)
    return (
        f"MQ Console for {stack} (queue manager {qm}, site {site}): live instance {live_url}\n"
        f"Forwarder {UNIT}: this VM's {FORWARD_BACKEND} -> {target}\n"
        f"\n"
        f"{access}\n"
        f"\n"
        f"The browser warns once about the certificate. That is expected: the mqweb\n"
        f"certificate names mqweb, not the address you browse, and comes from the lab's\n"
        f"private CA, which your workstation deliberately does not trust. Accept and\n"
        f"continue.\n"
        f"\n"
        f"Logins (lab-only convention, password = username):\n"
        f"{logins}\n"
        f"\n"
        f"The forward points at one instance and does not follow the queue manager.\n"
        f"After a failover or a DR cutover, re-run `{rerun_command(stack, site)}`."
    )
