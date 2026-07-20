#!/bin/bash
# =====================================================================
# Proof that the LAUNCHER IS UNNECESSARY: define amqsevt directly as the
# service STARTCMD with STDOUT -> the data file (no wrapper, no '>>').
# Confirm events append to the data file as JSON, survive a restart, and
# that amqsevt's stdout was opened O_APPEND. Runs as mqm on a RHEL node.
# =====================================================================
set -u
QM=EVTPROOF2
D=/var/mqm/stdout-proof2
OUT=$D/events.json
AMQSEVT=/opt/mqm/samp/bin/amqsevt

svc_pid() { echo "DISPLAY SVSTATUS(MQ.EVENT.MONITOR) PID" | runmqsc "$QM" 2>/dev/null | grep -oE 'PID\([0-9]+\)' | grep -oE '[0-9]+' | head -1; }

echo "################ SETUP ################"
rm -rf "$D"; mkdir -p "$D"
crtmqm "$QM" 2>&1 | tail -1
strmqm "$QM" 2>&1 | tail -1
# enable the 11 classes (NO LOGGEREV) — the corrected command
printf "ALTER QMGR AUTHOREV(ENABLED) CHADEV(ENABLED) CHLEV(ENABLED) CONFIGEV(ENABLED) INHIBTEV(ENABLED) LOCALEV(ENABLED) PERFMEV(ENABLED) REMOTEEV(ENABLED) SSLEV(ENABLED) STRSTPEV(ENABLED) CMDEV(NODISPLAY)\n" | runmqsc "$QM" 2>/dev/null | grep -iE 'AMQ8005|AMQ8[0-9]+E' | tail -1

echo; echo "################ LAUNCHER-LESS SERVICE: STARTCMD=amqsevt, STDOUT=data file ################"
printf "DEFINE SERVICE(MQ.EVENT.MONITOR) REPLACE CONTROL(QMGR) SERVTYPE(SERVER) STARTCMD('%s') STARTARG('-m +QMNAME+ -o json') STDOUT('%s') STOPCMD('/bin/kill') STOPARG('+MQ_SERVER_PID+') DESCR('amqsevt direct, no launcher')\n" "$AMQSEVT" "$OUT" | runmqsc "$QM" 2>/dev/null | grep -iE 'AMQ8625|AMQ8[0-9]+E' | tail -1
echo "START SERVICE(MQ.EVENT.MONITOR)" | runmqsc "$QM" 2>/dev/null | grep -iE 'AMQ8733|AMQ8[0-9]+E' | tail -1
sleep 3
P1=$(svc_pid); echo "collector PID = $P1  (process: $(tr '\0' ' ' < /proc/$P1/cmdline 2>/dev/null))"
echo "amqsevt stdout (fd1) open flags: $(awk '/^flags:/{print $2}' /proc/$P1/fdinfo/1 2>/dev/null)  (O_APPEND=02000)"

echo; echo "################ FORCE EVENT 1 (Not Authorized 2035) ################"
runuser -u nobody -- "$AMQSEVT" >/dev/null 2>&1 &   # nobody: unauthorized connect -> 2035 AUTHOREV
runuser -u nobody -- /opt/mqm/samp/bin/amqsput NOSUCHQ "$QM" >/dev/null 2>&1 || true
sleep 4
echo "data file after event 1:  $(stat -c 'inode=%i size=%s' "$OUT")"
echo "  eventType lines seen:"; grep -o '"eventType".*' "$OUT" 2>/dev/null | head -3 | sed 's/^/    /'
SZ1=$(stat -c '%s' "$OUT"); INO1=$(stat -c '%i' "$OUT")

echo; echo "################ RESTART, then FORCE EVENT 2 — does event 1 survive + event 2 append? ################"
echo "STOP SERVICE(MQ.EVENT.MONITOR)"  | runmqsc "$QM" 2>/dev/null | grep -iE 'AMQ8732' | tail -1
sleep 3
echo "data file right after STOP (unchanged?): $(stat -c 'inode=%i size=%s' "$OUT")"
echo "START SERVICE(MQ.EVENT.MONITOR)" | runmqsc "$QM" 2>/dev/null | grep -iE 'AMQ8733' | tail -1
sleep 3
runuser -u nobody -- /opt/mqm/samp/bin/amqsput NOSUCHQ2 "$QM" >/dev/null 2>&1 || true
sleep 4
echo "data file after restart + event 2: $(stat -c 'inode=%i size=%s' "$OUT")"
SZ2=$(stat -c '%s' "$OUT"); INO2=$(stat -c '%i' "$OUT")
echo "  total eventType occurrences in file: $(grep -c '"eventType"' "$OUT" 2>/dev/null)"
echo "  size grew $SZ1 -> $SZ2 ; inode $INO1 -> $INO2 (same inode + growth = appended across restart, event 1 retained)"

echo; echo "################ CLEANUP ################"
echo "STOP SERVICE(MQ.EVENT.MONITOR)" | runmqsc "$QM" >/dev/null 2>&1; sleep 2
endmqm -i "$QM" 2>&1 | tail -1
dltmqm "$QM" 2>&1 | tail -1
rm -rf "$D"
echo "cleanup done."
