# Reference benchmark results

## What is checked in vs. what runs in CI

| Tier      | Rows       | Where it lives                                              |
|-----------|------------|-------------------------------------------------------------|
| `smoke`   | 10,000     | **Checked in below.** Fast sanity check, all tools.         |
| `pr`      | 1,000,000  | CI artifact `bench-pr-1m` from `.github/workflows/benchmarks.yml`. |
| `nightly` | + 10M      | CI artifact `bench-nightly` from the nightly cron.          |
| `full`    | + 100M     | CI artifact `bench-full` from manual `workflow_dispatch`.   |

The 1M / 10M / 100M results aren't checked into the repo because they
require all seven tools' isolated venvs (`bash benchmarks/setup_envs.sh`)
and many minutes of compute — they're produced by CI and downloaded
from the latest workflow run. To reproduce locally:

```bash
bash benchmarks/setup_envs.sh
PYTHONPATH=src:. python -m benchmarks.run_matrix --tier pr \
    --json benchmarks/results/pr.json \
    --markdown benchmarks/results/pr.md
```

`run_matrix` exits 1 if any non-DNF cell disagrees with the seeded
ground truth, so the CI gate fails on correctness regressions.

## Tools in the matrix

| Tool           | Style                                              |
|----------------|----------------------------------------------------|
| `fastrecon`    | This project. Partitioned, in-process.             |
| `datacompy`    | Pandas-bound recon library; reads everything in.   |
| `data-diff`    | DB-side diff (we materialize into local DuckDB).   |
| `pandas-merge` | Hand-written `pd.merge(..., indicator=True)`.      |
| `pyspark`      | Spark `local[*]` full-outer-join; needs a JVM.     |
| `polars`       | Polars `df.join(..., how="full")` — multi-threaded, Arrow-native. |
| `duckdb-sql`   | Hand-written `FULL OUTER JOIN` SQL via DuckDB.     |

`pandas-merge` is the "I'll just do it myself" baseline most data
engineers reach for first; `polars` and `duckdb-sql` are the modern,
fast equivalents teams reach for when pandas gets too slow but they
don't want to pull in a recon-specific library; `pyspark` is the
heavyweight distributed option teams move to once tables outgrow
in-process tools. All are thin adapters under `benchmarks/adapters/`
and slot into the same harness as the recon-specific tools.

## Reference machine (for the smoke numbers below)

- CPU: 4-core x86_64
- RAM: 8 GiB
- Disk: SSD, local NVMe
- OS: Linux (NixOS container)
- Python: 3.11
- fastrecon: same commit as this file

## Smoke tier — 10,000 rows, all tools

| Scenario       | fastrecon                          | pandas-merge                       | polars                            | duckdb-sql                        | pyspark         |
|----------------|------------------------------------|------------------------------------|-----------------------------------|-----------------------------------|-----------------|
| all_match      | ~1.21s · ~133 MB · ~8.3k r/s · ✓  | ~1.31s · ~140 MB · ~7.6k r/s · ✓  | ~0.60s · ~74 MB · ~16.7k r/s · ✓  | ~0.46s · ~111 MB · ~21.7k r/s · ✓ | DNF (no JVM)   |
| small_mismatch | ~1.04s · ~136 MB · ~9.6k r/s · ✓  | ~0.88s · ~141 MB · ~11.4k r/s · ✓ | ~0.55s · ~75 MB · ~18.1k r/s · ✓  | ~0.44s · ~112 MB · ~23.0k r/s · ✓ | DNF (no JVM)   |
| large_mismatch | ~1.01s · ~134 MB · ~9.9k r/s · ✓  | ~0.88s · ~140 MB · ~11.4k r/s · ✓ | ~0.56s · ~75 MB · ~17.9k r/s · ✓  | ~0.42s · ~111 MB · ~23.7k r/s · ✓ | DNF (no JVM)   |
| precision_diff | ~0.97s · ~135 MB · ~10.3k r/s · ✓ | ~0.90s · ~140 MB · ~11.1k r/s · ✓ | ~0.54s · ~74 MB · ~18.5k r/s · ✓  | ~0.44s · ~112 MB · ~22.6k r/s · ✓ | DNF (no JVM)   |

_Cell format: wall time · peak RSS · throughput · correctness
(✓ = exact match against seeded ground-truth counts, ✗ = disagrees,
DNF = did not finish; the parenthetical is the reason)._

`datacompy` and `data-diff` aren't shown in the smoke table because
their isolated venvs aren't built on this reference machine; they
appear in the CI `bench-pr-1m` artifact alongside fastrecon,
pandas-merge, polars, duckdb-sql, and pyspark. `pyspark` shows DNF
here because no JRE/JDK is installed on the smoke machine — the CI
runner provides Java 17 via `actions/setup-java`, so the
`bench-pr-1m` artifact has real Spark numbers.

The smoke tier exists to verify the harness is wired correctly and
that each adapter's correctness math matches the seeded ground truth
— it's not meant for tool-vs-tool perf comparisons. At 10k rows the
fixed overhead of partitioned execution dominates fastrecon's wall
time, while a hand-tuned `duckdb-sql` or `polars` join finishes the
whole table in one vectorized pass; the picture flips well before
the 1M row PR tier, where fastrecon's streaming partitioning starts
paying off in peak RSS even when wall time is close. Look at the
`bench-pr-1m` workflow artifact for the actual seven-way comparison
numbers.
