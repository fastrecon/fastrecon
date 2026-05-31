# Benchmark dashboard

Static site that visualises historical results from the `benchmarks/` matrix.

## Layout

```
benchmarks/dashboard/
├── index.html          # the dashboard (Chart.js, no build step)
├── build_index.py      # rebuilds history/index.json from the JSON files
└── history/
    ├── index.json      # list of available runs (generated)
    └── 20260423T060000Z-nightly-abc1234.json   # one file per CI run
```

Each history file is the raw JSON that `python -m benchmarks.run_matrix --json …`
produces — the dashboard parses it client-side, no backend required.

## How CI publishes a run

`.github/workflows/benchmarks.yml` adds a `publish` step on the nightly /
`workflow_dispatch` job that:

1. Copies the run's `benchmarks/results/<tier>.json` into a checkout of the
   `gh-pages` branch as
   `benchmarks/dashboard/history/<utc-timestamp>-<tier>-<sha7>.json`.
2. Re-runs `python -m benchmarks.dashboard.build_index` to refresh
   `history/index.json`.
3. Commits & pushes back to `gh-pages`.

GitHub Pages then serves `benchmarks/dashboard/index.html`.

## Local preview

```bash
# generate a fake history entry from a local run
python -m benchmarks.run_matrix --tier smoke \
    --json benchmarks/dashboard/history/$(date -u +%Y%m%dT%H%M%SZ)-smoke-localdev.json

python -m benchmarks.dashboard.build_index \
    --history-dir benchmarks/dashboard/history

# serve it
python -m http.server -d benchmarks/dashboard 8000
# open http://localhost:8000
```

## Charts

One card per scenario at the selected scale. Each card plots the chosen metric
(wall time / peak RSS / throughput) over time, with one line per tool. DNF
runs (timeout, OOM, error, missing dep) appear as a red ✕ marker so they don't
silently disappear from the trend.
