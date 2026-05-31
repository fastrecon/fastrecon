"""JUnit XML report for ``ReconResult`` — slots into CI dashboards.

Each compare run is one ``<testsuite>`` with one ``<testcase>``. If the
result is ``MISMATCH`` or ``ERROR`` the testcase contains a ``<failure>`` /
``<error>`` element. Per-partition results, when present, are emitted as
additional testcases so dashboards can pinpoint the failing slice.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET


def render_junit(result, suite_name: str = "fastrecon") -> str:
    suite = ET.Element("testsuite", {
        "name": suite_name,
        "tests": "1",
        "failures": "0",
        "errors": "0",
        "time": f"{result.execution_metrics.elapsed_sec:.3f}",
    })

    case = ET.SubElement(suite, "testcase", {
        "classname": f"fastrecon.{result.compare_mode}",
        "name": f"compare[{','.join(result.keys) or '*'}]",
        "time": f"{result.execution_metrics.elapsed_sec:.3f}",
    })

    if result.status == "ERROR":
        err = ET.SubElement(case, "error", {
            "type": "ReconError",
            "message": result.error or "compare failed",
        })
        err.text = result.error or ""
        suite.set("errors", "1")
    elif result.status == "MISMATCH":
        msg = (
            f"missing_in_left={result.missing_in_left}, "
            f"missing_in_right={result.missing_in_right}, "
            f"changed={result.changed_rows}, "
            f"dup_left={result.duplicate_keys_left}, "
            f"dup_right={result.duplicate_keys_right}"
        )
        f = ET.SubElement(case, "failure", {"type": "Mismatch", "message": msg})
        f.text = result.summary()
        suite.set("failures", "1")

    parts = result.column_stats.get("partitions") if result.column_stats else None
    if parts:
        n_extra_fail = 0
        for p in parts:
            tc = ET.SubElement(suite, "testcase", {
                "classname": f"fastrecon.partition.{result.compare_mode}",
                "name": f"partition[{p['partition']}]",
                "time": "0",
            })
            if not p["match"]:
                msg = (
                    f"missing_in_left={p['missing_in_left']}, "
                    f"missing_in_right={p['missing_in_right']}, "
                    f"changed={p['changed_rows']}"
                )
                ET.SubElement(tc, "failure", {"type": "Mismatch", "message": msg})
                n_extra_fail += 1
        suite.set("tests", str(1 + len(parts)))
        suite.set("failures", str(int(suite.get("failures", "0")) + n_extra_fail))

    suites = ET.Element("testsuites")
    suites.append(suite)
    ET.indent(suites, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(suites, encoding="unicode")
