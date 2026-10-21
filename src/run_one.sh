#!/bin/bash
# One injection run in debug mode, with the cycle-watchdog wired (EXPLINES/MAXCYC) so
# HANG / RUNAWAY / crash are detected. Prints inject_one.tcl's INJ| lines + a final RUN| line.
# Usage: run_one.sh <rundir> <KEY> <TILE> <PC> <OCC> <class> <site> <bit> <P1> <P2>
#                   <label> <outcopy> <explines> <maxcyc> <outrel> <inputdir>
set +u
EX="${1:?}"; KEY="${2:?}"; TILE="${3:?}"; PC="${4:?}"; OCC="${5:?}"; CLASS="${6:?}"; SITE="${7:?}"; BIT="${8:?}"
P1="${9:?}"; P2="${10:?}"; LABEL="${11:?}"; OUTCOPY="${12:?}"; EXPL="${13:-0}"; MAXCYC="${14:-0}"
OUTREL="${15:-aiesimulator_output/output.txt}"
INPUTDIR="${16:-data}"   ;# input dir, or "NONE" when the graph already prefixes data/
IDFLAG=""; [ "$INPUTDIR" != "NONE" ] && IDFLAG="--input-dir=$INPUTDIR"
HERE="$(cd "$(dirname "$0")" && pwd)"
INJDIR="$HERE"
WORK="$EX/Work"
OUTRAW="$EX/$OUTREL"; mkdir -p "$(dirname "$OUTRAW")"
if ! timeout 5 head -c1 /tools/xilinx/Vitis/2024.2/settings64.sh >/dev/null 2>&1; then echo "RUN| rc=97 ABORT_TOOLS"; exit 97; fi
source /tools/xilinx/Vitis/2024.2/settings64.sh >/dev/null 2>&1
AIESIM="$(command -v aiesimulator)"; XSDB="$(command -v xsdb)"
[ -x "$AIESIM" ] || { echo "RUN| rc=96 NO_AIESIM"; exit 96; }
# Free our ports, but only from a sim whose cwd is our own rundir (a leftover of a previous
# run on this slot). Never kill a listener we don't own, or we'd kill a concurrent run's sim.
port_owners() {
  ss -ltnp 2>/dev/null | grep -E ":$P1 |:$P2 " | grep -oE 'pid=[0-9]+' | grep -oE '[0-9]+' | sort -u
}
free_our_ports() {
  for p in $(port_owners); do
    owner=$(readlink /proc/$p/cwd 2>/dev/null)
    case "$owner" in
      "$EX"|"$EX"/*) kill -9 "$p" 2>/dev/null ;;                 # ours -> safe to kill
      *) echo "RUN| PORT_BUSY p=$p cwd=$owner (not ours; refusing to kill)" ;;
    esac
  done
}
free_our_ports
# Someone else holds our ports -> can't boot; fail fast as infra (port assignment is broken).
if [ -n "$(port_owners)" ]; then echo "RUN| rc=96 PORT_CONFLICT"; exit 96; fi
sleep 1; rm -f "$OUTRAW"
( cd "$EX" && "$AIESIM" --pkg-dir=Work $IDFLAG --profile --aximm-port=$P1 --rpc-server-port=$P2 \
    --aiesimulator-debug >/tmp/run_${LABEL}_$$.log 2>&1 ) &
SIMPID=$!
booted=0
for i in $(seq 1 180); do
  ss -ltn 2>/dev/null | grep -q ":$P1 " && ss -ltn 2>/dev/null | grep -q ":$P2 " && { booted=1; break; }
  kill -0 "$SIMPID" 2>/dev/null || { echo "RUN| rc=95 SIM_DIED_BOOT"; tail -6 /tmp/run_${LABEL}_$$.log | sed 's/^/RUN| /'; exit 95; }
  sleep 1
done
[ "$booted" = 1 ] || { echo "RUN| rc=94 BOOT_TIMEOUT"; kill -9 "$SIMPID" 2>/dev/null; exit 94; }
sleep 3
# drive the injection + watchdog (inject_one prints INJ| ... WATCHDOG <status> ... INJDONE)
( cd "$INJDIR" && timeout 300 "$XSDB" inject_one.tcl "$WORK" localhost:$P1 "$KEY" "$TILE" \
    "$PC" "$OCC" "$CLASS" "$SITE" "$BIT" "-" "$OUTRAW" "$EXPL" "$MAXCYC" ) 2>&1 | grep -E "INJ\|"
# wait for sim to finish (watchdog already decided; give it a bounded grace)
term=0
for i in $(seq 1 120); do kill -0 "$SIMPID" 2>/dev/null || { term=1; break; }; sleep 1; done
sleep 1
grep -vE '^[XT]' "$OUTRAW" 2>/dev/null > "$OUTCOPY"
OL=$(wc -l < "$OUTCOPY" 2>/dev/null || echo 0)
free_our_ports
kill -9 "$SIMPID" 2>/dev/null
echo "RUN| rc=0 term=$term outlines=$OL"
