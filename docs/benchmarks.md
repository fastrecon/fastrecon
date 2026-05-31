# Benchmarks

> _This is the docs-site "Benchmarks" page. fastrecon doesn't yet ship
> a generated docs site (mkdocs / sphinx); this Markdown file is the
> canonical home for the methodology and result links and will be
> picked up verbatim once a site is added._

fastrecon's pitch — "DuckDB+Arrow scales past where datacompy dies and
replaces the now-maintenance-mode data-diff" — is backed by a
reproducible benchmark suite that lives in the [`benchmarks/`](../benchmarks)
directory of the repository.

## What gets measured

For every `(tool × scenario × scale)` cell:

| Metric          | Source                                                        |
|-----------------|---------------------------------------------------------------|
| `elapsed_sec`   | `time.perf_counter()` around the adapter's `run()` call.      |
| `peak_rss_bytes`| `resource.getrusage(RUSAGE_SELF).ru_maxrss` (normalized).     |
| `rows_per_sec`  | `rows / elapsed_sec`.                                         |
| `correct`       | The tool's reported `(missing_in_left, missing_in_right, changed_rows)` triple, compared **strictly** against the seeded ground truth. |

A cell that OOMs, exceeds the per-cell timeout, or hits a missing
dependency is reported as **DNF** (with reason) — never as a silent
zero or a crash.

## Tools

| Tool          | Why it's in the comparison                                                                |
|---------------|-------------------------------------------------------------------------------------------|
| `fastrecon`   | The library this project ships. DuckDB+Arrow under the hood.                              |
| `datacompy`   | Capital One's pandas-bound recon library; the de-facto baseline.                          |
| `data-diff`   | Datafold's now-maintenance-mode in-DB differ.                                             |
| `pandas-merge`| Hand-rolled `pd.merge(..., indicator=True)` + per-row column diff — the "I'll just do it myself" baseline. |
| `pyspark`     | Spark `local[*]` full-outer join (eqNullSafe). Requires a JVM on `PATH`; CI installs Temurin 17 via `actions/setup-java`. |

Each tool runs in its own virtualenv (`.benchmarks_envs/<tool>/`) so
their (often conflicting) transitive dependencies don't pollute the
results. The benchmark harness shells out to each tool's interpreter
and parses a single JSON line of measurements from stdout.

## Scenarios

Four canonical reconciliation shapes, all sharing the same 8-column
schema (id, customer_id, region, amount, qty, name, created_at,
is_active):

| Scenario          | Description                                                    |
|-------------------|----------------------------------------------------------------|
| `all_match`       | Identical inputs; measures pure scan + join cost.              |
| `small_mismatch`  | ~0.1% rows changed + a handful missing on each side.           |
| `large_mismatch`  | ~5% rows changed across multiple columns.                      |
| `precision_diff`  | Same rows, but every numeric/timestamp drifts by 1e-6 / 3 ms. |

## Scales

| Tier      | Rows       | Frequency                                               |
|-----------|------------|---------------------------------------------------------|
| `smoke`   | 10,000     | Local only — sanity check.                              |
| `pr`      | 1,000,000  | Per pull request — regression gate.                     |
| `nightly` | + 10M      | Nightly cron.                                           |
| `full`    | + 100M     | Manual trigger; datacompy is expected to OOM here.     |

## Reading the results

- **Smoke (10k):** checked into [`benchmarks/results/reference.md`](../benchmarks/results/reference.md).
- **PR (1M):** download the `bench-pr-1m` artifact from the latest
  successful run of the
  [Benchmarks workflow](../.github/workflows/benchmarks.yml).
- **Nightly (10M):** download the `bench-nightly` artifact from the
  same workflow.
- **Full (100M):** download the `bench-full` artifact (manual
  `workflow_dispatch` only).

Cells are formatted as `wall · peak_rss · rows/sec · ✓/✗`. Compare
**column-wise** (within one tool, across scales) to assess scaling, and
**row-wise** (within one scale, across tools) for the head-to-head
story.

## Statistical methodology

Every cell in the matrix CLI is now run **multiple times**, with per-tier
defaults wired into `run_matrix`:

| Tier      | Repeats per cell | Reasoning                                              |
|-----------|------------------|--------------------------------------------------------|
| `smoke`   | 1                | Local sanity check; speed over rigor.                  |
| `pr`      | 3                | Cheap enough to keep PRs fast; enough to flag noise.   |
| `nightly` | 5                | More signal for the trend dashboard.                   |
| `full`    | 5                | Same as nightly; the dataset itself is the bottleneck. |

For each cell the JSON output contains the full `samples_sec` array plus
`median_sec`, `p95_sec`, `min_sec`, `max_sec`, and `repeat`. The
human-readable Markdown table renders cells as
`median (p95) · peak_rss · rows/sec · ✓` so spread is always visible at
a glance. We report the **median** rather than the mean because a single
GC pause or noisy neighbor on a CI runner shouldn't swing the headline
number.

Override the default with `--repeat N` if you need more samples for a
specific run.

### Per-sample spread summary in CI

After every matrix run, [`benchmarks/ci_spread_summary.py`](../benchmarks/ci_spread_summary.py)
prints a per-cell table of `n / min / median / p95 / max` to the job log
and emits a GitHub `::warning::` annotation for any cell whose
`p95/median` exceeds **1.5×** — those are flagged as `NOISY (treat
results with caution)`. The annotation is informational only; it never
fails the build, since runner noise isn't a code regression.

## Methodology caveats

- **Single-node, in-process.** No warehouse / cluster benchmarks; those
  depend on cloud connectors and have cost implications, and are
  tracked in a separate task.
- **Synthetic data shape is one slice of reality** — a warehouse-style
  fact table. Tools may rank differently on wide string-heavy or
  deeply-nested data.
- **Strict correctness gate.** A tool that can't decompose differences
  into the `(missing_in_left, missing_in_right, changed_rows)` triple
  must surface a DNF rather than guess; ✓/✗ in the table is meant to
  be trustworthy.

## Trend dashboard

Nightly runs publish their JSON into the `gh-pages` branch under
`benchmarks/dashboard/history/`, where a static Chart.js dashboard
renders one card per scenario at the selected scale. Switch metric
(wall time / peak RSS / throughput), scale, and tier via the dropdowns;
DNF runs are rendered as red ✕ markers on the x-axis so they stay
visible instead of silently disappearing as gaps. See
[`benchmarks/dashboard/README.md`](../benchmarks/dashboard/README.md)
for the publish flow and local-preview instructions.

## Reproducing locally

```bash
# 1. Build per-tool isolated envs (one-time)
bash benchmarks/setup_envs.sh

# 2. Run a tier and emit a JSON + Markdown report
PYTHONPATH=src:. python -m benchmarks.run_matrix --tier pr \
    --json benchmarks/results/pr.json \
    --markdown benchmarks/results/pr.md
```

The matrix exits with code **1** if any non-DNF cell disagrees with
the seeded ground truth — that's the safety net the PR job uses to
fail bad changes before they merge.
