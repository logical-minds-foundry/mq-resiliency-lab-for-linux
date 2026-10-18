"""Exit 0 if the kernel registers the named generic-netlink family, 1 if it does not.

Asks the generic-netlink controller directly (CTRL_CMD_GETFAMILY on a NETLINK_GENERIC
socket) instead of `genl ctrl get name <family>`: iproute2 6.19's `genl` (Ubuntu 26.04)
aborts with "stack smashing detected" on arm64, which read as a missing family (#1609).
Stdlib only, so it runs on the node's system python3.

Usage: genl-family-present.py <family>   (e.g. handshake, the kernel's TLS upcall)
"""

from __future__ import annotations

import os
import socket
import struct
import sys

NETLINK_GENERIC = 16
GENL_ID_CTRL = 0x10
NLMSG_ERROR = 0x2
NLM_F_REQUEST = 0x1
CTRL_CMD_GETFAMILY = 3
CTRL_ATTR_FAMILY_NAME = 2


def request(family: str) -> bytes:
    name = family.encode() + b"\0"
    attr = struct.pack("=HH", 4 + len(name), CTRL_ATTR_FAMILY_NAME) + name
    attr += b"\0" * (-len(attr) % 4)
    body = struct.pack("=BBH", CTRL_CMD_GETFAMILY, 1, 0) + attr
    return struct.pack("=IHHII", 16 + len(body), GENL_ID_CTRL, NLM_F_REQUEST, 1, 0) + body


def main(family: str) -> int:
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_GENERIC) as sock:
        sock.bind((0, 0))
        sock.send(request(family))
        reply = sock.recv(65536)
    (msg_type,) = struct.unpack_from("=H", reply, 4)
    if msg_type == GENL_ID_CTRL:
        print(f"generic-netlink family {family!r} present")
        return 0
    if msg_type == NLMSG_ERROR:
        err = -struct.unpack_from("=i", reply, 16)[0]
        print(
            f"kernel {os.uname().release} has no {family!r} generic-netlink family: "
            f"{os.strerror(err)}",
            file=sys.stderr,
        )
        return 1
    print(f"unexpected netlink reply type {msg_type} for {family!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
