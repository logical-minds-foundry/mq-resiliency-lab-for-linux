# Guides

Generic, product-level how-to guides for configuring IBM MQ — prose backed by
declarative configuration, pinned to **MQ 10.0.x**. Each guide is self-contained
and written to be applied on any IBM MQ installation, independent of this lab.

!!! note "MQ version: re-pinned from 9.4 to 10.0.x"
    The guides follow the lab's pinned MQ level,
    [`lab/mq-version`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/lab/mq-version)
    (currently `10.0.1.0`). On 2026-10-15
    ([#1555](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1555))
    every guide, the authoring standard and the template were re-pinned from 9.4
    to 10.0.x. Each IBM Docs citation now links the 10.0.x topic, and each was
    compared with its 9.4 counterpart. Where 10.0 differs, the guide says so and
    quotes both versions.

    This was a documentation re-check, not a lab re-run. Each guide's *Last
    validated in lab* line still names the MQ level it was proven on (9.4.x).
    Claims that only a live queue manager can confirm are marked to verify on the
    next 10.0.1 bring-up. MQ 10.0 is the documented default. An older level can
    still be built by changing `lab/mq-version`.

New here? Read the **[authoring standard](guide-standard.md)** first — it defines
the format every guide follows: the two-layer structure (a short how-to up front,
supporting depth in appendices), the scan-safe content boundary, and how
alternatives are ranked. Start a new guide by copying
[`_template.md`](_template.md).

## Available guides

- **[JSON diagnostic logging](mq-json-logging-guide.md)** — make IBM MQ write its
  diagnostic messages as one JSON object per line, across the queue manager,
  clients, and mqweb.
- **[Instrumentation event monitoring](mq-event-monitoring-guide.md)** — turn the
  queue manager's instrumentation events (authority, channel, queue-depth,
  config, command, start/stop) into a JSON feed with the IBM-supplied `amqsevt`
  sample — no PCF-parsing code. The complementary *event* stream to the *log*
  stream above.
- **[Metrics & monitoring configuration](mq-metrics-config-guide.md)** — enable
  the queue-manager monitoring and statistics a metrics collector (e.g. the IBM MQ
  Prometheus exporter) needs, with coherent per-object inheritance.
- **[TCP configuration](mq-tcp-config-guide.md)** — tune the `TCP` stanza and the
  OS keepalive timers for fast dead-peer detection (HA failover), plus listener
  backlog, connect timeout, and buffers.
- **[Authorization & access control](mq-authorization-guide.md)** — map an
  authenticated TLS client's certificate DN to a non-privileged service identity
  with CHLAUTH `SSLPEERMAP`, back it with a deny-all rule, and grant
  least-privilege `setmqaut` authorities per role — including the message-context
  (`+setall`) authority a receiver MCA needs on every destination, the
  dead-letter queue included.
- **[Native HA log lifecycle (runbook)](nativeha-log-lifecycle-guide.md)** — read
  a Native HA queue manager's recovery log as a living system: why it uses
  replicated logging, what automatic log management and automatic media images do
  to log extents, and how to read the log-health band (extent reclaim, disk fill,
  media-image recency, logger events) — including why the active and its replicas
  legitimately diverge. A reading runbook, not a config guide.

## Planned

Further guides tracked under the epic: TLS transport security (ciphers,
certificates, and channel/connection authentication — the authentication layer
beneath the [authorization guide](mq-authorization-guide.md)), among others.
