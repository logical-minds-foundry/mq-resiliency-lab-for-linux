from __future__ import annotations

from mqlab.inventory import lab_inventory
from mqlab.setups import lab_groups, lab_setups


def test_real_topology_renders_without_error():
    out = lab_inventory()  # raises InventoryError on any integrity problem
    assert "[all:vars]" in out
    assert out.endswith("ansible_python_interpreter=/usr/bin/python3\n")


def test_every_setup_group_is_defined():
    groups = lab_groups()
    for setup in lab_setups().values():
        for g in setup.groups:
            assert g in groups, f"{setup.name} references undefined group {g}"
