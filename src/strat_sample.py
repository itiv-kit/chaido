#!/usr/bin/env python3
"""
strat_sample.py — draw injection coordinates for one stratum.

Each coordinate = a bit target x a cycle target:
  bit   : stratum bits laid end-to-end, drawn Uniform[0,N) (width-weighted per site), mapped
          back to (site, bit-in-site).
  cycle : drawn Uniform[0,kernel_cycles) weighted by the profile's per-PC cycles, mapped to a
          PC and occurrence j ~ Uniform[1, exe-count(PC)].
Registers -> class "iss" (inject by name); local DM -> class "dm" (inject at a device addr).
Pools: strata 1-9 from strata_pools.json; stratum 11 (DM) from dm_pool_<bench>.tsv.

Usage: strat_sample.py --pools p.json --dm dm_pool_<bench>.tsv --profile <p> --stratum 3 --n 663 --seed 42
Emits TSV: idx  stratum  class  site  bit  pc_hex  occurrence  cycle
"""
import argparse, re, sys, json, random, bisect

RUNTIME = ("main","memset","_GLOBAL","_init","_main_init","_fini","__cxa_finalize","_start","_clearbss")

def parse_functions(path):
    lines = open(path).readlines(); funcs=[]; i=0
    while i < len(lines):
        if not re.match(r'\s*Function detail:\s+(\S+)', lines[i]): i+=1; continue
        name = re.match(r'\s*Function detail:\s+(\S+)', lines[i]).group(1)
        low=None; j=i+1; sep=None
        while j < len(lines):
            lm=re.match(r'\s*Low PC\s*:\s*(\d+)', lines[j]);
            if lm: low=int(lm.group(1))
            if re.match(r'\s*-{6,}\s+-{6,}', lines[j]): sep=j; break
            if re.match(r'\s*Function detail:', lines[j]): break
            j+=1
        rows=[]
        if sep is not None:
            spans=[(m.start(),m.end()) for m in re.finditer(r'-+', lines[sep])]
            k=sep+1
            while k < len(lines):
                ln=lines[k].rstrip('\n')
                if ln.strip()=="" or re.match(r'\s*Function detail:', ln): break
                def col(x): s,e=spans[x]; return ln[s:e].strip()
                try:
                    pc=int(col(0)); exe=int(col(3)); cyc=int(col(4))
                    if exe>0: rows.append((pc,exe,cyc))
                except (ValueError,IndexError): pass
                k+=1
        funcs.append(dict(name=name, low_pc=low, rows=rows)); i=j
    return funcs

def pick_kernel(funcs):
    cand=[f for f in funcs if f["rows"]]
    nonrt=[f for f in cand if not any(r in f["name"] for r in RUNTIME)]
    return max(nonrt or cand, key=lambda f: sum(c for _,_,c in f["rows"]))

def load_pool(stratum, pools_json, dm_tsv):
    """Return list of (class, site, width) for the stratum."""
    if stratum == 11:                     # DM: sites come from the scanned dm_pool_<bench>.tsv
        out=[]
        if not dm_tsv: sys.exit("stratum 11 (DM) needs --dm")
        for ln in open(dm_tsv):
            ln=ln.split("#",1)[0].strip()
            if not ln: continue
            p=ln.split()
            if len(p)>=3 and p[0]=="dm": out.append(("dm", p[1], int(p[2])))
        return out
    pools=json.load(open(pools_json))
    return [("iss", site, int(w)) for site,w in pools.get(str(stratum), [])]

def build_cum(pool):
    cum=[]; acc=0
    for _,_,w in pool: acc+=w; cum.append(acc)
    return cum, acc

def draw_bit(pool, cum, total, rng):
    """1-to-N draw -> (class, site, bit). DM ranges map to (word device addr, bit%32)."""
    r = rng.randrange(total)
    i = bisect.bisect_right(cum, r)
    cls, site, w = pool[i]
    local = r - (cum[i]-w)
    if cls=="dm" and "-" in site:
        start,end = (int(x,16) for x in site.split("-"))
        word = start + (local//32)*4
        if word >= end: word = end-4
        return cls, f"0x{word:x}", local%32
    return cls, site, local % max(1,w)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--pools", required=True)
    ap.add_argument("--dm")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--stratum", type=int, required=True)
    ap.add_argument("--n", type=int, default=663)
    ap.add_argument("--seed", type=int, default=1)
    a=ap.parse_args()

    k=pick_kernel(parse_functions(a.profile)); rows=k["rows"]
    total_cyc=sum(c for _,_,c in rows)
    ccum=[]; acc=0
    for _,_,cyc in rows: acc+=cyc; ccum.append(acc)

    pool=load_pool(a.stratum, a.pools, a.dm)
    if not pool: sys.exit(f"empty pool for stratum {a.stratum}")
    bcum,total_bits=build_cum(pool)

    rng=random.Random(a.seed)
    for idx in range(a.n):
        cls,site,bit = draw_bit(pool, bcum, total_bits, rng)
        c=rng.randrange(total_cyc)
        lo,hi=0,len(ccum)-1
        while lo<hi:
            mid=(lo+hi)//2
            if c<ccum[mid]: hi=mid
            else: lo=mid+1
        pc,exe,_=rows[lo]
        occ=rng.randint(1,exe)
        print(f"{idx}\t{a.stratum}\t{cls}\t{site}\t{bit}\t0x{pc:x}\t{occ}\t{c}")

if __name__=="__main__":
    main()
