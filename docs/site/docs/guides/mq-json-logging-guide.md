# IBM MQ JSON diagnostic logging — emit error logs as JSON

- **MQ version:** 9.4 (stanza-based services available from 9.1.0)
- **Status:** Active
- **Last validated in lab:** 2026-06-22
- **Related guides:** none yet (a log-shipping pipeline guide is planned)

---

## 1. Purpose & audience

IBM MQ can write its diagnostic messages — the "error logs": information, warning,
and error events — as **one JSON object per line**, ready for any log processor
(journald/syslog, a file tailer, a SIEM). This guide configures that across the
queue manager, client applications, and the mqweb (MQ Console / REST) server. It
is for anyone who wants MQ diagnostics to be queryable rather than parsed out of
free-form text.

## 2. Scope & version floor

In scope: the MQ-side configuration that makes MQ *produce* JSON — the diagnostic
message service stanzas in the `.ini` files, and the mqweb logging element.

Out of scope: *shipping* those logs anywhere (journald → collector → store). That
is a separate concern and a planned guide; this one assumes you already have, or
will add, whatever transports your logs.

Version floor: the stanza-based diagnostic message services (`Service=File` or
`Service=Syslog`, `Format=json`) are available from **MQ 9.1.0**. Content here is
pinned to **9.4**. Configuration changes take effect on **queue-manager restart**.

## 3. Recommendation

Two output styles exist. Pick per environment; they can coexist across a fleet.

1. **Syslog (recommended on Linux/AIX).** MQ emits each JSON record through
   `syslog()`, so it joins the OS journal as a single managed stream — nothing
   extra to rotate on the box, and it rides your existing log transport.
   *Compromise:* Linux/AIX only, and `ExcludeMessage`/`SuppressMessage` filtering
   is not honoured for client processes (`mqclient.ini`), so the client surface is
   severity-filtered only.
2. **File.** MQ writes `AMQERR0x.json` alongside the legacy `.LOG` files.
   Portable across all platforms and useful when you need JSON on the box itself.
   *Compromise:* extra files to rotate and ship; you own their lifecycle.

Alongside the style choice, **set the template once so every new queue manager
inherits JSON logging automatically** (section 4, step 2). This is the single
highest-leverage step: it removes the per-QM edit and guarantees a freshly created
queue manager is already emitting JSON on first start.

The lab's default is **Syslog + template inheritance**. The evidence behind the
Syslog-versus-File choice is in
[Appendix B](#appendix-b-alternatives-and-tradeoffs).

## 4. How to configure it

Each surface below gets a diagnostic message service stanza. Add the ones that
apply to your deployment; all take effect on the next restart of the component.

1. **Queue manager (`qm.ini`).** Add a `DiagnosticMessages` stanza naming the
   service (`Syslog` or `File`), a unique `Name`, and — for Syslog — an `Ident`
   (syslog tag, default `ibm-mq`) and the `Severities` you want. For the File
   style, set `Format = json`. See
   [Appendix C](#appendix-c-complete-configuration-examples) for both variants in
   full.
2. **Every new queue manager (`mqs.ini`).** Add a `DiagnosticMessagesTemplate`
   stanza to the system-wide `mqs.ini`. MQ copies it into each queue manager's
   `qm.ini` at `crtmqm` time, so set it once and every queue manager created
   afterwards gets JSON logging without a per-QM edit.
3. **System / installation diagnostics (`mqs.ini`).** Add a
   `DiagnosticSystemMessages` stanza to catch messages emitted when no
   queue-manager context exists — early errors, or a queue manager that cannot
   write its own logs.
4. **Client applications (`mqclient.ini`).** Add a `DiagnosticSystemMessages`
   stanza for MQI/client processes (for example `runmqsc` in client mode, or
   sample apps). `mqclient.ini` is read from the application's directory, from
   `/var/mqm/mqclient.ini`, or from the path in the `MQCLNTCF` environment
   variable.
5. **MQ Console / REST server (mqweb).** mqweb is WebSphere Liberty, not the
   diagnostic-service stanzas. Turn on JSON for its `messages.log` by adding a
   Liberty `<logging>` element to the server configuration (`mqwebuser.xml`), then
   restart the mqweb server. The element is shown in
   [Appendix C](#appendix-c-complete-configuration-examples).

## 5. Verify it worked

- **File style:** find the newest `AMQERR0x.json` in the queue manager's `errors`
  directory and confirm each line is a single, self-contained JSON object.
- **Syslog style (Linux):** read the journal entries tagged with your `Ident`
  (default `ibm-mq`) and confirm each record is one JSON object per line.
- **Content check:** a valid record carries the key fields in
  [Appendix A](#appendix-a-full-parameter-reference) — notably `ibm_messageId`
  (e.g. `AMQ9209E`), `loglevel`, and the expanded `message` text. If those are
  present and each line parses cleanly, the surface is configured correctly.

## 6. What stays / caveats

- **The legacy text logs never go away.** The multi-line `AMQERRnn.LOG` files are
  always written and cannot be turned off. JSON is *additive* — it carries the
  same events, line-oriented. You simply stop shipping and parsing the text logs.
- **Restart to apply.** A `qm.ini` change takes effect only on queue-manager
  restart. With the template (step 2), a freshly created queue manager already has
  the stanza, so no restart is needed in the normal create-then-start flow.
- **Do not use `AMQ_ADDITIONAL_JSON_LOG`.** That environment variable was a 9.0.4
  stop-gap; it is undocumented in current MQ and superseded by these stanzas.
- **Client filtering is limited.** `ExcludeMessage` / `SuppressMessage` are not
  honoured in `mqclient.ini`; the client surface is severity-filtered only.

---

## Appendix A: Full parameter reference

### A.1 Stanza attributes

| Attribute | Applies to | Meaning |
|---|---|---|
| `Service` | all | `File` or `Syslog` (mandatory) |
| `Name` | all | unique stanza name (mandatory) |
| `Format` | File | `json` or `text` (`json` is the default for a custom File service) |
| `FilePrefix` / `FileSize` | File | filename prefix (default `AMQERR`) / rollover size (default 32 MB) |
| `Ident` | Syslog | syslog tag (default `ibm-mq`) |
| `Severities` | all | `I,W,E,S,T` or numeric `0,10,20,40,50`; `E+` = error-and-above |
| `ExcludeMessage` / `SuppressMessage` | `qm.ini` / `mqs.ini` only | drop / rate-limit specific message numbers |

### A.2 The JSON record (key fields)

Each record is `type:"mq_log"` and includes, among others: `ibm_messageId` (e.g.
`AMQ9209E`), `loglevel` (`INFO` / `WARNING` / `ERROR`), `ibm_datetime` (ISO-8601
UTC), `ibm_serverName` (queue-manager name), `host`, `module` (`source.c:line`),
`ibm_remoteHost` (client IP), and `message` (the full expanded text).

## Appendix B: Alternatives and tradeoffs

| Dimension | Syslog | File |
|---|---|---|
| Platforms | Linux / AIX only | all platforms |
| Where it lands | OS journal / syslog, one managed stream | `AMQERR0x.json` beside the `.LOG` files |
| On-box lifecycle | OS-managed | you rotate and ship the files |
| Client `ExcludeMessage` | not honoured (severity filter only) | not honoured (severity filter only) |
| Best when | shipping centrally on Linux/AIX | you need JSON on the box, or non-syslog platforms |

The recommendation ranks Syslog first for centralized Linux/AIX shipping because
it removes on-box file management and joins one OS-managed stream. Choose File
when a platform lacks syslog, or when a local JSON copy on the box has value. The
tradeoff is yours to weigh against your transport and platform mix.

## Appendix C: Complete configuration examples

**Queue manager (`qm.ini`) — Syslog variant** (Linux/AIX; emits JSON via
`syslog()`):

```ini
DiagnosticMessages:
   Service    = Syslog
   Name       = JSONLogs
   Ident      = ibm-mq          # syslog identifier/tag (default: ibm-mq)
   Severities = all             # I, W, E, S, T  (use E+ for errors-and-above)
   ExcludeMessage = 9001,9002   # optional: drop routine channel start/stop noise
```

**Queue manager (`qm.ini`) — File variant:**

```ini
DiagnosticMessages:
   Service    = File
   Name       = JSONLogs
   Format     = json
   FilePrefix = AMQERR
```

After restart, the File variant writes `AMQERR0x.json` in the queue manager's
`errors` directory (`/var/mqm/qmgrs/<QM>/errors/`).

**Every new queue manager (`mqs.ini`):**

```ini
DiagnosticMessagesTemplate:
   Service    = Syslog
   Name       = JSONLogs
   Ident      = ibm-mq
   Severities = all
```

**System / installation diagnostics (`mqs.ini`):**

```ini
DiagnosticSystemMessages:
   Service    = Syslog
   Name       = JSONLogs
   Ident      = ibm-mq
   Severities = all
```

**Client applications (`mqclient.ini`):**

```ini
DiagnosticSystemMessages:
   Service    = Syslog
   Name       = JSONLogs
   Ident      = ibm-mq
   Severities = all
```

**MQ Console / REST server (mqweb) — Liberty `<logging>` element in
`mqwebuser.xml`:**

```xml
<logging messageFormat="json" messageSource="message,ffdc"/>
```

mqweb logs land in
`<MQ_DATA>/web/installations/<installation>/servers/mqweb/logs/messages.log`.
Restart the mqweb server to apply.

## Appendix D: Troubleshooting

Work the surfaces in order; the first one that is empty is your fault domain.

| Symptom | Likely cause | Fix |
|---|---|---|
| No JSON at all from a queue manager | `qm.ini` has no `DiagnosticMessages` stanza, or the queue manager was created before the `mqs.ini` template was set | confirm the stanza is present (or the template is set and the QM re-created); restart the queue manager |
| JSON to the wrong place | `Service` set to `File` when you expected Syslog, or vice-versa | correct `Service` and, for File, ensure `Format = json` |
| A line will not parse as JSON | you are reading a legacy `.LOG` text file, not the JSON output | read `AMQERR0x.json` (File) or the entries tagged with your `Ident` (Syslog) |
| Bursts of events missing under load | the OS log layer is rate-limiting (e.g. journald default rate limits) | raise or disable rate limiting for the MQ log source at the OS layer |
| mqweb still writing text | the Liberty `<logging>` element is missing or the mqweb server was not restarted | add the element to `mqwebuser.xml` and restart mqweb |

Notes:

- A `qm.ini` change requires a queue-manager restart; with the `mqs.ini` template,
  a freshly created queue manager already has the stanza and needs no restart.
- Confirm no events are silently dropped: MQ and the OS log layer should report no
  "suppressed" counters for the MQ source. If they do, the drop is at the OS log
  layer, not in MQ.

## Appendix E: References

Search these topic titles in the IBM MQ 9.4 documentation at
<https://www.ibm.com/docs/en/ibm-mq/9.4>: *Diagnostic message logging*,
*Diagnostic message services*, *Diagnostic message service stanzas*, *JSON format
diagnostic messages*, and *QMErrorLog stanza*.
