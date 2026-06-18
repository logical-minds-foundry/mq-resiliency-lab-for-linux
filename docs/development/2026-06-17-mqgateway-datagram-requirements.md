# MQGateway + vendor-initiated datagram queue — requirements capture (SOC)

- **Issue:** #245
- **Date:** 2026-06-17
- **Mode:** Stream-of-consciousness capture of an ad-hoc developer conversation
  about MQ requirements. Notes only — not a design or a decision record. Feeds
  future design work; relates to the two-site vendor DR design (#237).

## Captured notes

- **MQGateway** sits between the production app and our queue manager. It
  **proxies all MQ interaction, bidirectionally** — the app does not talk to the
  QM directly; everything goes through the gateway.
- It mediates **two traffic patterns**:
  1. **Request/reply**, initiated by our app. (This is the pattern the lab models
     today — app → our QM → vendor → reply back.)
  2. **Inbound notification datagrams**: the gateway **listens on queues** for
     one-way messages sent **from the vendor/counterparty** — e.g. start-of-day
     and end-of-day events.
- **The new twist:** a **datagram queue with messages initiated *from* the
  counterparty**. Everything modeled so far is app-initiated request/reply; a
  vendor-initiated, one-way (fire-and-forget) inbound flow is a new pattern that
  is not yet represented.

## Summary

- MQGateway is a bidirectional proxy fronting our QM for the production app.
- It carries two distinct messaging paradigms: (a) app-initiated request/reply,
  and (b) vendor-initiated inbound notification datagrams (SOD/EOD-style events)
  that the gateway listens for.
- The vendor-initiated, one-way inbound path is the new requirement.

## Themes

- **Gateway/proxy layer.** The app is decoupled from the QM by MQGateway — a
  component the lab does not currently model (today `app_requester` connects to
  the QM directly).
- **Two paradigms, not one.** Request/reply (synchronous, correlated, app-driven)
  versus datagram (asynchronous, one-way, vendor-driven). They have different
  delivery, ordering, and failure semantics.
- **Direction reversal.** Until now every modeled flow originates on our side;
  the notification datagrams originate on the counterparty side and arrive
  inbound — a new direction of initiation.

## Open Questions

- **DR behavior for inbound datagrams.** How do vendor-initiated datagrams behave
  during a vendor DR (and our DR)? With no reply path or correlation, the
  duplicate/missing-message reasoning from the #237 vendor-DR design differs —
  e.g. a missed SOD/EOD event has different reconciliation needs than a missed
  reply. Ties to #237's deferred message-integrity dimension.
- **Delivery guarantees.** Are these datagrams persistent or fire-and-forget?
  What are the expectations for ordering and for duplicate SOD/EOD events?
- **Queue topology.** Where do the notification queues live (on our QM, consumed
  by MQGateway?), how is the gateway's listener modeled, and how is it triggered?
- **Is MQGateway in lab scope?** Should the lab model the gateway as a distinct
  layer between app and QM, or continue with the app talking to the QM directly?
- **Fit with existing work.** How this composes with the distributed flow and the
  two-site vendor DR model, and where it lands in the lab phases.
