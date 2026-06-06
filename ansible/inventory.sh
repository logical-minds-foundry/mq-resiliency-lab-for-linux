#!/usr/bin/env bash
# ansible/inventory.sh - render an INI inventory from vagrant ssh-config.
# Usage: ansible/inventory.sh   (writes build/inventory.ini)
set -euo pipefail
cd "$(dirname "$0")/../lab"
{
  echo "[qm_hosts]"; echo "qm-main"; echo "dtcc-sim"
  echo "[client_hosts]"; echo "app-client"
  echo "[all:vars]"
  echo "ansible_user=vagrant"
  echo "ansible_python_interpreter=/usr/bin/python3"
} > ../build/inventory.ini
for h in qm-main dtcc-sim app-client; do
  cfg=$(vagrant ssh-config "$h")
  hn=$(awk '/HostName/{print $2}' <<<"$cfg")
  port=$(awk '/Port/{print $2}' <<<"$cfg")
  key=$(awk '/IdentityFile/{print $2}' <<<"$cfg")
  sed -i "s|^${h}\$|${h} ansible_host=${hn} ansible_port=${port} ansible_ssh_private_key_file=${key} ansible_ssh_common_args='-o StrictHostKeyChecking=no'|" ../build/inventory.ini
done
echo "wrote build/inventory.ini" >&2
