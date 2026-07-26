# Syslog fidelity — do the events fit, and is the journald copy faithful?

Captured live on `NHARAPP` (Native HA RHEL, IBM MQ 9.4.5) on 2026-07-26. Each event was read two ways: the **authoritative** copy via a direct `amqsevt -m NHARAPP -b -o json_compact` browse (collector paused), and the **journald** copy the `mq-event-monitor` collector shipped through `logger --size 32768` (tag `mq-events`). Byte lengths of one representative per reason:

| eventReason | code | authoritative B | journald B | fidelity |
|---|---:|---:|---:|---|
| Put Inhibited | 2051 | 512 | 512 | **identical** |
| Queue Full | 2053 | 549 | 549 | **identical** |
| Unknown Object Name | 2085 | 524 | 524 | **identical** |
| Unknown Xmit Queue | 2196 | 546 | 546 | **identical** |
| Queue Depth High | 2224 | 555 | 555 | **identical** |
| Channel Stopped By User | 2279 | 530 | 530 | **identical** |
| Channel Started | 2282 | 472 | 472 | **identical** |
| Channel Stopped | 2283 | 796 | 796 | **identical** |
| Config Create Object | 2367 | 1126 | 1126 | **identical** |
| Config Change Object | 2368 | 2389 | 2389 | **identical** |
| Config Delete Object | 2369 | 2368 | 2368 | **identical** |
| Command MQSC | 2412 | 721 | 695 | diff +26 B (different command instance, not truncation) |

**Finding.** The largest event observed was **2424 bytes** (a `Config Change` full attribute dump) — roughly **13x under the 32,768-byte `logger --size` cap** the `mq-event-monitor` role sets. Every reason type except one was **byte-identical** between the authoritative read and the journald copy; the lone Command-MQSC difference is a different captured command instance (12 command events were forced), **not** truncation. So: **MQ instrumentation events fit comfortably in the journald/syslog path, and the shipped copy is byte-faithful — no truncation.** The `logger --size 32768` setting exists as headroom for pathological cases (very large config objects), not because normal events approach the limit.
