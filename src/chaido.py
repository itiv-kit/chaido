#!/usr/bin/env python3
"""
chaido.py — minimal sequential single-bit fault injector for the AMD Versal AIE-1.

Point it at a built, fault-free kernel + its golden output + target categories. Runs N
injections sequentially; each draws a category (uniform), a bit in it (uniform), and a time
(PC+occurrence from the profile), flips the bit on the live aiesimulator (MemSock for DM,
chess-ISS RPC for registers), and classifies vs golden as MASKED / SDC / DUE. A fault that
did not land is re-drawn, so each of the N is a genuine injection; one row per run is written.

Usage:  python3 chaido.py --config <kernel>/chaido/config  [--n N] [--seed S]
"""
import argparse, subprocess, os, sys, time, random, re, shutil
import strat_sample as ss

# category name -> stratum id in strata_pools.json (DM=11 comes from the scanned DM pool).
CATS = {"vector": 1, "accumulator": 2, "scalar": 3, "addrgen": 4, "config": 5,
        "circbuf": 6, "controlflow": 7, "status": 8, "impldef": 9, "dm": 11}
NUM2CAT = {v: k for k, v in CATS.items()}
REAL = {"MASKED", "SDC", "DUE"}
ALL_CATS = "vector,accumulator,scalar,addrgen,config,circbuf,controlflow,status,impldef,dm"

_TS_RE = re.compile(r'^\s*[-+]?[\d.]+\s*(ps|ns|us|ms)\s*$', re.I)
def _clean(lines):
    # drop X/T stream markers + bare timestamps so a timing-only change isn't a false SDC
    return [l for l in lines if l[:1] not in ("X", "T") and not _TS_RE.match(l)]

def classify(outcopy, golden_lines, watchdog, rc, caught, commit):
    if rc in (94, 95, 96, 97):    return "INFRA_ERROR"
    if caught is not True:        return "NOTCAUGHT"
    if commit != 1:               return "NOCOMMIT"
    if watchdog == "RUNAWAY":     return "DUE"            # non-termination (cycle ceiling)
    if watchdog == "HANG":        return "DUE"            # stuck after a committed fault
    if watchdog == "POLL_BROKEN": return "INFRA_ERROR"
    try:
        got = _clean(open(outcopy).read().splitlines())
    except OSError:
        return "EMPTY"
    if not got:                   return "EMPTY"
    return "MASKED" if got == golden_lines else "SDC"

def parse_run(text):
    caught = commit = watchdog = None; rc = 0
    for ln in text.splitlines():
        if "NOTCAUGHT" in ln: caught = False
        m = re.search(r"caught (\d+)/(\d+)", ln)
        if m: caught = (m.group(1) == m.group(2) and int(m.group(2)) > 0)
        m = re.search(r"commit=(\d)", ln)
        if m: commit = int(m.group(1))
        if "NOCOMMIT" in ln: commit = 0
        m = re.search(r"WATCHDOG (\w+)", ln)
        if m: watchdog = m.group(1)
        m = re.search(r"RUN\| rc=(\d+)", ln)
        if m: rc = int(m.group(1))
    if commit is None and caught: commit = 1
    return caught, commit, watchdog, rc

def setup_rundir(rundir, ex):
    os.makedirs(os.path.join(rundir, "aiesimulator_output", "data"), exist_ok=True)
    for name in ("Work", "data", ".AIE_SIM_CMD_LINE_OPTIONS"):
        dst = os.path.join(rundir, name); src = os.path.join(ex, name)
        if not os.path.exists(src): continue
        if os.path.lexists(dst): os.remove(dst)
        os.symlink(src, dst)

def load_config(path):
    cfg = {}
    for ln in open(path):
        ln = ln.split("#", 1)[0].strip()
        if "=" in ln:
            k, v = ln.split("=", 1); cfg[k.strip()] = v.strip()
    return cfg

def resolve(base, p):
    p = os.path.expanduser(os.path.expandvars(p))
    return p if os.path.isabs(p) else os.path.abspath(os.path.join(base, p))

def derive_tile(adf):
    # ADF tile (col,row) -> device tile (row+1; shim is row 0)
    col, row = (int(x) for x in adf.replace(" ", "").split(","))
    return f"{col},{row+1}", f"{col}_{row+1}"

def cats_to_strata(s):
    out = []
    for tok in s.replace(" ", "").split(","):
        if not tok: continue
        if tok.isdigit(): out.append(int(tok))
        elif tok.lower() in CATS: out.append(CATS[tok.lower()])
        else: sys.exit(f"[chaido] unknown category '{tok}' (use {sorted(CATS)} or stratum numbers)")
    return out

def main():
    ap = argparse.ArgumentParser(description="minimal sequential AIE-1 single-bit fault injector")
    ap.add_argument("--config", required=True, help="path to a kernel's chaido/config (built kernel + golden + categories)")
    ap.add_argument("--n", type=int, help="number of injections (overrides config)")
    ap.add_argument("--seed", type=int, help="RNG seed (overrides config)")
    ap.add_argument("--maxmult", type=float, help="DUE cycle ceiling as a multiple of fault-free cycles (overrides config; default 8)")
    ap.add_argument("--portbase", type=int, help="first TCP port; uses portbase and portbase+1 (overrides config)")
    ap.add_argument("--results", help="results .tsv path (overrides config; its folder also holds the run scratch and DM pool)")
    ap.add_argument("--dm-pool", dest="dm_pool", help="path to a prebuilt dm_pool_<name>.tsv to reuse (skips the one-time DM scan)")
    a = ap.parse_args()

    HERE = os.path.dirname(os.path.abspath(__file__))
    cdir = os.path.dirname(os.path.abspath(a.config))
    cfg = load_config(a.config)

    name     = cfg.get("name", "kernel")
    ex       = resolve(cdir, cfg["kernel_dir"])
    golden   = resolve(cdir, cfg["golden"])
    profile  = resolve(cdir, cfg["profile"])
    key, tile = derive_tile(cfg["inject_tile"])
    outrel   = cfg.get("output_file", "aiesimulator_output/output.txt")
    inputdir = cfg.get("input_dir", "data")
    strata   = cats_to_strata(cfg.get("categories", ALL_CATS))
    N        = a.n if a.n is not None else int(cfg.get("n", 100))
    seed     = a.seed if a.seed is not None else int(cfg.get("seed", 1))
    maxmult  = a.maxmult if a.maxmult is not None else float(cfg.get("maxmult", 8))
    portbase = a.portbase if a.portbase is not None else int(cfg.get("portbase", 19001))
    ecc      = str(cfg.get("ecc", "false")).lower() in ("1", "true", "yes", "on")
    pools    = resolve(HERE, "strata_pools.json")
    results  = resolve(cdir, a.results if a.results else cfg.get("results", "results/results.tsv"))
    out_dir  = os.path.dirname(results) or "."
    os.makedirs(out_dir, exist_ok=True)

    if not os.path.exists(os.path.join(ex, "Work")):
        sys.exit(f"[chaido] {ex}/Work not found — build the kernel (make aie aiesim) first")

    golden_lines = _clean(open(golden).read().splitlines())
    expl = len(golden_lines)

    # cycle window from the profile — needs the per-PC profile_instr_<tile>.txt, not the summary.
    funcs = ss.parse_functions(profile)
    if not any(f["rows"] for f in funcs):
        sys.exit(f"[chaido] profile '{profile}' has no per-PC function details.\n"
                 f"         Point `profile` at the profile_instr_<tile>.txt from your fault-free\n"
                 f"         '--profile' run (the profile_funct_*.txt summary does not contain them).")
    k = ss.pick_kernel(funcs); rows = k["rows"]
    total_cyc = sum(c for _, _, c in rows); maxcyc = int(total_cyc * maxmult)
    ccum = []; acc = 0
    for _, _, cyc in rows: acc += cyc; ccum.append(acc)

    # DM pool: enumerate the used DM ranges once (needs Work/) if DM is a target.
    dm_tsv = resolve(cdir, a.dm_pool) if a.dm_pool else os.path.join(out_dir, f"dm_pool_{name}.tsv")
    if 11 in strata and not os.path.exists(dm_tsv):
        print("[chaido] enumerating the DM pool (one scan)…")
        subprocess.run(["bash", os.path.join(HERE, "build_dm_pool.sh"), ex, name,
                        str(portbase), str(portbase + 1), out_dir], check=False)

    cat_pool = {}
    for s in strata:
        pool = ss.load_pool(s, pools, dm_tsv if s == 11 else None)
        if not pool:
            print(f"[chaido] note: empty pool for category {s} — skipping"); continue
        cat_pool[s] = (pool,) + ss.build_cum(pool)      # (pool, cum, total_bits)
    strata = [s for s in strata if s in cat_pool]
    if not strata: sys.exit("[chaido] no non-empty target categories")

    rundir = os.path.join(out_dir, f"_run_{portbase}")
    setup_rundir(rundir, ex)
    if not os.path.exists(results):
        open(results, "w").write("ts\tcategory\tclass\tsite\tbit\tpc\tocc\tcycle\t"
                                 "caught\tcommit\twatchdog\trc\tstatus\n")

    rng = random.Random(seed); tally = {}
    print(f"[chaido] {N} sequential injections | categories "
          f"{[NUM2CAT.get(s, s) for s in strata]} | seed {seed} | -> {results}")
    for i in range(N):
        status = "INFRA_ERROR"; row = None
        for _ in range(6):                               # re-draw until the fault genuinely lands
            s = rng.choice(strata)                       # 1) category, uniform
            pool, cum, tbits = cat_pool[s]
            cls, site, bit = ss.draw_bit(pool, cum, tbits, rng)   # 2) bit, uniform
            c = rng.randrange(total_cyc)                 # 3) injection time
            lo, hi = 0, len(ccum) - 1
            while lo < hi:
                mid = (lo + hi) // 2
                if c < ccum[mid]: hi = mid
                else: lo = mid + 1
            pc, exe, _ = rows[lo]; pc_hex = f"0x{pc:x}"; occ = rng.randint(1, exe)
            # ECC: DM banks 0-1 (offset <0x2000) are SECDED-corrected; score such flips MASKED.
            if ecc and s == 11 and (int(site, 16) & 0x7FFF) < 0x2000:
                caught, commit, watchdog, rc, status = True, 1, "ECC", 0, "MASKED"
            else:
                outcopy = os.path.join(rundir, "outcopy.txt")
                try:
                    r = subprocess.run(
                        ["bash", os.path.join(HERE, "run_one.sh"), rundir, key, tile, pc_hex,
                         str(occ), cls, site, str(bit), str(portbase), str(portbase + 1),
                         f"chaido{i}", outcopy, str(expl), str(maxcyc), outrel, inputdir],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        universal_newlines=True, timeout=900)
                    caught, commit, watchdog, rc = parse_run(r.stdout or "")
                    status = classify(outcopy, golden_lines, watchdog, rc, caught, commit)
                except subprocess.TimeoutExpired:
                    caught, commit, watchdog, rc, status = None, None, "HANG", -1, "DUE"
                except Exception:
                    caught, commit, watchdog, rc, status = None, None, "exc", -1, "INFRA_ERROR"
            row = [time.strftime("%Y-%m-%dT%H:%M:%S"), NUM2CAT.get(s, s), cls, site, bit, pc_hex, occ, c,
                   caught, commit, watchdog, rc, status]
            if status in REAL: break
        tally[status] = tally.get(status, 0) + 1
        with open(results, "a") as f:
            f.write("\t".join(str(x) for x in row) + "\n")
        print(f"  [{i + 1}/{N}] {str(row[1]):11} {row[2]:3} {row[3]} bit{row[4]} -> {status}")
    print(f"[chaido] done — {tally}")
    shutil.rmtree(rundir, ignore_errors=True)

if __name__ == "__main__":
    main()
