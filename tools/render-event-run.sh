#!/usr/bin/env bash
# Render the mq-event-monitor run.sh wrapper for a given sink, exactly as Ansible
# renders it (same Jinja engine + trim_blocks). Used by the byte-identical syslog
# guard, to inspect the file variant, and to quote the exact script in the report.
# Usage: render-event-run.sh <syslog|file> [QM]
set -euo pipefail
sink="${1:?usage: render-event-run.sh <syslog|file> [QM]}"
qm="${2:-EVTCAP}"
root="$(git rev-parse --show-toplevel)"
role="${root}/ansible/roles/mq-event-monitor"
out="$(mktemp)"; trap 'rm -f "${out}"' EXIT
cd "${root}/ansible"
uv run ansible localhost -m template \
  -a "src=${role}/templates/run.sh.j2 dest=${out} mode=0755" \
  -e "@${role}/defaults/main.yml" \
  -e "qmgr_name=${qm}" -e "mq_event_sink=${sink}" >/dev/null
cat "${out}"
