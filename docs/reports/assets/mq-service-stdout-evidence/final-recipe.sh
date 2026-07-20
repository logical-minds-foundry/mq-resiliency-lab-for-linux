#!/bin/bash
# End-to-end proof of the EXACT how-to recipe: launcher-less service running
# amqsevt -o json_compact with STDOUT = data file. Confirm the file is JSONL
# (one JSON object per line) and appends across a restart. Run as mqm on RHEL.
set -u
QM=EVTFINAL; D=/var/mqm/final; OUT=$D/events.json; A=/opt/mqm/samp/bin/amqsevt
rm -rf "$D"; mkdir -p "$D"
crtmqm "$QM" 2>&1 | tail -1; strmqm "$QM" 2>&1 | tail -1
printf "ALTER QMGR AUTHOREV(ENABLED) CHADEV(ENABLED) CHLEV(ENABLED) CONFIGEV(ENABLED) INHIBTEV(ENABLED) LOCALEV(ENABLED) PERFMEV(ENABLED) REMOTEEV(ENABLED) SSLEV(ENABLED) STRSTPEV(ENABLED) CMDEV(NODISPLAY)\n" | runmqsc "$QM" 2>/dev/null | grep AMQ8005

echo "### DEFINE the EXACT recipe service (json_compact, STDOUT=file) ###"
printf "DEFINE SERVICE(MQ.EVENT.MONITOR) REPLACE CONTROL(QMGR) SERVTYPE(SERVER) STARTCMD('%s') STARTARG('-m +QMNAME+ -o json_compact') STDOUT('%s') STDERR('%s.err') STOPCMD('/bin/kill') STOPARG('+MQ_SERVER_PID+') DESCR('events JSONL to file')\n" "$A" "$OUT" "$OUT" | runmqsc "$QM" 2>/dev/null | grep AMQ8625
echo "START SERVICE(MQ.EVENT.MONITOR)" | runmqsc "$QM" 2>/dev/null | grep AMQ8733
sleep 2

echo "### force events, then inspect the FILE ###"
runuser -u nobody -- /opt/mqm/samp/bin/amqsput NOSUCHQ "$QM" 2>/dev/null || true
runuser -u nobody -- "$A" -m "$QM" 2>/dev/null || true
printf "DEFINE QLOCAL(EVT.Y)\nALTER QLOCAL(EVT.Y) DESCR('z')\n" | runmqsc "$QM" >/dev/null 2>&1
sleep 4
echo "file: $(stat -c 'inode=%i size=%s' "$OUT")  lines=$(wc -l < "$OUT")"
python3 - "$OUT" <<'PY'
import json,sys
n=bad=0
for line in open(sys.argv[1]):
    if not line.strip(): continue
    n+=1
    try: json.loads(line)
    except Exception: bad+=1
print(f"JSONL: {n} lines, {n-bad} valid JSON objects, {bad} bad -> {'ONE OBJECT PER LINE (JSONL) OK' if bad==0 and n>0 else 'FAIL'}")
PY
L1=$(wc -l < "$OUT")

echo "### restart, force more, confirm APPEND (retain L1 + grow) ###"
echo "STOP SERVICE(MQ.EVENT.MONITOR)" | runmqsc "$QM" >/dev/null 2>&1; sleep 3
echo "START SERVICE(MQ.EVENT.MONITOR)" | runmqsc "$QM" 2>/dev/null | grep AMQ8733; sleep 2
runuser -u nobody -- /opt/mqm/samp/bin/amqsput NOSUCHQ3 "$QM" 2>/dev/null || true
sleep 4
L2=$(wc -l < "$OUT")
echo "lines before restart=$L1  after restart+more=$L2  inode=$(stat -c %i "$OUT")  (grew, same inode = appended JSONL across restart)"
echo "--- sample: first 2 lines each a full object (truncated to 120c) ---"
head -2 "$OUT" | cut -c1-120 | sed 's/^/  /'

echo "### cleanup ###"
echo "STOP SERVICE(MQ.EVENT.MONITOR)" | runmqsc "$QM" >/dev/null 2>&1; sleep 2
endmqm -i "$QM" 2>&1 | tail -1; dltmqm "$QM" 2>&1 | tail -1; rm -rf "$D"; echo done
