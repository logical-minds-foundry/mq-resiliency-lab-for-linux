"""Grafana access URLs + the vergil port-forward relay units (#170/#264).

Isolated here so BOTH the obs CLI (cli.py) and the import-pure observe phase
(phases.py) can reference them — phases.py must not import cli.py (circular), so
these shared observability constants live in their own leaf module.
"""

from __future__ import annotations

# Grafana on the obs guest's net-mgmt IP, reached directly from inside the VM.
GRAFANA_URL = "http://10.50.0.2:3000"
# What the workstation browses: Lima auto-forwards the base VM's :3000 to the Mac's
# localhost:3000, and the vergil-portforward relay bridges :3000 to the obs guest.
WORKSTATION_GRAFANA_URL = "http://localhost:3000"
# The vergil-portforward relay vergil-vm provisions from port_forwards in
# vergil.toml (#170) — now a plain service with no companion .socket unit (#1013).
# Restarting grafana (the obs role's notify) wedges its held downstream connection,
# so the observe phase bounces it after provisioning (#264).
RELAY_UNITS = ("vergil-portforward-3000.service",)
