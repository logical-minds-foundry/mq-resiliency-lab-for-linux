#!/bin/bash
# Verify amqsevt compact-JSON output is one JSON object per line (JSONL).
# Runs as mqm on a RHEL node. Throwaway QM.
set -u
QM=EVTCOMPACT; D=/var/mqm/compact; A=/opt/mqm/samp/bin/amqsevt
rm -rf "$D"; mkdir -p "$D"

echo "################ amqsevt valid -o formats (usage) ################"
"$A" -o __bogus__ -m NOSUCHQM 2>&1 | grep -iE 'format|json|usage|valid|-o' | head -10
echo "(also grepping the binary's help text if present)"
"$A" 2>&1 | grep -iE '\-o .*format|json' | head -5

echo; echo "################ SETUP throwaway QM ################"
crtmqm "$QM" 2>&1 | tail -1; strmqm "$QM" 2>&1 | tail -1
printf "ALTER QMGR AUTHOREV(ENABLED) CONFIGEV(ENABLED) LOCALEV(ENABLED) CMDEV(NODISPLAY)\n" | runmqsc "$QM" 2>/dev/null | grep AMQ8005

echo; echo "################ generate a few events on the event queues ################"
runuser -u nobody -- "$A" -m "$QM" 2>/dev/null || true                      # 2035 connect
runuser -u nobody -- /opt/mqm/samp/bin/amqsput NOSUCHQ "$QM" 2>/dev/null || true   # 2035
printf "DEFINE QLOCAL(EVT.X)\nALTER QLOCAL(EVT.X) DESCR('a')\n" | runmqsc "$QM" 2>/dev/null | grep -c AMQ8 >/dev/null
sleep 2

for FMT in json_compact jsoncompact compact json0; do
  echo; echo "################ TRY: amqsevt -o $FMT (browse, non-destructive) ################"
  OUT="$D/$FMT.out"
  "$A" -m "$QM" -o "$FMT" -b -w 3 > "$OUT" 2>"$D/$FMT.err"
  RC=$?
  if grep -qiE 'not valid|invalid|usage|unknown' "$D/$FMT.err" || [ ! -s "$OUT" ]; then
    echo "  -> '$FMT' rejected or empty. stderr:"; sed 's/^/     /' "$D/$FMT.err" | head -3
    continue
  fi
  echo "  bytes=$(stat -c %s "$OUT")  lines=$(wc -l < "$OUT")"
  echo "  JSONL check (each non-empty line must parse as one JSON object):"
  python3 - "$OUT" <<'PY'
import json,sys
p=sys.argv[1]; n=0; bad=0
for line in open(p):
    if not line.strip(): continue
    n+=1
    try: json.loads(line)
    except Exception as e:
        bad+=1
        if bad<=2: print("     BAD LINE:", str(e)[:60])
print(f"     lines={n} valid_json_lines={n-bad} bad={bad} -> {'JSONL OK (one object per line)' if bad==0 and n>0 else 'NOT JSONL'}")
PY
  echo "  first line (truncated):"; head -1 "$OUT" | cut -c1-160 | sed 's/^/     /'
done

echo; echo "################ contrast: default -o json (multi-line) line count ################"
"$A" -m "$QM" -o json -b -w 3 > "$D/json.out" 2>/dev/null
echo "  -o json:  bytes=$(stat -c %s "$D/json.out")  lines=$(wc -l < "$D/json.out")  (many lines per event = pretty-printed)"

echo; echo "################ CLEANUP ################"
endmqm -i "$QM" 2>&1 | tail -1; dltmqm "$QM" 2>&1 | tail -1; rm -rf "$D"; echo done
