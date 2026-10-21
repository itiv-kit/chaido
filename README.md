# CHAIDO

A simulation-based fault injector for the AMD Versal AI Engine (AIE-1) based on the aiesimulator provided by Vitis. Given a built single-tile kernel and a list of target storage
categories, the tool runs N single-bit injections and records each as `MASKED`, `SDC`, or `DUE`.

## Requirements

- Vitis 2024.2
- Python 3.6+ (standard library only).

## Quick start

```bash
cd example/gemm_fp32 && make          # build Work/ + the fault-free golden and profile
cd ../..
python3 src/chaido.py --config example/gemm_fp32/chaido/config --n 5
```

Results are saved in `example/gemm_fp32/chaido/results/results.tsv`.

## Parallel campaigns

`chaido.py` is sequential. To run several campaigns at once, wrap it:

```bash
python3 src/chaido_parallel.py --config example/gemm_fp32/chaido/config --jobs 4 --n 100
```

`--jobs J` launches J independent campaigns side by side and results are saved under `results/parallel/jobNN/`. Total injections are `J × n` and results are pooled under `results/parallel/all_results.tsv`. One `aiesimulator` instance runs per job.

## Output

`results.tsv`, one row per run:

```
ts  category  class  site  bit  pc  occ  cycle  caught  commit  watchdog  rc  status
```

`status` is one of:

- `MASKED`
- `SDC` — output differs from golden.
- `DUE` — timeout/hang.

A run whose fault never genuinely lands is re-drawn a few times; if it still doesn't, the row
instead carries a non-terminal status to discard (not an outcome): `NOTCAUGHT` (breakpoint never
hit), `NOCOMMIT` (the bit write didn't stick), `EMPTY` (no output), or `INFRA_ERROR` /
`POLL_BROKEN` (simulator or tool failure).

## Layout

```
src/                 chaido.py chaido_parallel.py strat_sample.py run_one.sh
                     inject_one.tcl build_dm_pool.sh dmscan_multi.tcl strata_pools.json
example/gemm_fp32/   src/ data/ Makefile + chaido/config
```

## License

MIT (`LICENSE`). The bundled `example/gemm_fp32` kernel (`kernel.cc`, `para_L0.h`) is from
CHARM (github.com/arc-research-lab/CHARM, MIT, (c) 2022 Jinming Zhuang); see
`example/gemm_fp32/THIRD_PARTY_NOTICES.txt`.

## Citation

Developed at the Institut für Technik der Informationsverarbeitung (ITIV), Karlsruhe Institute
of Technology (KIT). If you use CHAIDO, please cite the relevant paper (ASP-DAC 2027). The full citation details will be added once it is published.
