#!/bin/bash
# Build the local-DM pool for one kernel = used-DM device ranges across the core tile and its
# neighbour memory tiles (from Map_Report). Boots one debug-mode sim, scans each used tile's DM
# for non-fill words, writes dm_pool_<bench>.tsv rows:  dm  <start>-<end>  <bits>.
# Usage: build_dm_pool.sh <kernel_dir> <bench_name> <P1> <P2> [out_dir]
set +u
EX0="${1:?example dir}"; BENCH="${2:?bench}"; P1="${3:-13001}"; P2="${4:-13002}"; OUTDIR="${5:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
EX0="$(cd "$EX0" && pwd)"
OUTDIR="${OUTDIR:-$HERE/results}"; mkdir -p "$OUTDIR"; OUTDIR="$(cd "$OUTDIR" && pwd)"
OUT="$OUTDIR/dm_pool_${BENCH}.tsv"
MAP="$EX0/Map_Report.csv"
[ -f "$MAP" ] || { echo "no Map_Report at $MAP"; exit 1; }
# ISOLATED rundir so we never clobber the benchmark's golden profile/output
EX="$OUTDIR/.dmpool_scratch_${BENCH}"
rm -rf "$EX"; mkdir -p "$EX/aiesimulator_output/data"
ln -sf "$EX0/Work" "$EX/Work"; ln -sf "$EX0/data" "$EX/data"
[ -f "$EX0/.AIE_SIM_CMD_LINE_OPTIONS" ] && ln -sf "$EX0/.AIE_SIM_CMD_LINE_OPTIONS" "$EX/.AIE_SIM_CMD_LINE_OPTIONS"
WORK="$EX/Work"

# --- used tiles from the linker map: distinct MEMORY_GROUP "(col, row)" -> candidate keys
# (col,row) and (col,row+1) to cover the CR-vs-debug-key row convention; scanner skips absent.
KEYS="$(python3 - "$MAP" <<'PY'
import sys, re
tiles=set()
for ln in open(sys.argv[1]):
    m=re.search(r'"\((\d+),\s*(\d+)\)"', ln)
    if m and (',' in ln) and any(t in ln for t in ('buf','sysmem')):
        tiles.add((int(m.group(1)), int(m.group(2))))
c=[]
for col,row in sorted(tiles):
    c.append(f"{col},{row}"); c.append(f"{col},{row+1}")
print(";".join(dict.fromkeys(c)))
PY
)"
echo "[$BENCH] used-tile candidate keys: $KEYS"

if ! timeout 5 head -c1 /tools/xilinx/Vitis/2024.2/settings64.sh >/dev/null 2>&1; then echo "ABORT /tools unresponsive"; exit 1; fi
source /tools/xilinx/Vitis/2024.2/settings64.sh >/dev/null 2>&1
AIESIM="$(command -v aiesimulator)"; XSDB="$(command -v xsdb)"
for p in $(ss -ltnp 2>/dev/null | grep -E ":$P1 |:$P2 " | grep -oE 'pid=[0-9]+' | grep -oE '[0-9]+' | sort -u); do kill -9 "$p" 2>/dev/null; done
sleep 1
( cd "$EX" && "$AIESIM" --pkg-dir=Work --input-dir=data --profile --aximm-port=$P1 --rpc-server-port=$P2 \
    --aiesimulator-debug >/tmp/dmpool_${BENCH}_$$.log 2>&1 ) &
SIMPID=$!
for i in $(seq 1 180); do ss -ltn 2>/dev/null | grep -q ":$P1 " && ss -ltn 2>/dev/null | grep -q ":$P2 " && break
  kill -0 "$SIMPID" 2>/dev/null || { echo "[$BENCH] SIM DIED at boot"; tail -8 /tmp/dmpool_${BENCH}_$$.log; exit 1; }; sleep 1; done
sleep 3
SCAN="$(cd "$HERE" && timeout 400 "$XSDB" dmscan_multi.tcl "$WORK" localhost:$P1 "$KEYS" 2>&1)"
echo "$SCAN" | grep -E "DM\|"
# emit pool: one dm row per RANGE
{ echo "# Local-DM pool for $BENCH (used tiles from Map_Report; raw bits, ECC ignored)"
  echo "# scanned keys reported by AVAILKEYS; ranges are live non-fill device addresses"
  echo "$SCAN" | awk '/RANGE/{printf "dm\t%s-%s\t%d\n",$4,$5,($6*32)}'
} > "$OUT"
NR=$(grep -c '^dm' "$OUT"); NB=$(awk -F'\t' '/^dm/{s+=$3}END{print s+0}' "$OUT")
echo "[$BENCH] wrote $OUT : $NR ranges, $NB bits"
for p in $(ss -ltnp 2>/dev/null | grep -E ":$P1 |:$P2 " | grep -oE 'pid=[0-9]+' | grep -oE '[0-9]+' | sort -u); do kill -9 "$p" 2>/dev/null; done
kill -9 "$SIMPID" 2>/dev/null
rm -rf "$EX"   # remove the DM-scan scratch dir; keep only dm_pool_<bench>.tsv
