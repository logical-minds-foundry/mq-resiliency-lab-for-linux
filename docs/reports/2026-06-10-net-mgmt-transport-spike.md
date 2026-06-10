# net-mgmt static SSH transport spike — GO

**Date:** 2026-06-10
**Issue:** #101
**Verdict:** GO

## What was proven

The static-inventory premise (spec §11): that Ansible can reach a guest over a
**dedicated host-only management network** with a **shared SSH key**, instead of
scraping `vagrant ssh-config` for a DHCP-assigned management IP and a per-machine
key. If this works, the inventory can be a pure function of `topology.yaml`.

## Method

1. Added `lab/networks/net-mgmt.xml` (host-only, `10.50.0.1/24`, no DHCP) and
   `mqlab net up net-mgmt`.
2. Set `config.ssh.insert_key = false` in `lab/Vagrantfile` (all guests share
   Vagrant's well-known insecure key).
3. Gave `pcmk-a1` a `net-mgmt: 10.50.0.51` NIC; `mqlab vm destroy pcmk-a1 &&
   mqlab vm create pcmk-a1`.
4. Hand-wrote a 1-host inventory pointing `ansible_host` at `10.50.0.51` with
   `ansible_user=vagrant` and `ansible_ssh_private_key_file=~/.vagrant.d/insecure_private_key`.

## Result

```
pcmk-a1 | SUCCESS => {"changed": false, "ping": "pong"}
```

- **Confirmed key path:** `~/.vagrant.d/insecure_private_key` — this is the
  `INSECURE_KEY` constant in `src/mqlab/inventory.py`.
- Ansible auto-discovered `/usr/bin/python3.12` (a warning) only because the
  hand-written spike inventory omitted `ansible_python_interpreter`. The rendered
  inventory pins `/usr/bin/python3`, which suppresses the warning.

## Decision

GO — proceed with the full `net-mgmt` rollout to all nodes, the unified
grouping schema, and retiring `ansible/inventory.sh`.
