"""pytest plugin hooks for the benchmark suite.

`pytest_addoption` lives here (rather than inside ``test_benchmarks.py``)
so the ``--bench-tier`` CLI flag is registered during pytest's plugin-loading
phase — before any test module is imported. Putting it in a test file works
only sometimes and is brittle across pytest versions; conftest.py is the
correct location.
"""


def pytest_addoption(parser):
    parser.addoption(
        "--bench-tier", action="store", default="smoke",
        choices=("smoke", "pr", "nightly", "full"),
        help="Benchmark scale tier (smoke=10k, pr=1M, nightly=10M, full=100M)",
    )
