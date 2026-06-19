# IBM MQ diagnostic logging in JSON — a research study

> **Issue:** #282. **Date:** 2026-06-19. **Scope:** MQ-general (all arms), not
> Native-HA-specific. **Target version:** MQ 9.4 (lab runs 9.4.5).
> **Status:** Research complete (Step 1 of #282). **Team decision recorded
> 2026-06-19: JSON-only — we do not parse the legacy multi-line `.LOG` text
> format under any circumstance.**

This study enumerates every way IBM MQ 9.4 can emit **JSON-format diagnostic
logs** (server, system, and client), exactly how each is enabled and what files
or streams it produces, and the version floor for each capability. It also
enumerates **all** the standard text log locations MQ core and MQ Web produce —
both so we know the full surface, and to make explicit which ones we are
deliberately choosing *not* to parse.

Every IBM-MQ claim is quoted from **primary IBM 9.4 canonical documentation**
(retrieved via the content-API route, since the bot `WebFetch` is 403-blocked).
Cited topics are cached gitignored under
`build/refs/ibm-docs/ibm-mq/9.4/` (`manifest.md` lists each). Claims tagged
**[doc]** come directly from IBM text; **[judgment]** marks our inference for the
lab; version-floor facts that lean on community/APAR sources are flagged inline.

---

## 1. Bottom line

1. **JSON diagnostic logging is real, supported, and configured per-component
   via `qm.ini` / `mqs.ini` / `mqclient.ini` stanzas — not via an environment
   variable.** The `AMQ_ADDITIONAL_JSON_LOG` env var (the lead in #282) exists
   but is a deprecated 9.0.4 "stop-gap" IBM deliberately never documented; it was
   superseded by the `DiagnosticMessages` stanza. **Do not build on it.**
2. MQ routes diagnostics to any/all of three **diagnostic message services**:
   **File** (`.LOG` text *or* `.json`), **Syslog** (JSON, Linux only), and the
   always-on traditional **QMErrorLog**.
3. **The legacy `AMQERRnn.LOG` text files are always written and cannot be turned
   off** — JSON is *additive*. Our decision is to **ship and parse only the JSON
   surface** and treat the `.LOG` files as an unparsed on-disk artifact.
4. **[judgment, high-value]** The **Syslog** service emits MQ diagnostics as JSON
   into **journald**, which the lab's existing Alloy journald pipeline already
   scrapes. This may light up the cockpit log panels with no new file-tailer at
   all — the strongest design lead to pressure-test in the spike.

---

## 2. The architecture (mental model)

**[doc]** MQ reports diagnostics through *diagnostics message services* with three
endpoint types: **`AMQERRnn` log files**, **JSON-formatted log files**, and
**Syslog in JSON format**. JSON output is **single-line JSON objects — one valid
JSON object per line** (the file as a whole is *not* one JSON object). This is
exactly what a Loki/Alloy JSON parse stage expects.
*(Diagnostic message logging, `q018792`)*

**[doc] The always-on caveat:** *"Regardless of the style of diagnostics logging
configured, the traditional diagnostics files held under
`/var/mqm/errors/AMQERRnn.log` and
`/var/mqm/qmgrs/<qmgr_name>/errors/AMQERRnn.log` are always written to, in
addition to any other logging configuration used."* → JSON never replaces the
text logs; it runs alongside them. *(`q018792`)*

**[doc]** Configuration is per-component via four stanza names:

| Stanza | Config file | Applies to |
|---|---|---|
| `DiagnosticMessages` | **`qm.ini`** | Queue-manager-generated messages |
| `DiagnosticSystemMessages` | **`mqs.ini`** (server) / **`mqclient.ini`** (client) | System messages / client operation (e.g. `runmqsc` in client mode) |
| `DiagnosticMessagesTemplate` | **`mqs.ini`** | Template copied into a new QM's `qm.ini` at QM creation |

*(Diagnostic message service stanzas, `q130440`; Diagnostic message logging,
`q018792`)*

The fourth stanza — `DiagnosticMessagesTemplate` — is operationally important:
it lets us define JSON logging **once** at the system level and have every
*newly created* queue manager inherit it automatically.

---

## 3. Enabling JSON — the queue manager (primary lab case)

**[doc]** Add a **File** service to `qm.ini` with `Format = json`:

```ini
DiagnosticMessages:
   Service    = File
   Name       = JSONLogs
   Format     = json
   FilePrefix = AMQERR
```

*"After restarting, the queue manager will have `AMQERR0x.json` files in its
ERRORS directory."* *(Diagnostic message services, `q018795`)*

File-service attributes **[doc]** (`q018795`):

| Attribute | Meaning | Default |
|---|---|---|
| `Service` | `File` (mandatory) | — |
| `Name` | unique stanza name (mandatory) | — |
| `Format` | `text` or `json`; sets suffix `.LOG` / `.json` | **`json`** (for a custom File service) |
| `FilePath` | absolute dir; supports inserts `+MQ_Q_MGR_DATA_PATH+`, `+MQ_DATA_PATH+` | same dir as `AMQERR01.LOG` |
| `FilePrefix` | log filename prefix | `AMQERR` |
| `FileSize` | rollover size | 32 MB |

Plus the generic filtering attributes (shared with QMErrorLog): `Severities`
(`I/W/E/S/T`, or `0/10/20/40/50`; `E+` = "Error and above"), `ExcludeMessage`,
`SuppressMessage`, `SuppressInterval`. **Changes take effect only on QM restart.**

**[doc]** Multiple File services let us split by severity into separate file sets:

```ini
DiagnosticMessages:
  Name=ErrorsToFile
  Service=File
  Severities=E+
  FilePrefix=OnlyErrors
DiagnosticMessages:
  Name=NonErrorsToFile
  Service=File
  Severities=I,W
  FilePrefix=InformationAndWarning
```

---

## 4. The Syslog service (Linux only — the obs shortcut)

**[doc]** *"The Syslog service is not available on Windows or IBM i."* It sends
unfiltered messages to syslog **using the JSON format diagnostic messages
specification**. One Syslog service only. *(`q018795`)*

```ini
DiagnosticMessages:
  Name=ErrorsToSyslog
  Ident=mq
  Service=Syslog
  Severities=E+
```

- `Ident` — the syslog ident tag (default **`ibm-mq`**).
- Severity → syslog level mapping **[doc]**: `0→LOG_INFO`, `10→LOG_WARNING`,
  `20/30→LOG_ERR`, `40/50→LOG_ALERT`.

**[judgment]** On RHEL, syslog → journald → **already harvested by the existing
Alloy journald pipeline**. Two candidate ingestion paths to A/B in the spike:

- **Syslog service → journald → Alloy** — no new file-tailer; reuses the current
  pipeline. Cost: each record is wrapped in a journald envelope (the MQ JSON
  becomes the `MESSAGE` field), so Alloy needs a nested JSON parse.
- **File service (`AMQERR0x.json`) → Alloy `loki.source.file`** — raw single-line
  JSON objects, cleanest to parse; cost: a new file-tailing target + positions
  tracking per QM errors dir.

---

## 5. The JSON message schema (structured fields = free labels)

**[doc]** Each record is `type: "mq_log"` and carries the following name/value
pairs *(JSON format diagnostic messages, `q130430`)*:

| Field | Type | Meaning |
|---|---|---|
| `ibm_messageId` | string | Message id incl. severity char, e.g. `AMQ9209E` |
| `loglevel` | string | `INFO`, `WARNING`, or `ERROR` |
| `ibm_datetime` | string | ISO-8601 UTC `YYYY-MM-DDTHH:MM:SS.mmmZ` |
| `ibm_serverName` | string | Queue manager name |
| `ibm_qmgrId` | string | Queue manager identifier |
| `host` | string | Host name |
| `module` | string | Source file:line, e.g. `amqccita.c:4214` |
| `ibm_processId` / `ibm_processName` / `ibm_threadId` | num/str/num | Process + thread |
| `ibm_userName` | string | OS user the process runs as |
| `ibm_remoteHost` | string | Client IP, if applicable |
| `ibm_version` | string | VRMF (e.g. `9.4.5.0`) |
| `ibm_sequence` | string | Disambiguates same-timestamp messages |
| `ibm_installationName` / `ibm_installationDir` | string | MQ installation |
| `ibm_commentInsert1-3`, `ibm_arithInsert1-2` | str/num | Message variables |
| `message` | string | Full expanded message text |

Canonical example **[doc]** (`q130430`):

```json
{"ibm_messageId":"AMQ9209E","ibm_commentInsert1":"localhost (127.0.0.1)","ibm_commentInsert3":"SYSTEM.DEF.SVRCONN","ibm_datetime":"2018-02-22T06:54:53.942Z","ibm_serverName":"QM1","type":"mq_log","host":"0df0ce19c711","loglevel":"ERROR","module":"amqccita.c:4214","ibm_sequence":"1519282493_947814358","ibm_remoteHost":"127.0.0.1","ibm_qmgrId":"QM1_2018-02-13_10.49.57","ibm_processId":4927,"ibm_threadId":4,"ibm_version":"9.0.5.0","ibm_processName":"amqrmppa","ibm_userName":"johndoe","ibm_installationName":"Installation1","ibm_installationDir":"/opt/mqm","message":"AMQ9209E: Connection to host 'localhost (127.0.0.1)' for channel 'SYSTEM.DEF.SVRCONN' closed."}
```

**[judgment]** Map to the lab's `cluster_*` label convention with low cardinality:
`host`→host, `ibm_serverName`→qm, `loglevel`→severity, `ibm_messageId`→msgid.
Keep `ibm_datetime`, `ibm_sequence`, and `message` as log *content*, never labels.

---

## 6. Env vars, mqweb, AMQP/MQTT, containers

- **`AMQ_ADDITIONAL_JSON_LOG=1`** — **[doc + community]** Introduced **9.0.4 (CD,
  Nov 2017)** as an opt-in tech preview emitting `AMQERR0x.json` alongside
  `.LOG`. IBM's own developer: it *"was only a stop gap measure and… did not go
  into the Knowledge Center."* **Confirmed absent from the 9.4 env-var reference
  (`q082720`).** → **Do not use.** *(IBM Community: "Introducing MQ Error Logs in
  JSON Format".)*
- **`AMQ_DIAGNOSTIC_MSG_SEVERITY`** — **[doc]** Appends the severity character to
  the message number in logs/console; **on by default**, set `0` to disable.
  (`q082720`)
- **`MQMAXERRORLOGSIZE`** — **[doc]** Env-var equivalent of `ErrorLogSize`/
  `FileSize`. (`q082720`, `q019020`)
- **mqweb (MQ Console / REST API)** — **[doc]** mqweb is **WebSphere Liberty**;
  its logs are `messages.log` + `console.log` (§7). **[doc, Liberty]** Liberty
  supports JSON logging via `<logging messageFormat="json" .../>` in `server.xml`
  (or `com.ibm.ws.logging.message.format=json`). **[judgment]** Applying this to
  mqweb means editing the Liberty config (`mqwebuser.xml`); plausible but **not
  documented by IBM MQ specifically** → spike item, not a confirmed MQ feature.
- **AMQP / MQTT channels** — **[doc]** Separate JSON toggles exist (`Enabling
  JSON formatted logs for AMQP` / `for MQTT`); out of scope unless those
  protocols are in use.
- **Containers (comparison only — lab is VM-based)** — **[doc, mq-container]**
  `MQ_LOGGING_CONSOLE_FORMAT=json` + `MQ_LOGGING_CONSOLE_SOURCE=qmgr,web` mirror
  logs to stdout. Not applicable to VMs.

---

## 7. Full log-surface enumeration (Linux/RHEL)

Paths **[doc]** from *Error log directories* (`q039570`), *Error logs on AIX,
Linux, and Windows* (`q039560`), *FFST: AIX or Linux* (`q040170`),
*Troubleshooting IBM MQ Console and REST API problems* (`q132080`).

### 7.1 MQ core

| Surface | Path | Format | Monitor? |
|---|---|---|---|
| **QM error log** | `/var/mqm/qmgrs/<qmgr>/errors/AMQERR0{1,2,3}.LOG` | text (always on) | **No (ignored)** — same events available as JSON |
| **QM error log (JSON)** | `/var/mqm/qmgrs/<qmgr>/errors/AMQERR0{1,2,3}.json` | JSON | **Yes** (if File service configured) |
| **System/installation error log** | `/var/mqm/errors/AMQERR0{1,2,3}.LOG` | text | **No (ignored)** |
| **System error log (JSON)** | `/var/mqm/errors/AMQERR0{1,2,3}.json` | JSON | **Yes** (if `DiagnosticSystemMessages` JSON-configured) |
| **FFST / FDC** | `/var/mqm/errors/AMQ<pid>.<n>.FDC` | text records | **Indirect** — each FDC also emits a `user.error` syslog record naming the file → visible in journald |
| **Client app error log** | `/var/mqm/errors/AMQERR0{1,2,3}.LOG` (+`.json`) | text/JSON | **Yes (JSON)** — via `mqclient.ini` |

**[doc] Routing notes:** channel-related messages go to the **QM** errors dir
unless the QM is unavailable/unknown, in which case they fall back to the
**system** errors dir. **[doc] Distinction:** `/var/mqm/log/<qmgr>/` holds the
**recovery/transaction logs** (the WAL), *not* diagnostic logs — monitor for
disk/space, never for error events.

### 7.2 MQ Web (mqweb / Liberty)

**[doc]** Default dir:
`/var/mqm/web/installations/<installationName>/servers/mqweb/logs/`
(default install: `/var/mqm/web/installation1/servers/mqweb/logs/`). UTF-8.

| Surface | File | Monitor? |
|---|---|---|
| **mqweb messages** | `messages.log` | **Yes** (JSON if Liberty `messageFormat=json` — spike) |
| **mqweb console** | `console.log` | **Yes** |
| **mqweb trace** | `trace.log` | Only when trace enabled (IBM Support) |
| **mqweb FFDC** | `logs/ffdc/` | Indirect |
| *(config, not logs)* | `jvm.options`, `mqwebuser.xml`, `server.xml` (in `.../servers/mqweb/`) | health check via `crtmqdir -a` |

---

## 8. Version floors

| Capability | Floor | Confidence |
|---|---|---|
| JSON diagnostic messages (preview, via `AMQ_ADDITIONAL_JSON_LOG`) | **9.0.4** (CD) | **[doc, community]** — IBM Community article, author = MQ team |
| `DiagnosticMessages` / `DiagnosticSystemMessages` stanza (File + Syslog, `Format=json`) | **9.1.0** | **[judgment]** APAR SE70292 references the service on 9.1; exact `.0` not pinned to a single doc. All present & documented in 9.4. |
| `AMQ_ADDITIONAL_JSON_LOG` | 9.0.4, **undocumented/deprecated** | **[doc]** absent from 9.4 env-var ref |
| Container stdout mirror (`MQ_LOGGING_CONSOLE_*`) | container image feature | **[doc, mq-container]** |

Lab runs **9.4.5** → everything above is available; the stanza approach is the
supported path.

---

## 9. Implications & open questions for the obs design

- **JSON-only is viable.** The `.LOG` files can't be disabled, but every event we
  care about is reproduced in the JSON surface (File and/or Syslog). We parse
  JSON, full stop. *(Confirms #282 Step 3 — decision made.)*
- **Where do JSON logs get configured?** Two seams: per-QM (`qm.ini`
  `DiagnosticMessages`) and system-wide template (`mqs.ini`
  `DiagnosticMessagesTemplate`, inherited by new QMs). The provisioning roles
  need to lay these down.
- **Spike (Step 2) must settle:** (a) Syslog→journald vs File-tail ingestion;
  (b) whether HA/CRR/Native-HA events (the #279 driver) actually appear in the
  JSON surface and at what severity; (c) mqweb JSON via Liberty.
- **Dashboards:** structured fields (`ibm_messageId`, `loglevel`, `module`,
  `ibm_remoteHost`) unlock per-msgid panels, severity filters, and
  channel/connection error breakdowns the current journald-only panels can't show.

---

## 10. Sources

Primary IBM MQ 9.4 docs (cached under `build/refs/ibm-docs/ibm-mq/9.4/`; verify
any via `https://www.ibm.com/docs/api/v1/content/SSFKSJ_9.4.0/<path>`):

- Diagnostic message logging — `configure/q018792_.html`
- Diagnostic message services (File/Syslog, `Format`) — `configure/q018795_.html`
- Diagnostic message service stanzas — `configure/q130440_.html`
- QMErrorLog stanza — `configure/q019020_.html`
- JSON format diagnostic messages (field table) — `reference/q130430_.html`
- Environment variables descriptions — `configure/q082720_.html`
- mqclient.ini — `configure/q016840_.html`
- Error logs on AIX, Linux, and Windows — `troubleshoot/q039560_.html`
- Error log directories on AIX, Linux, and Windows — `troubleshoot/q039570_.html`
- FFST: IBM MQ for AIX or Linux — `troubleshoot/q040170_.html`
- Troubleshooting IBM MQ Console and REST API problems — `troubleshoot/q132080_.html`

Supporting:

- [Introducing MQ Error Logs in JSON Format (IBM Community)](https://community.ibm.com/community/user/viewdocument/introducing-mq-error-logs-in-json-f-1)
- [WebSphere Liberty — logging and trace](https://www.ibm.com/docs/en/was-liberty/core?topic=liberty-logging-trace)
- [ibm-messaging/mq-container — runmqserver/logging.go](https://github.com/ibm-messaging/mq-container/blob/master/cmd/runmqserver/logging.go)
- [APAR SE70292](https://www.ibm.com/support/pages/apar/SE70292)
