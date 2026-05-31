#!/usr/bin/env bash
# Create per-tool isolated virtualenvs under .benchmarks_envs/ so the
# benchmark harness can run each tool in its own dependency space and
# avoid datacompy / data-diff / fastrecon fighting over transitive deps.
#
# Usage:
#     bash benchmarks/setup_envs.sh             # build all 7 envs
#                                               # (fastrecon, datacompy, datadiff,
#                                               #  pandas_merge, pyspark,
#                                               #  polars, duckdb_sql)
#     bash benchmarks/setup_envs.sh fastrecon   # build only one
#
# The harness in benchmarks/harness.py auto-detects these venvs by path:
# .benchmarks_envs/<tool>/bin/python.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENVS_DIR="${ROOT}/.benchmarks_envs"
PYTHON="${PYTHON:-python3}"

mkdir -p "${ENVS_DIR}"

declare -A REQS=(
  [fastrecon]="${ROOT}/."
  [datacompy]="datacompy>=0.13 pandas pyarrow"
  [datadiff]="data-diff[duckdb] duckdb sqlalchemy"
  [pandas_merge]="pandas pyarrow"
  [pyspark]="pyspark>=3.5 pandas pyarrow"
  [polars]="polars>=1.0 pyarrow"
  [duckdb_sql]="duckdb>=1.0 pyarrow"
)

build_one() {
  local name="$1"
  local target="${ENVS_DIR}/${name}"
  echo ">>> Building env: ${name} -> ${target}"
  "${PYTHON}" -m venv "${target}"
  "${target}/bin/pip" install --upgrade pip wheel setuptools
  # shellcheck disable=SC2086
  "${target}/bin/pip" install ${REQS[$name]}
  echo ">>> Done: ${name}"
}

if [[ $# -gt 0 ]]; then
  for n in "$@"; do build_one "$n"; done
else
  for n in fastrecon datacompy datadiff pandas_merge pyspark polars duckdb_sql; do build_one "$n"; done
fi
