#!/usr/bin/env python3
"""Rewrite a saved libvirt domain XML for restore (#218).

The restored disks are standalone, flattened qcow2 goldens (qemu-img convert
resolved the overlay->box-base chain into one file), so the saved definition's
<backingStore> for each device='disk' no longer matches the file. Drop it; libvirt
then treats the restored qcow2 as self-contained. Everything else — NIC MACs, the
cdrom (DVD) path, machine type / emulator, the qemu namespace overrides — is
preserved verbatim, so the domain comes back byte-identical apart from the (now
absent) backing chain.

Usage: lab-restore-xml.py <saved-domain.xml>   # rewritten XML -> stdout
"""

import sys
import xml.etree.ElementTree as ET

# Preserve the qemu:* override namespace vagrant-libvirt may use (TCG/commandline),
# so ET round-trips the prefix instead of emitting ns0:.
ET.register_namespace("qemu", "http://libvirt.org/schemas/domain/qemu/1.0")


def main() -> int:
    tree = ET.parse(sys.argv[1])
    root = tree.getroot()
    for disk in root.findall("./devices/disk"):
        if disk.get("device") != "disk":
            continue
        for backing in disk.findall("backingStore"):
            disk.remove(backing)
    sys.stdout.write(ET.tostring(root, encoding="unicode"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
