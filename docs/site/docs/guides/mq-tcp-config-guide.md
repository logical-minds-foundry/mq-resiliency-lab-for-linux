# IBM MQ TCP configuration — tuning the TCP stanza for HA and performance

- **MQ version:** 9.4 (Multiplatforms)
- **Status:** Draft — pending review
- **Last validated in lab:** 2026-07-02 (config live in lab; guide pending review)
- **Related guides:** [JSON diagnostic logging](mq-json-logging-guide.md),
  [Metrics & monitoring configuration](mq-metrics-config-guide.md)

---

## 1. Purpose & audience

The `TCP` stanza in the queue manager's `qm.ini` (and the client's `mqclient.ini`)
tunes how IBM MQ uses TCP/IP. Its highest-value use is **fast dead-peer
detection** — so a client or channel blocked on a failed or fenced peer gives up
quickly and fails over, instead of hanging — but the same stanza also governs
listener backlog, connect timeouts, socket buffers, and whether plaintext is
allowed at all. This guide is for anyone tuning MQ's TCP behaviour, especially for
high availability.

## 2. Scope & version floor

In scope: the `qm.ini` / `mqclient.ini` `TCP` stanza attributes, and the
**OS-level TCP keepalive settings they depend on** — because the two only work as
a pair (section 3). Pinned to **MQ 9.4 Multiplatforms**.

Out of scope: TLS, ciphers, and certificate configuration — that is the Security
guide's job; this guide covers the transport, not its encryption. Lab-specific
values.

## 3. Recommendation

**Headline — for HA, enable keepalive *and* tune the OS timers together.** This is
the single most important thing in this guide, and the part most often gotten
wrong. Setting `KeepAlive=YES` in the `TCP` stanza only switches on the socket's
`SO_KEEPALIVE` option — it does **not** set the timing. The actual keepalive
schedule comes from the **operating system**, whose default idle time is commonly
**two hours**. With the default, a client blocked in an MQI call against a peer
that was fenced during a failover can hang for up to two hours before the dead
connection is detected.

So enable both:

- `KeepAlive=YES` in the `TCP` stanza, and
- short OS keepalive timers. On Linux, `tcp_keepalive_time` (idle before the first
  probe), `tcp_keepalive_intvl` (gap between probes), and `tcp_keepalive_probes`
  (probes before the connection is declared dead). A tuning of 15 / 5 / 3, for
  example, detects a dead peer in roughly 30 seconds instead of hours.

Then, as needed:

- **`Connect_Timeout`** — default `0` means *no* connect timeout. MQ connects over
  nonblocking sockets and retries up to 20 times before reporting an error. On a
  high-latency or loaded network, set a non-zero value so MQ waits over `select()`
  for the socket to become ready, improving the odds a connect succeeds.
- **`ListenerBacklog`** — default 100 on Linux. Raise it on a queue manager that
  receives bursts of connections; if the backlog fills, TCP rejects the
  connection, message channels go to `RETRY` and clients get
  `MQRC_Q_MGR_NOT_AVAILABLE`. Note the OS may cap the effective backlog below what
  you request.
- **`SecureCommsOnly=YES`** — refuse plaintext connections (a queue manager with it
  off logs a warning at start). Defense-in-depth for the transport; the actual TLS
  setup lives in the Security guide.
- **Socket buffers (`SndBuffSize` / `RcvBuffSize`)** — leave at `0` (OS-managed)
  unless you have a *measured* reason to fix them. IBM warns that wrong values
  hurt TCP performance.

## 4. How to configure it

1. **Queue manager — `qm.ini` `TCP` stanza.** Add the attributes you need; the
   stanza's values override the default channel attributes.

   ```ini
   TCP:
      KeepAlive       = YES
      ListenerBacklog = 4096
      Connect_Timeout = 30
      SecureCommsOnly = YES
   ```

2. **Client — `mqclient.ini` `TCP` stanza.** A client application detects a dead
   peer on its *own* socket, so client-side keepalive matters for a blocked MQI
   call:

   ```ini
   TCP:
      KeepAlive = YES
   ```

3. **Operating system — keepalive timers (Linux).** `KeepAlive=YES` uses these;
   set them short for fast failover detection. As sysctl values (for example in
   `/etc/sysctl.d/`):

   ```ini
   net.ipv4.tcp_keepalive_time  = 15
   net.ipv4.tcp_keepalive_intvl = 5
   net.ipv4.tcp_keepalive_probes = 3
   ```

`qm.ini` changes take effect on queue-manager restart; `mqclient.ini` is read when
the client connects; sysctl changes apply when reloaded.

## 5. Verify it worked

- **Confirm the stanza:** inspect `qm.ini` (or `mqclient.ini`) and confirm the
  `TCP` stanza holds the attributes you set.
- **Confirm keepalive timing:** confirm the OS keepalive timers are the short
  values you set, not the multi-hour default.
- **Confirm the behaviour that matters:** with a peer abruptly lost (fenced during
  a failover drill), a blocked MQI call or channel should detect the dead
  connection within your tuned window (tens of seconds) rather than hanging — that
  end-to-end behaviour is the real test, not the config alone.

## 6. What stays / caveats

- **Keepalive timing is the OS's job.** `KeepAlive=YES` without short OS timers is
  the classic trap — it looks enabled but detects nothing useful for hours.
- **Client vs server.** A client tunes `mqclient.ini` plus its own host's OS
  timers; a queue manager tunes `qm.ini` plus its host's timers. Both ends matter.
- **`ListenerBacklog` is capped by the OS.** The effective backlog can be smaller
  than requested (e.g. by `somaxconn`); raise the OS limit too if needed.
- **`Connect_Timeout` default is no timeout.** Leaving it `0` is fine on healthy
  networks; set it where connects must not wait through the full retry sequence.
- **Buffer sizes are sharp.** Fixing `SndBuffSize`/`RcvBuffSize` wrong degrades
  throughput; prefer `0` (OS-managed) without measurements.

---

## Appendix A: Full parameter reference

### A.1 `TCP` stanza attributes (`qm.ini` / `mqclient.ini`)

| Attribute | Values / default | Meaning |
|---|---|---|
| `Port` | integer, default `1414` | Default TCP port for MQ sessions. |
| `KeepAlive` | `NO` (default) / `YES` | Turn on `SO_KEEPALIVE` so TCP periodically checks the peer is alive; closes the channel if not. Timing is OS-controlled (A.2). |
| `ListenerBacklog` | integer, default 100 (Linux) | Outstanding connection requests the listener may queue. Full backlog → connection rejected, channel `RETRY` / client `MQRC_Q_MGR_NOT_AVAILABLE`. |
| `Connect_Timeout` | `0` (default, no timeout) / seconds | Seconds MQ waits (over `select()`) for a nonblocking socket to become ready before connecting; `0` = rely on the 20-attempt retry. |
| `SndBuffSize` / `RcvBuffSize` | `0` (OS-managed, default) / bytes | TCP send/receive buffer sizes passed to the OS; MQ's fallback is 32768. Change with great care. |
| `SecureCommsOnly` | `NO` (default) / `YES` | `YES` refuses plaintext connections (info message at start); `NO` allows them (warning at start). |
| `Library1` | Windows only | Name of the TCP/IP sockets DLL (default `WSOCK32`). |

### A.2 OS keepalive timers (Linux sysctl)

| Setting | Meaning |
|---|---|
| `net.ipv4.tcp_keepalive_time` | Idle seconds before the first keepalive probe. |
| `net.ipv4.tcp_keepalive_intvl` | Seconds between probes. |
| `net.ipv4.tcp_keepalive_probes` | Unanswered probes before the connection is declared dead. |

Detection time ≈ `time + (intvl × probes)`. These govern the timing that
`KeepAlive=YES` merely enables.

## Appendix B: Alternatives and tradeoffs

### Dead-peer detection layers — complementary, not substitutes

- **TCP keepalive (this guide)** catches a silently dead or fenced peer at the
  socket layer, including a connection blocked mid-MQI-call. Its timing is OS-wide.
- **Channel keepalive/heartbeat (`KAINT`, `HBINT`)** are MQSC channel attributes.
  `HBINT` is the channel heartbeat; `KAINT` can set a per-channel keepalive
  interval that overrides the OS value for that channel (requires `KeepAlive=YES`
  in the `TCP` stanza). These give per-channel control on the queue-manager side,
  where the OS-wide sysctl is a blunter instrument. See the references.
- **Application-level reconnect** (client auto-reconnect) handles recovery once the
  dead connection is detected; it does not shorten detection itself.

A client blocked in an MQI call relies on its *own* socket's keepalive — which on
the client side is OS-governed — so client-heavy topologies tune the OS timers;
queue-manager-side channels can additionally use `KAINT`.

**Socket buffers** — `0` (OS auto-tuning) is the safe default; fixed sizes only
pay off with measured evidence on a specific network path.

## Appendix C: Complete configuration examples

**Queue manager — `qm.ini` `TCP` stanza:**

```ini
TCP:
   KeepAlive       = YES
   ListenerBacklog = 4096
   Connect_Timeout = 30
   SecureCommsOnly = YES
```

**Client — `mqclient.ini` `TCP` stanza:**

```ini
TCP:
   KeepAlive = YES
```

**Operating system — short keepalive timers (Linux sysctl):**

```ini
net.ipv4.tcp_keepalive_time  = 15
net.ipv4.tcp_keepalive_intvl = 5
net.ipv4.tcp_keepalive_probes = 3
```

## Appendix D: Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| A blocked MQI call hangs for a very long time after a peer is fenced | `KeepAlive=NO`, or `KeepAlive=YES` but OS keepalive still at the multi-hour default | set `KeepAlive=YES` **and** short OS keepalive timers (A.2) |
| Connections rejected / channels in `RETRY` under connection bursts | `ListenerBacklog` too low, or OS backlog cap (`somaxconn`) | raise `ListenerBacklog`, and the OS limit if needed |
| Connects hang or fail on a lossy / high-latency network | no connect timeout; relying on the full retry sequence | set `Connect_Timeout` to a non-zero value |
| Plaintext connections still accepted | `SecureCommsOnly=NO` | set `SecureCommsOnly=YES` (and configure TLS — Security guide) |
| Throughput dropped after buffer tuning | `SndBuffSize`/`RcvBuffSize` fixed to a poor value | revert to `0` (OS-managed) unless you have measurements |

## Appendix E: References

IBM MQ 9.4 documentation:

- *TCP stanza of the qm.ini file* —
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=qmini-tcp-stanza-file>
  (authoritative for `Port`, `KeepAlive`, `ListenerBacklog`, `Connect_Timeout`,
  `SndBuffSize`/`RcvBuffSize`, `SecureCommsOnly`).
- Search these topic titles at <https://www.ibm.com/docs/en/ibm-mq/9.4>:
  *Channel attributes — Keepalive Interval (KAINT)* and *Heartbeat interval
  (HBINT)*, for the per-channel keepalive/heartbeat levers.
- Your operating system's TCP keepalive documentation (`tcp_keepalive_time`,
  `tcp_keepalive_intvl`, `tcp_keepalive_probes` on Linux).
