#!/usr/bin/env bash
# lab/scripts/relay-heal.sh — heal the Grafana port-forward relay if it has wedged (#984).
# STANDALONE: no mqlab / uv / venv. The host unit (lab-relay-heal.service) runs as root with
# a minimal PATH that has neither uv nor an mqlab console script; this script needs only
# bash + curl + systemctl, all of which that PATH has. (It replaced `mqlab obs relay-heal`,
# whose only real work was these two commands but which dragged in the whole mqlab package
# and a host-runnable-mqlab path dance that broke the unit with status=203/EXEC — #984.)
#
# The vergil-portforward relay leaks connections on abrupt client disconnect and wedges into
# accept-then-reset after sustained use — the fd leak that blanks every Grafana dashboard
# (durable upstream fix vergil-project/vergil-vm#298). Probe the workstation-facing forward
# and restart the relay ONLY when it is wedged: a restart drops live Grafana sessions, so a
# healthy relay is left untouched. Fail loud (non-zero) if still wedged after a restart.
#
# Safe to run by hand; also run on a 60s cadence by lab-relay-heal.timer.
set -euo pipefail

# The workstation-facing Grafana forward + the relay units. Kept in sync with
# src/mqlab/relay.py (WORKSTATION_GRAFANA_URL / RELAY_UNITS) — this script is standalone so it
# cannot import them. Override the probe URL via RELAY_PROBE_URL (used by tests).
PROBE_URL="${RELAY_PROBE_URL:-http://localhost:3000/api/health}"
RELAY_UNITS=(vergil-portforward-3000.socket vergil-portforward-3000.service)

# systemctl needs root. The systemd unit already runs as root (empty prefix); a human running
# this by hand does not, so prefix with sudo only then.
maybe_sudo=()
if [ "$(id -u)" -ne 0 ]; then
  maybe_sudo=(sudo)
fi

probe() { curl -fsS -m 5 "$PROBE_URL" >/dev/null 2>&1; }

if probe; then
  echo "grafana port-forward relay healthy — no action"
  exit 0
fi

echo "grafana port-forward relay wedged — restarting (vergil-vm#298)"
"${maybe_sudo[@]}" systemctl restart "${RELAY_UNITS[@]}"

if probe; then
  echo "grafana port-forward relay healed"
  exit 0
fi

echo "relay-heal: relay still wedged after restart — see vergil-vm#298" >&2
exit 1
