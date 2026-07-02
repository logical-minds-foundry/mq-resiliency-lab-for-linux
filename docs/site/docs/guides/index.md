# Guides

Generic, product-level how-to guides for configuring IBM MQ — prose backed by
declarative configuration, pinned to **MQ 9.4**. Each guide is self-contained and
written to be applied on any IBM MQ installation, independent of this lab.

New here? Read the **[authoring standard](guide-standard.md)** first — it defines
the format every guide follows: the two-layer structure (a short how-to up front,
supporting depth in appendices), the scan-safe content boundary, and how
alternatives are ranked. Start a new guide by copying
[`_template.md`](_template.md).

## Available guides

- **[JSON diagnostic logging](mq-json-logging-guide.md)** — make IBM MQ write its
  diagnostic messages as one JSON object per line, across the queue manager,
  clients, and mqweb.
- **[Metrics & monitoring configuration](mq-metrics-config-guide.md)** — enable
  the queue-manager monitoring and statistics a metrics collector (e.g. the IBM MQ
  Prometheus exporter) needs, with coherent per-object inheritance.
- **[TCP configuration](mq-tcp-config-guide.md)** — tune the `TCP` stanza and the
  OS keepalive timers for fast dead-peer detection (HA failover), plus listener
  backlog, connect timeout, and buffers.

## Planned

Further guides tracked under the epic: security (TLS with channel and connection
authentication and object authorizations), among others.
