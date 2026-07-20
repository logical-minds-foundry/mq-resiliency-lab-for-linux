#!/bin/bash
# =====================================================================
# MQ SERVICE STDOUT open-mode proof — append vs truncate on (re)start.
# Runs as mqm on a RHEL node. Throwaway QM. Minimal SERVER service whose
# STARTCMD writes ONE unique marker line to its stdout, then sleeps so we
# can read the process's open-flags. Two independent proofs:
#   (1) content persistence of a pre-seeded sentinel
#   (2) /proc/<pid>/fdinfo/1 open flags (O_APPEND = octal 02000)
# =====================================================================
set -u
QM=EVTPROOF
D=/var/mqm/stdout-proof
PROBE=$D/probe.sh
OUT=$D/probe.out

svc_pid() { echo "DISPLAY SVSTATUS(STDOUT.PROBE) PID" | runmqsc "$QM" 2>/dev/null | grep -oE 'PID\([0-9]+\)' | grep -oE '[0-9]+' | head -1; }
show_file() { echo "  stat: $(stat -c 'inode=%i size=%s' "$OUT" 2>&1)"; echo "  sha256: $(sha256sum "$OUT" 2>&1 | awk '{print $1}')"; echo "  --- content ---"; sed 's/^/  | /' "$OUT" 2>&1; echo "  --- end ---"; }

echo "################ ENVIRONMENT ################"
dspmqver -b -f 2 2>/dev/null || dspmqver 2>/dev/null | head -4
uname -sr

echo; echo "################ SETUP: throwaway QM $QM (default = circular logging) ################"
rm -rf "$D"; mkdir -p "$D"
crtmqm "$QM"  2>&1 | tail -1
strmqm "$QM"  2>&1 | tail -1
echo "DISPLAY QMSTATUS LOGTYPE" | runmqsc "$QM" 2>/dev/null | grep -io 'LOGTYPE([A-Z]*)'

cat > "$PROBE" <<'EOS'
#!/bin/bash
echo "RUN_MARKER pid=$$ nonce=${RANDOM}${RANDOM} ts=$(date +%s.%N)"
exec sleep 600
EOS
chmod 0755 "$PROBE"
echo "--- probe STARTCMD script (writes ONE marker line to stdout, then sleeps) ---"
sed 's/^/  | /' "$PROBE"

printf "DEFINE SERVICE(STDOUT.PROBE) REPLACE CONTROL(MANUAL) SERVTYPE(SERVER) STARTCMD('%s') STDOUT('%s') STOPCMD('/bin/kill') STOPARG('+MQ_SERVER_PID+') DESCR('stdout open-mode probe')\n" "$PROBE" "$OUT" | runmqsc "$QM" 2>/dev/null | grep -iE 'AMQ8[0-9]+' | tail -1

echo; echo "################ TRIAL 1: pre-seed a SENTINEL, then start the service ################"
printf 'SENTINEL_PRESEED_LINE_A\n' > "$OUT"
echo "[BEFORE start]"; show_file
echo "START SERVICE(STDOUT.PROBE)" | runmqsc "$QM" 2>/dev/null | grep -iE 'AMQ8[0-9]+' | tail -1
sleep 3
P1=$(svc_pid); echo "service PID = $P1"
echo "[AFTER start]"; show_file
echo "[OS-LEVEL PROOF — the flags MQ opened this stdout with]"
echo "  fd1 -> $(ls -l /proc/$P1/fd/1 2>&1 | sed 's#.*/proc#/proc#')"
echo "  fdinfo/1:"; sed 's/^/    /' /proc/$P1/fdinfo/1 2>&1
RAWFLAGS=$(awk '/^flags:/{print $2}' /proc/$P1/fdinfo/1 2>/dev/null)
echo "  raw open flags (octal) = $RAWFLAGS"
python3 - "$RAWFLAGS" <<'PY' 2>/dev/null || echo "  (python decode unavailable)"
import sys
f=int(sys.argv[1],8)
print("  O_APPEND(02000) set? ", "YES -> APPEND mode" if f & 0o2000 else "NO")
print("  O_WRONLY set?       ", bool(f & 0o1))
PY
echo ">>> TRIAL 1 READS: sentinel still present above => APPEND; sentinel gone => TRUNCATE"

echo; echo "################ TRIAL 2: RESTART (doc claim: 'truncates on every restart') ################"
echo "[file now holds sentinel + run-1 marker; STOP service]"
echo "STOP SERVICE(STDOUT.PROBE)" | runmqsc "$QM" 2>/dev/null | grep -iE 'AMQ8[0-9]+' | tail -1
sleep 3
echo "[BEFORE restart]"; show_file
echo "START SERVICE(STDOUT.PROBE)" | runmqsc "$QM" 2>/dev/null | grep -iE 'AMQ8[0-9]+' | tail -1
sleep 3
P2=$(svc_pid); echo "restarted PID = $P2"
echo "[AFTER restart]"; show_file
echo ">>> TRIAL 2 READS: TWO RUN_MARKER lines => APPEND across restart; ONE => TRUNCATE on restart"

echo; echo "################ CLEANUP ################"
echo "STOP SERVICE(STDOUT.PROBE)" | runmqsc "$QM" >/dev/null 2>&1
sleep 2
endmqm -i "$QM" 2>&1 | tail -1
dltmqm "$QM"  2>&1 | tail -1
rm -rf "$D"
echo "cleanup done."
