
### 10,000 rows

| Scenario | duckdb-sql | fastrecon | pandas-merge | polars | pyspark |
|---|---|---|---|---|---|
| all_match | 0.46s (p95 0.46s) · 111 MB · 21,687 r/s · ✓ | 1.21s (p95 1.21s) · 133 MB · 8,295 r/s · ✓ | 1.31s (p95 1.31s) · 140 MB · 7,615 r/s · ✓ | 0.60s (p95 0.60s) · 74 MB · 16,670 r/s · ✓ | DNF (MISSING_DEP: No module named 'pyspark') |
| large_mismatch | 0.42s (p95 0.42s) · 111 MB · 23,728 r/s · ✓ | 1.01s (p95 1.01s) · 134 MB · 9,938 r/s · ✓ | 0.88s (p95 0.88s) · 140 MB · 11,378 r/s · ✓ | 0.56s (p95 0.56s) · 75 MB · 17,915 r/s · ✓ | DNF (MISSING_DEP: No module named 'pyspark') |
| precision_diff | 0.44s (p95 0.44s) · 112 MB · 22,596 r/s · ✓ | 0.97s (p95 0.97s) · 135 MB · 10,318 r/s · ✓ | 0.90s (p95 0.90s) · 140 MB · 11,133 r/s · ✓ | 0.54s (p95 0.54s) · 74 MB · 18,484 r/s · ✓ | DNF (MISSING_DEP: No module named 'pyspark') |
| small_mismatch | 0.44s (p95 0.44s) · 112 MB · 22,956 r/s · ✓ | 1.04s (p95 1.04s) · 136 MB · 9,642 r/s · ✓ | 0.88s (p95 0.88s) · 141 MB · 11,392 r/s · ✓ | 0.55s (p95 0.55s) · 75 MB · 18,063 r/s · ✓ | DNF (MISSING_DEP: No module named 'pyspark') |

_Cell format: median (p95) · peak RSS · throughput · correctness (✓ matches ground-truth counts, ✗ disagrees). Median is taken over the cell's repeated samples; raw timings are in the JSON output._
