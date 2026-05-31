# fastrecon benchmark suite

Reproducible head-to-head benchmarks of **fastrecon** vs **[datacompy]**,
**[data-diff]**, a hand-written **pandas merge** baseline, **PySpark**
(`local[*]`), **[Polars]**, and a hand-written **DuckDB SQL** full-outer-join
on synthetic datasets at 1M / 10M / 100M rows.

The goal is to back up fastrecon's pitch ("DuckDB+Arrow scales past
where datacompy dies and replaces the now-maintenance-mode data-diff")
with **numbers**, and to catch performance regressions in CI.

[datacompy]: https://github.com/capitalone/datacompy
[data-diff]: https://github.com/datafold/data-diff
[Polars]: https://github.com/pola-rs/polars

---

## Layout

```
benchmarks/
├── README.md                   ← you are here
├── datasets.py                 deterministic synthetic-dataset generator
├── result.py                   BenchResult dataclass (incl. DNF)
├── harness.py                  orchestrator: run_one() / run_matrix()
├── run_matrix.py               CLI: emit JSON + Markdown table
├── test_benchmarks.py          pytest-benchmark harness
├── setup_envs.sh               build .benchmarks_envs/<tool>/ venvs
├── adapters/                   uniform run() interface per tool
│   ├── _base.py                JSON-line subprocess protocol
│   ├── fastrecon_adapter.py
│   ├── datacompy_adapter.py
│   ├── datadiff_adapter.py
│   ├── pandas_merge_adapter.py
│   ├── pyspark_adapter.py
│   ├── polars_adapter.py
│   └── duckdb_sql_adapter.py
├── data/                       cached parquet fixtures (NOT committed —
│                               regenerated on demand by datasets.py)
└── results/                    only the smoke-tier table is committed;
    └── reference.md            pr/nightly/full tables live as CI artifacts
                                (`bench-pr-1m`, `bench-nightly`, `bench-full`).
```

---

## Scenarios

Each scenario produces a `(left.parquet, right.parquet)` pair plus a
seeded `GroundTruth` (`missing_in_left`, `missing_in_right`,
`changed_rows`) the harness checks each tool's output against.

| Scenario          | What it exercises                                                |
|-------------------|------------------------------------------------------------------|
| `all_match`       | Identical inputs — best case; measures pure scan + join cost.    |
| `small_mismatch`  | ~0.1% rows changed + a handful missing on each side.             |
| `large_mismatch`  | ~5% rows changed across multiple columns.                        |
| `precision_diff`  | Same rows, but every numeric/timestamp drifts by 1e-6 / 3 ms.   |

The shared schema mimics a typical warehouse fact table:

```
id          int64        primary key
customer_id int64        high-cardinality dim
region      string       4 distinct values, partition key
amount      float64
qty         int32
name        string
created_at  timestamp_ms
is_active   bool
```

Generation is **deterministic** (seeded by `(scenario, rows)`), and the
parquet output plus a sidecar `<scenario>_<rows>_ground_truth.json` is
cached in `benchmarks/data/`. Reruns reuse the cache.

---

## Scales (tiers)

| Tier      | Rows       | When it runs                                          |
|-----------|------------|-------------------------------------------------------|
| `smoke`   | 10,000     | Local sanity check; runs in seconds.                  |
| `pr`      | 1,000,000  | Per-PR regression gate (`.github/workflows/benchmarks.yml`). |
| `nightly` | + 10M      | Nightly schedule.                                     |
| `full`    | + 100M     | Manual `workflow_dispatch`. datacompy and pandas-merge are expected to DNF here. |

Cells that OOM, time out, or hit a missing dependency are recorded as
**DNF** (with reason) instead of crashing the rest of the matrix.

---

## Metrics per cell

| Metric          | Source                                                    |
|-----------------|-----------------------------------------------------------|
| `elapsed_sec`   | **median** of `time.perf_counter()` across `repeat` runs  |
| `median_sec` / `p95_sec` / `min_sec` / `max_sec` | summary stats over `samples_sec` |
| `samples_sec`   | every raw timing (one per repeat); kept in JSON output    |
| `peak_rss_bytes`| max of `resource.getrusage(RUSAGE_SELF).ru_maxrss`* across samples |
| `rows_per_sec`  | `rows / median_sec`                                       |
| `correct`       | adapter's reported counts vs `GroundTruth`                |

*`ru_maxrss` is in KiB on Linux and bytes on macOS — the harness
normalizes both to bytes.

### Statistical methodology — why median, not single-shot

CI runners are shared boxes: a noisy neighbour can add 10–20% to any
single timing, which is enough to drown a real 5% regression and to
turn a 5% win into a "look, 25% faster!" headline. To keep the
matrix-table numbers honest, `run_matrix.py` runs each
`(tool, scenario, scale)` cell **N times** and reports summary stats
instead of one sample:

| Tier      | Default `--repeat` | Why                                            |
|-----------|--------------------|------------------------------------------------|
| `smoke`   | 1                  | Local sanity check; cost matters more than spread. |
| `pr`      | 3                  | Per-PR gate; 3× wall time but stabilizes the median enough to catch ≥10% regressions. |
| `nightly` | 5                  | Has the budget; tighter p95.                   |
| `full`    | 5                  | Same.                                          |

Override per invocation with `--repeat N`.

What the cell shows is `median (p95) · RSS · r/s · ✓`:

* **Median** (not mean) is reported because a single GC pause or
  noisy-neighbour spike skews the mean a lot and the median almost
  not at all — and we care about typical performance, not average
  performance including outliers.
* **p95** is shown alongside so spread is visible at a glance: a cell
  that says `1.20s (p95 1.25s)` is rock-stable; one that says
  `1.20s (p95 4.50s)` is telling you the runner is pathological and
  the comparison is suspect. With low sample counts (the default
  `pr` tier uses N=3) p95 is necessarily coarse — under nearest-rank
  it lands on the **max** sample — so treat it as "worst observed",
  not a calibrated 95th percentile. Bump `--repeat` if you need a
  tighter bound.
* **`min` and `max`** plus **every raw sample** are kept in the JSON
  output (`samples_sec`) so downstream tooling can recompute whatever
  statistic it wants without rerunning.
* **Peak RSS** is the max across samples (worst-case footprint); rows/sec
  is derived from the median.

If a sample DNFs (timeout, OOM, adapter error) the cell short-circuits
to DNF — re-running a 60s timeout four more times burns CI minutes for
the same answer. For publication-quality numbers (tighter CI, more
samples, warmups) use the `pytest-benchmark` path instead.

---

## Isolated environments

Each tool runs in its **own virtualenv** under `.benchmarks_envs/<tool>/`
so transitive-dep conflicts (datacompy pins old pandas, data-diff pulls
SQLAlchemy 1.x, etc.) don't pollute results.

```bash
# Build all seven (fastrecon, datacompy, data-diff, pandas-merge, pyspark,
# polars, duckdb_sql)
bash benchmarks/setup_envs.sh

# Or build just one
bash benchmarks/setup_envs.sh datacompy
```

The harness auto-detects these envs by path
(`.benchmarks_envs/<tool>/bin/python`); if a venv is absent, it falls
back to the active interpreter so a quick local smoke test ("just run
fastrecon against itself") doesn't require provisioning all of them.
The `pyspark` adapter additionally needs a JRE/JDK on `PATH`; without
one its venv is built but every cell DNFs at SparkSession start.

---

## Running

### One-shot matrix

```bash
# 1M tier, all tools, all scenarios
PYTHONPATH=src:. python -m benchmarks.run_matrix --tier pr \
    --json benchmarks/results/pr.json \
    --markdown benchmarks/results/pr.md

# Just fastrecon at the smoke tier
PYTHONPATH=src:. python -m benchmarks.run_matrix --tier smoke --tools fastrecon
```

`run_matrix` exits with code **1** if any non-DNF run produced
incorrect counts (so CI fails on correctness regressions).

### Via pytest-benchmark

```bash
pip install pytest-benchmark psutil
PYTHONPATH=src:. pytest benchmarks/test_benchmarks.py \
    --benchmark-only --bench-tier pr
```

Tools that aren't installed in the active env appear as **skipped**
(DNF: MISSING_DEP), not failures.

### Pre-generating fixtures

Useful before timing runs so generation cost doesn't pollute the first
measurement:

```bash
python -m benchmarks.datasets --scenario small_mismatch --rows 1000000
```

---

## Reading the results

Cells are formatted as `wall · peak_rss · rows/sec · ✓/✗`. Compare
**column-wise** (within one tool, across scales) to check scaling, and
**row-wise** (within one scale, across tools) for the head-to-head story.
Correctness is **strict** — the tool's reported `(missing_in_left,
missing_in_right, changed_rows)` triple must equal the seeded ground
truth exactly. Tools that can only emit a single "differing rows" total
should mark themselves DNF (`UNSUPPORTED: cannot decompose`) rather
than guess; ✓/✗ is meant to be trustworthy.

Where to find each tier's results:

- `smoke` (10k): checked in at `benchmarks/results/reference.md`.
- `pr` (1M): downloaded from the `bench-pr-1m` artifact of the latest
  PR run.
- `nightly` (10M) / `full` (100M): downloaded from the `bench-nightly`
  / `bench-full` workflow artifacts.

Caveats baked into the methodology:

- Single-node, in-process — no warehouse / cluster benchmarks (out of
  scope; tracked in the cloud-connectors task).
- Synthetic data shape is one slice of reality (warehouse fact-table-ish).
  Tools may rank differently on wide string-heavy or deeply-nested data.
- `run_matrix.py` reports median + p95 across `--repeat` samples
  (default 3 for `pr`, 5 for `nightly` / `full`). For
  publication-quality numbers (warmups, tighter CIs, more iterations),
  run via `pytest-benchmark` instead.

## Scalability notes

- Fixture generation streams **inside DuckDB** (`COPY (SELECT ...) TO
  '...parquet'`) — no Python list materialization — so the 100M tier
  generates without OOM on the reference machine.
- All column values are derived from the row's `id` via `hash(id * P)`
  with distinct primes per column. That makes the output identical
  across processes (no Python `random` / hash-randomization) and lets
  ground-truth counts be computed by `COUNT(*)` over the same modular
  predicates rather than by simulating the generator.

---

## CI integration

`.github/workflows/benchmarks.yml` defines two jobs:

- **`pr-1m`** runs on every pull request, executes the `pr` tier,
  uploads `benchmarks/results/pr.json` + `pr.md` as an artifact, and
  fails the build if any tool reports incorrect counts.
- **`nightly-and-full`** runs on a nightly cron and on
  `workflow_dispatch` (with a `tier` input — `nightly` or `full`),
  uploads its results as a separate artifact.

Both jobs install dependencies into the per-tool venvs via
`benchmarks/setup_envs.sh` so each run reflects the latest published
versions of datacompy and data-diff.

### Per-sample spread summary

After the matrix runs, both jobs invoke `benchmarks/ci_spread_summary.py`
on the results JSON and print a per-cell `min .. p95` table directly
in the job log. This means reviewers can eyeball sample-to-sample noise
without downloading the artifact and digging through `samples_sec` by
hand. Cells whose `p95 / median` exceeds **1.5x** are flagged with a
GitHub `::warning::` annotation reading `NOISY (… — treat results with
caution)`, surfaced in the PR's "Files changed" / "Checks" view. The
flag is informational only — it never fails the build, since runner
noise isn't a code regression — but it tells reviewers when a
suspicious-looking number is more likely a noisy neighbour than a
real change. The full per-sample distribution remains in the uploaded
JSON artifact for offline analysis.
