# Enabling IBM MQ JSON diagnostic logging — config recipe

A short, portable, **MQ-only** recipe for making IBM MQ (9.x) write its diagnostic
messages as JSON. No site tooling assumed — this is just the queue-manager,
client, and mqweb configuration. Pair it with whatever ships your logs
(journald/syslog, a file tailer, a SIEM). Pinned to **MQ 9.4**.

## What you get, and what stays

- MQ writes diagnostic messages (the "error logs" — info, warning, and error) as
  **one JSON object per line**, ideal for any log processor.
- JSON is **additive**: the legacy multi-line `AMQERRnn.LOG` text files are
  **always written and cannot be turned off**. You simply stop shipping/parsing
  them — the JSON carries the same events line-oriented.
- Configure it with **diagnostic message service stanzas** in the `.ini` files.
  Do **not** use the old `AMQ_ADDITIONAL_JSON_LOG` environment variable — it was a
  9.0.4 stop-gap, is undocumented in current MQ, and is superseded by these stanzas.
- **Version floor:** the stanza-based services (File/Syslog, `Format=json`) are
  available from **MQ 9.1.0**. Changes take effect on **queue-manager restart**.

## Two output styles — pick per environment

| Style | Where it goes | Stanza |
|---|---|---|
| **JSON to files** | `AMQERR0x.json` next to the `.LOG` files | `Service=File`, `Format=json` |
| **JSON to syslog** | the OS syslog/journal (Linux/AIX only) | `Service=Syslog` |

## 1. Queue manager — `qm.ini`

Add a `DiagnosticMessages` stanza. **File** variant:

```ini
DiagnosticMessages:
   Service    = File
   Name       = JSONLogs
   Format     = json
   FilePrefix = AMQERR
```

After restart, the QM writes `AMQERR0x.json` in its `errors` directory
(`/var/mqm/qmgrs/<QM>/errors/`).

**Syslog** variant (Linux/AIX; emits JSON via `syslog()`):

```ini
DiagnosticMessages:
   Service    = Syslog
   Name       = JSONLogs
   Ident      = ibm-mq          # syslog identifier/tag (default: ibm-mq)
   Severities = all             # I, W, E, S, T  (use E+ for errors-and-above)
   ExcludeMessage = 9001,9002   # optional: drop routine channel start/stop noise
```

## 2. Make every new queue manager inherit it — `mqs.ini`

A `DiagnosticMessagesTemplate` stanza in the system `mqs.ini` is **copied into each
queue manager's `qm.ini` at `crtmqm` time** — set it once and every QM created
afterward gets JSON logging automatically:

```ini
DiagnosticMessagesTemplate:
   Service    = Syslog
   Name       = JSONLogs
   Ident      = ibm-mq
   Severities = all
```

## 3. System / installation diagnostics — `mqs.ini`

Catches messages emitted when no queue manager context exists (early errors, or a
QM that can't write its own logs → `/var/mqm/errors`):

```ini
DiagnosticSystemMessages:
   Service    = Syslog
   Name       = JSONLogs
   Ident      = ibm-mq
   Severities = all
```

## 4. Client applications — `mqclient.ini`

For MQI/client processes (e.g. `runmqsc` in client mode, sample apps). Note:
`ExcludeMessage`/`SuppressMessage` are **not** honoured in `mqclient.ini`, so the
client surface is severity-filtered only:

```ini
DiagnosticSystemMessages:
   Service    = Syslog
   Name       = JSONLogs
   Ident      = ibm-mq
   Severities = all
```

(`mqclient.ini` is read from the app's directory, `/var/mqm/mqclient.ini`, or the
path in the `MQCLNTCF` environment variable.)

## 5. MQ Console / REST server (mqweb)

mqweb is **WebSphere Liberty**, not the diagnostic-service stanzas. Turn on JSON
for its `messages.log` by adding a `<logging>` element to the server config
(`mqwebuser.xml`):

```xml
<logging messageFormat="json" messageSource="message,ffdc"/>
```

Logs land in
`<MQ_DATA>/web/installations/<installation>/servers/mqweb/logs/messages.log`
(e.g. `/var/mqm/web/installations/Installation1/servers/mqweb/logs/`). Restart the
mqweb server to apply.

## Stanza attribute quick reference

| Attribute | Applies to | Meaning |
|---|---|---|
| `Service` | all | `File` or `Syslog` (mandatory) |
| `Name` | all | unique stanza name (mandatory) |
| `Format` | File | `json` or `text` (`json` is the default for a custom File service) |
| `FilePrefix` / `FileSize` | File | filename prefix (default `AMQERR`) / rollover size (default 32 MB) |
| `Ident` | Syslog | syslog tag (default `ibm-mq`) |
| `Severities` | all | `I,W,E,S,T` / numeric `0,10,20,40,50`; `E+` = error-and-above |
| `ExcludeMessage` / `SuppressMessage` | qm.ini/mqs.ini only | drop / rate-limit specific message numbers |

## The JSON record (key fields)

`type:"mq_log"` with, among others: `ibm_messageId` (e.g. `AMQ9209E`),
`loglevel` (`INFO`/`WARNING`/`ERROR`), `ibm_datetime` (ISO-8601 UTC),
`ibm_serverName` (QM name), `host`, `module` (`source.c:line`),
`ibm_remoteHost` (client IP), `message` (full expanded text).

## Verify

- **Files:** `tail -1 /var/mqm/qmgrs/<QM>/errors/AMQERR01.json | python3 -m json.tool`
- **Syslog/journald (Linux):** `journalctl -t ibm-mq -o cat | tail -1` → one JSON object

## Sources (IBM MQ 9.4 documentation)

Search these topic titles at <https://www.ibm.com/docs/en/ibm-mq/9.4>:
*Diagnostic message logging*, *Diagnostic message services*, *Diagnostic message
service stanzas*, *JSON format diagnostic messages*, *QMErrorLog stanza*.
