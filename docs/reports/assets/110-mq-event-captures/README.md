# Captured MQ instrumentation-event fixtures (epic #110, T1)

Real `amqsevt` JSON events captured from the live lab — the authoritative reference set behind Report A (working-with-the-data) and Report B (generation reference).

- **Source:** queue manager `NHARAPP`, Native HA RHEL arm, IBM MQ 9.4.5, captured 2026-07-26.
- **Method:** collector (`SERVICE(MQ.EVENT.MONITOR)`) paused, one event per class forced, authoritative copy read with `amqsevt -m NHARAPP -b -o json_compact` (non-destructive browse), collector resumed, journald copy compared.
- **`json/`** — one pretty-printed representative event per reason code.
- **`syslog-fidelity.md`** — authoritative-vs-journald byte comparison + the "do events fit in syslog?" finding.
- **`commands.md`** — the exact force commands.

Captured reason codes:

- **2051** Put Inhibited — `json/2051-put-inhibited.json` (512 B)
- **2053** Queue Full — `json/2053-queue-full.json` (549 B)
- **2085** Unknown Object Name — `json/2085-unknown-object-name.json` (524 B)
- **2196** Unknown Xmit Queue — `json/2196-unknown-xmit-queue.json` (546 B)
- **2222** Queue Mgr Active — `json/2222-queue-mgr-active.json` (510 B)
- **2224** Queue Depth High — `json/2224-queue-depth-high.json` (555 B)
- **2279** Channel Stopped By User — `json/2279-channel-stopped-by-user.json` (530 B)
- **2282** Channel Started — `json/2282-channel-started.json` (472 B)
- **2283** Channel Stopped — `json/2283-channel-stopped.json` (796 B)
- **2367** Config Create Object — `json/2367-config-create-object.json` (1126 B)
- **2368** Config Change Object — `json/2368-config-change-object.json` (2389 B)
- **2369** Config Delete Object — `json/2369-config-delete-object.json` (2368 B)
- **2412** Command MQSC — `json/2412-command-mqsc.json` (721 B)

Not captured here (already documented / deferred): **Not Authorized (2035)** is captured in Report A A.1 (a local unauthorized connect could not be forced on this hardened QM in the capture window); **CHADEV** and **SSLEV** are deferred (need CHAD staging / a staged TLS cert fault).
