# Data Prepper DLQ (rejected documents)

Documents that OpenSearch rejects (a mapping conflict, an unparseable date) are not
dropped: Data Prepper's OpenSearch sink appends them to a local dead-letter file on
the obs node (#1239).

| What | Where |
| --- | --- |
| Live DLQ file | `/var/lib/data-prepper/dlq/opensearch-sink.dlq` (dir `0750`, owner `data-prepper`) |
| Rotated copies | `opensearch-sink.dlq.1.gz` … `.4.gz`, same dir |
| Rotation rule | `/etc/logrotate-data-prepper-dlq.conf` |
| Schedule | `data-prepper-dlq-rotate.timer` (hourly) → `data-prepper-dlq-rotate.service` |
| Pipeline setting | `dlq_file` in `ansible/roles/data-prepper/templates/pipelines.yaml.j2` |

## How the file behaves (Data Prepper 2.16.0)

These points come from the 2.16.0 source (data); the rotation choice follows from them.

- **Format: one record per line, not strict JSON.** Each record is written as
  `{"Document": [index {[<index>][<id>], source[<document JSON>]}], "failure": <message>}`.
  The `failure` message is OpenSearch's error reasons joined with ` caused by `
  (`ErrorCauseStringCreator`). It is not quoted, so a JSON parser rejects the line. The
  `source[...]` part is valid JSON: the document as Data Prepper sent it.
  Sources: `BulkIngester.logFailureForDlqObjects` and
  `BulkOperationWriter.dlqObjectToString`.
- **Opened once, held open, never reopened.** The sink opens the file at startup with
  `Files.newBufferedWriter(path, CREATE, APPEND)` (`BulkIngester.setupDlq`), closes it
  only at shutdown, and has no reopen or reload hook. That is why the rule uses
  **`copytruncate`**. Rename-and-create would leave Data Prepper writing into the
  renamed file, which would then grow without bound. Because the file is opened
  `O_APPEND`, writes after a truncate land at the new end of file. The cost: records
  written between the copy and the truncate are lost.
- **Buffered, not flushed per record.** The writer is never flushed after a record. A
  record reaches the file only once about 8 KiB has built up, or when Data Prepper
  stops (`systemctl stop`/`restart` closes the writer and flushes it). A single reject
  can therefore take a while to show up. Also, a rotation can split a record, leaving a
  partial line at the start of the live file.
- **Bounded only by logrotate.** The sink has no size limit of its own. The rule caps
  the live file at 100M and keeps 4 gzip'd rotations. The timer checks hourly because
  Ubuntu 24.04's stock `logrotate.timer` runs only daily, which cannot bound a burst.
  The reasoning for the caps is in the role defaults (`data_prepper_dlq_*`).

Source tree: [data-prepper 2.16.0, opensearch sink](https://github.com/opensearch-project/data-prepper/tree/2.16.0/data-prepper-plugins/opensearch/src/main/java/org/opensearch/dataprepper/plugins/sink/opensearch).
Option reference: [OpenSearch sink docs, `dlq_file`](https://docs.opensearch.org/latest/data-prepper/pipelines/configuration/sinks/opensearch/).

## Reading it

```bash
# On obs. Newest rejects, then a count by failure reason:
sudo tail -n 5 /var/lib/data-prepper/dlq/opensearch-sink.dlq
sudo sed -n 's/.*"failure": \(.*\)}$/\1/p' /var/lib/data-prepper/dlq/opensearch-sink.dlq \
  | cut -c1-120 | sort | uniq -c | sort -rn | head
# Rotated copies:
sudo zcat /var/lib/data-prepper/dlq/opensearch-sink.dlq.1.gz | tail -n 5
```

## Replaying it

Fix the cause first, usually the `logs-*` index template in the opensearch role.
Otherwise the replayed documents are rejected again. Then pull each `source[...]` JSON
out and bulk-index it into the record's original index. Lines that do not match the
pattern are skipped: the partial lines a rotation can leave behind.

```bash
sudo cat /var/lib/data-prepper/dlq/opensearch-sink.dlq | python3 -c '
import json, re, sys
rec = re.compile(r"^\{\"Document\": \[index \{\[(.*?)\]\[(.*?)\], source\[(.*)\]\}\], \"failure\": ")
for line in sys.stdin:
    m = rec.match(line)
    if not m:
        print("skipped partial line", file=sys.stderr)
        continue
    print(json.dumps({"index": {"_index": m.group(1)}}))
    print(json.dumps(json.loads(m.group(3))))
' > /tmp/dlq-replay.ndjson
curl -s -H 'Content-Type: application/x-ndjson' --data-binary @/tmp/dlq-replay.ndjson \
  'http://localhost:9200/_bulk' | python3 -c 'import json,sys; print("errors:", json.load(sys.stdin)["errors"])'
```

Replay is manual and deliberate. Nothing reads the DLQ automatically.
