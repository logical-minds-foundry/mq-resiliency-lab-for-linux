# Debugging the MQ JSON logging pipeline

The path: **MQ Syslog service → `/dev/log` → journald → Alloy (relabel) → Loki**,
plus a separate **mqweb `messages.log` → Alloy file tail → Loki**. Diagram:
[`../specs/diagrams/mq-json-logging-flow.html`](../specs/diagrams/mq-json-logging-flow.html).
Walk the stages in order; the first one that's empty is your fault domain.

## How it's configured (so you know what "right" looks like)

- The queue-manager stanza is **inherited at `crtmqm`** from the
  `DiagnosticMessagesTemplate` in `/var/mqm/mqs.ini` (seeded by `mq-diag-logging`
  *before* the QM is created). There is **no** separate `qm.ini` edit — a fresh QM
  has the `DiagnosticMessages` Syslog stanza baked in and active on first start.
- System errors use `DiagnosticSystemMessages` in `mqs.ini`; clients use the same
  stanza in `/var/mqm/mqclient.ini`.
- journald rate-limiting is disabled on MQ nodes
  (`/etc/systemd/journald.conf.d/10-mq.conf`) so bursts are never silently dropped.

## Stage 1 — is MQ producing JSON?
- Stanza inherited? `sudo grep -A4 DiagnosticMessages /var/mqm/qmgrs/<QM>/qm.ini`
  (and the template: `sudo grep -A4 DiagnosticMessagesTemplate /var/mqm/mqs.ini`)
- QM up? `sudo -u mqm dspmq -m <QM>` · generate one event:
  `echo "STOP LISTENER(L1414)
  START LISTENER(L1414)" | sudo -u mqm runmqsc <QM>`
- A `qm.ini` change only takes effect on QM restart — but with the template, a
  freshly-created QM already has it, so no restart is needed in normal flow.

## Stage 2 — did it reach journald?
- `sudo journalctl -t ibm-mq -o cat --no-pager | tail -3`
- Empty? Confirm the service is `Service=Syslog` (not `File`) and `Ident=ibm-mq`,
  and that journald is reading `/dev/log` (default).

## Stage 3 — is it valid single-line JSON?
- `sudo journalctl -t ibm-mq -o cat --no-pager | tail -1 | python3 -m json.tool`
- Fails? The `MESSAGE` field isn't a clean JSON blob — re-check against the spike
  finding in the research report; consider the `Service=File` fallback.

## Stage 4 — labelled correctly for Loki?
- The Alloy relabel sets `unit=ibm-mq` only when there's no systemd unit. Inspect
  `/etc/alloy/config.alloy`; restart: `sudo systemctl restart alloy`.

## Stage 5 — is it in Loki?
- `logcli --addr=http://<obs>:3100 query '{unit="ibm-mq"} | json | ibm_messageId != ""' --limit=5`
- mqweb: `logcli --addr=http://<obs>:3100 query '{unit="ibm-mqweb"} | json' --limit=5`
- `logcli --addr=http://<obs>:3100 labels unit` — see what labels exist.
  (`logcli` is installed on the obs node by the `loki` role.)

## mqweb specifics
- JSON on? `sudo tail -1 /var/mqm/web/installations/Installation1/servers/mqweb/logs/messages.log | python3 -m json.tool`
- Alloy tailing it? `alloy_tail_mqweb` is set true for QM-node groups in
  `observability.yml`; the path is the `Installation1` one above. Liberty start is
  slow under TCG — give it time before expecting `messages.log`.

## No silent loss
- `sudo journalctl -t ibm-mq --no-pager | grep -i Suppressed` → must be empty. If
  not, the journald drop-in (`/etc/systemd/journald.conf.d/10-mq.conf`) didn't
  apply; `sudo systemctl restart systemd-journald`.

## Common failure modes
| Symptom | Likely cause | Fix |
|---|---|---|
| Nothing in `journalctl -t ibm-mq` | QM created before the template was seeded, or `Service=File` | confirm `mqs.ini` template; cold-rebuild the QM |
| Entries present, `unit` empty in Loki | Alloy relabel rule missing/typo | fix `config.alloy`, restart alloy |
| `python3 -m json.tool` fails on a line | `MESSAGE` not a JSON blob | re-check spike finding; `Service=File` fallback |
| mqweb rows missing | `alloy_tail_mqweb` false for the group, or wrong Installation path | enable on the group; fix path casing |
| "Suppressed N messages" in journal | journald drop-in not applied | reapply drop-in; restart journald |
