#!/usr/bin/env bash
# ansible/inventory.sh - render an INI inventory from vagrant ssh-config.
# Only RUNNING machines are included; groups are assigned by name pattern.
# Usage: ansible/inventory.sh   (writes build/inventory.ini)
set -euo pipefail
cd "$(dirname "$0")/../lab"

mapfile -t RUNNING < <(vagrant status --machine-readable 2>/dev/null \
  | awk -F, '$3=="state" && $4=="running"{print $2}' | sort -u)

qm=(); cl=(); ra=(); rb=()
for h in "${RUNNING[@]:-}"; do
  case "$h" in
    qm-main | dtcc-sim) qm+=("$h") ;;
    app-client) cl+=("$h") ;;
    rdqm-a*) ra+=("$h") ;;
    rdqm-b*) rb+=("$h") ;;
  esac
done

{
  echo "[qm_hosts]"; printf '%s\n' "${qm[@]:-}"
  echo "[client_hosts]"; printf '%s\n' "${cl[@]:-}"
  echo "[rdqm_a]"; printf '%s\n' "${ra[@]:-}"
  echo "[rdqm_b]"; printf '%s\n' "${rb[@]:-}"
  echo "[all:vars]"
  echo "ansible_user=vagrant"
  echo "ansible_python_interpreter=/usr/bin/python3"
} | grep -v '^$' > ../build/inventory.ini

for h in "${RUNNING[@]:-}"; do
  [ -n "$h" ] || continue
  cfg=$(vagrant ssh-config "$h")
  hn=$(awk '/HostName/{print $2}' <<<"$cfg")
  port=$(awk '/Port/{print $2}' <<<"$cfg")
  key=$(awk '/IdentityFile/{print $2}' <<<"$cfg")
  sed -i "s|^${h}\$|${h} ansible_host=${hn} ansible_port=${port} ansible_ssh_private_key_file=${key} ansible_ssh_common_args='-o StrictHostKeyChecking=no'|" ../build/inventory.ini
done
echo "wrote build/inventory.ini (${#RUNNING[@]} hosts)" >&2
