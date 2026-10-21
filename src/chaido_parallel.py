#!/usr/bin/env python3
"""
chaido_parallel.py — run several CHAIDO campaigns side by side.

Each job is an independent `chaido.py` campaign with its own RNG seed, its own TCP port
pair, and its own results folder. Pooling the jobs afterwards gives jobs x n injections
drawn from `jobs` independent Monte-Carlo streams — the statistically correct way to
parallelise a random fault-injection campaign.

One aiesimulator process runs per job, so keep `--jobs` at or below the cores (and RAM)
you can spare; this launcher does no load throttling.

Layout under <outdir> (default <config dir>/results/parallel):
    job00/results.tsv  job01/results.tsv  ...   one folder per job
    all_results.tsv                             every job's rows, pooled
    dm_pool_<name>.tsv                          shared DM pool (scanned once, if dm targeted)

Usage: python3 chaido_parallel.py --config <kernel>/chaido/config --jobs 4 [--n N] [--seed S]
"""
import argparse, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PORT_STRIDE = 10            # ports per job; each job uses portbase+j*STRIDE and +1

def load_config(path):
    """Parse a chaido config (key = value, with # comments) into a dict of strings."""
    cfg = {}
    for ln in open(path):
        ln = ln.split("#", 1)[0].strip()
        if "=" in ln:
            k, v = ln.split("=", 1); cfg[k.strip()] = v.strip()
    return cfg

def resolve(base, p):
    """Resolve p against base unless it is already absolute (mirrors chaido.py)."""
    p = os.path.expanduser(os.path.expandvars(p))
    return p if os.path.isabs(p) else os.path.abspath(os.path.join(base, p))

def pool_results(outdir, jobs):
    """Concatenate every job's results.tsv into all_results.tsv; print the combined tally."""
    merged = os.path.join(outdir, "all_results.tsv")
    header = None; rows = []; tally = {}
    for j in range(jobs):
        f = os.path.join(outdir, f"job{j:02d}", "results.tsv")
        if not os.path.exists(f): continue
        lines = open(f).read().splitlines()
        if not lines: continue
        header = lines[0]                                   # same schema in every job
        for ln in lines[1:]:
            rows.append(ln)
            cols = ln.split("\t")
            if len(cols) >= 13: tally[cols[12]] = tally.get(cols[12], 0) + 1   # status column
    if not header:
        print("[chaido-parallel] no results to pool"); return
    with open(merged, "w") as o:
        o.write(header + "\n" + ("\n".join(rows) + "\n" if rows else ""))
    print(f"[chaido-parallel] pooled {len(rows)} rows -> {merged}")
    print(f"[chaido-parallel] combined tally: {tally}")

def main():
    ap = argparse.ArgumentParser(description="run several CHAIDO campaigns in parallel")
    ap.add_argument("--config", required=True, help="the kernel's chaido/config (same file chaido.py takes)")
    ap.add_argument("--jobs", type=int, required=True, help="campaigns to run at once (one aiesimulator each)")
    ap.add_argument("--n", type=int, help="injections PER job (total = jobs x n); default from config")
    ap.add_argument("--seed", type=int, help="base seed; job j uses seed+j so streams differ (default from config)")
    ap.add_argument("--maxmult", type=float, help="DUE cycle ceiling multiple, passed to every job")
    ap.add_argument("--portbase", type=int, default=19001, help="first port; job j uses portbase+j*10 and +1 (default 19001)")
    ap.add_argument("--outdir", help="parent of the per-job folders (default <config dir>/results/parallel)")
    a = ap.parse_args()
    if a.jobs < 1: sys.exit("[chaido-parallel] --jobs must be >= 1")

    cfgpath = os.path.abspath(a.config)
    cdir = os.path.dirname(cfgpath)
    cfg = load_config(cfgpath)
    name = cfg.get("name", "kernel")
    cats = [c.strip().lower() for c in cfg.get("categories", "").split(",")]
    base_seed = a.seed if a.seed is not None else int(cfg.get("seed", 1))
    outdir = resolve(cdir, a.outdir) if a.outdir else resolve(cdir, "results/parallel")
    os.makedirs(outdir, exist_ok=True)

    # DM pool is scanned once and shared: without this, every job would boot a sim and scan
    # the same memory. Build it here, then hand each job --dm-pool so it skips the scan.
    dm_flag = []
    if "dm" in cats:
        dm_pool = os.path.join(outdir, f"dm_pool_{name}.tsv")
        if not os.path.exists(dm_pool):
            kernel_dir = resolve(cdir, cfg.get("kernel_dir", ".."))
            print(f"[chaido-parallel] building the shared DM pool once -> {dm_pool}")
            subprocess.run(["bash", os.path.join(HERE, "build_dm_pool.sh"),
                            kernel_dir, name, str(a.portbase), str(a.portbase + 1), outdir],
                           check=False)
        if os.path.exists(dm_pool): dm_flag = ["--dm-pool", dm_pool]
        else: print("[chaido-parallel] warning: shared DM pool missing; each job will scan its own")

    # Launch all jobs at once; each writes its own folder and captures its log there.
    procs = []
    for j in range(a.jobs):
        jdir = os.path.join(outdir, f"job{j:02d}"); os.makedirs(jdir, exist_ok=True)
        pb = a.portbase + j * PORT_STRIDE
        cmd = ["python3", os.path.join(HERE, "chaido.py"), "--config", cfgpath,
               "--seed", str(base_seed + j), "--portbase", str(pb),
               "--results", os.path.join(jdir, "results.tsv")] + dm_flag
        if a.n is not None: cmd += ["--n", str(a.n)]
        if a.maxmult is not None: cmd += ["--maxmult", str(a.maxmult)]
        log = open(os.path.join(jdir, "run.log"), "w")
        print(f"[chaido-parallel] job {j}: seed {base_seed + j}, ports {pb}/{pb + 1} -> {jdir}")
        procs.append((j, subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT), log))

    rc_all = 0
    for j, p, log in procs:
        rc = p.wait(); log.close()
        print(f"[chaido-parallel] job {j} finished (rc={rc}; see {outdir}/job{j:02d}/run.log)")
        if rc != 0: rc_all = 1

    pool_results(outdir, a.jobs)
    sys.exit(rc_all)

if __name__ == "__main__":
    main()
