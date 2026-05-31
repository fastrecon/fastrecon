"""HTML/JUnit/CLI/exit-code tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fastrecon import CsvFile, compare
from fastrecon.cli import main as cli_main


def _csvs(tmp_path: Path, with_diff: bool = True):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text("id,amount\n1,10\n2,20\n3,30\n")
    if with_diff:
        b.write_text("id,amount\n1,10\n2,21\n4,40\n")
    else:
        b.write_text("id,amount\n1,10\n2,20\n3,30\n")
    return a, b


def test_to_html_writes_self_contained_file(tmp_path: Path):
    a, b = _csvs(tmp_path)
    res = compare(CsvFile(str(a)), CsvFile(str(b)), keys=["id"])
    out = tmp_path / "r.html"
    html = res.to_html(str(out), title="My recon")
    assert out.exists()
    text = out.read_text()
    assert "<!doctype html>" in text
    assert "MISMATCH" in text
    assert "My recon" in text
    # Sample mismatches table should be present
    assert "Mismatch samples" in text
    # No external CDN refs — must be self-contained
    assert "http://" not in text and "https://" not in text


def test_to_junit_failure_on_mismatch(tmp_path: Path):
    a, b = _csvs(tmp_path)
    res = compare(CsvFile(str(a)), CsvFile(str(b)), keys=["id"])
    xml = res.to_junit()
    assert '<?xml version="1.0"' in xml
    assert "<failure" in xml
    assert "fastrecon.keyed" in xml
    assert res.exit_code == 1


def test_to_junit_passes_on_match(tmp_path: Path):
    a, b = _csvs(tmp_path, with_diff=False)
    res = compare(CsvFile(str(a)), CsvFile(str(b)), keys=["id"])
    xml = res.to_junit()
    assert "<failure" not in xml and "<error" not in xml
    assert res.exit_code == 0


def test_cli_writes_outputs_and_exits_nonzero_on_mismatch(tmp_path: Path):
    a, b = _csvs(tmp_path)
    html = tmp_path / "r.html"
    junit = tmp_path / "r.xml"
    js = tmp_path / "r.json"
    code = cli_main([
        "--left-type", "csv",  "--left-path",  str(a),
        "--right-type", "csv", "--right-path", str(b),
        "--keys", "id",
        "--html", str(html), "--junit", str(junit), "--json", str(js),
        "--quiet",
    ])
    assert code == 1
    assert html.exists() and junit.exists() and js.exists()
    payload = json.loads(js.read_text())
    assert payload["status"] == "MISMATCH"
    assert payload["changed_rows"] == 1


def test_cli_match_returns_zero(tmp_path: Path):
    a, b = _csvs(tmp_path, with_diff=False)
    code = cli_main([
        "--left-type", "csv", "--left-path", str(a),
        "--right-type", "csv", "--right-path", str(b),
        "--keys", "id", "--quiet",
    ])
    assert code == 0


def test_cli_partition_round_trip(tmp_path: Path):
    a = tmp_path / "a.csv"; b = tmp_path / "b.csv"
    a.write_text("id,region,v\n1,EU,1\n2,EU,2\n3,US,3\n4,US,4\n")
    b.write_text("id,region,v\n1,EU,1\n2,EU,99\n3,US,3\n4,US,4\n")
    junit = tmp_path / "p.xml"
    code = cli_main([
        "--left-type", "csv", "--left-path", str(a),
        "--right-type", "csv", "--right-path", str(b),
        "--keys", "id", "--partition", "region:value",
        "--junit", str(junit), "--quiet",
    ])
    assert code == 1
    xml = junit.read_text()
    # Per-partition testcases
    assert "partition[EU]" in xml and "partition[US]" in xml


def test_cli_typer_uri_form_and_multi_report(tmp_path: Path):
    """New typer-style invocation: --left URI, --right URI, --report fmt:path."""
    a, b = _csvs(tmp_path)
    html = tmp_path / "r.html"
    junit = tmp_path / "r.xml"
    js = tmp_path / "r.json"
    code = cli_main([
        "compare",
        "--left",  f"csv:{a}",
        "--right", f"csv:{b}",
        "--keys", "id",
        "--report", f"html:{html}",
        "--report", f"junit:{junit}",
        "--report", f"json:{js}",
        "--fail-on", "mismatch",
        "--quiet",
    ])
    assert code == 1
    assert html.exists() and junit.exists() and js.exists()
    assert "MISMATCH" in html.read_text()
    assert "<failure" in junit.read_text()
    assert json.loads(js.read_text())["status"] == "MISMATCH"


def test_cli_fail_on_never_returns_zero_even_on_mismatch(tmp_path: Path):
    a, b = _csvs(tmp_path)
    code = cli_main([
        "compare",
        "--left",  f"csv:{a}",
        "--right", f"csv:{b}",
        "--keys", "id",
        "--fail-on", "never",
        "--quiet",
    ])
    assert code == 0


def test_cli_repeated_keys_flag(tmp_path: Path):
    a = tmp_path / "a.csv"; b = tmp_path / "b.csv"
    a.write_text("k1,k2,v\nA,1,10\nB,2,20\n")
    b.write_text("k1,k2,v\nA,1,10\nB,2,99\n")
    code = cli_main([
        "compare",
        "--left",  f"csv:{a}",
        "--right", f"csv:{b}",
        "--keys", "k1", "--keys", "k2",
        "--quiet",
    ])
    assert code == 1


def test_cli_bad_report_format_exits_non_zero(tmp_path: Path):
    a, b = _csvs(tmp_path, with_diff=False)
    code = cli_main([
        "compare",
        "--left",  f"csv:{a}",
        "--right", f"csv:{b}",
        "--keys", "id",
        "--report", "pdf:/tmp/x.pdf",  # unsupported format
        "--quiet",
    ])
    assert code != 0


def test_html_report_contains_partition_heatmap_and_no_external_assets(tmp_path: Path):
    a = tmp_path / "a.csv"; b = tmp_path / "b.csv"
    a.write_text("id,region,v\n1,EU,1\n2,EU,2\n3,US,3\n4,US,4\n")
    b.write_text("id,region,v\n1,EU,1\n2,EU,99\n3,US,3\n4,US,4\n")
    out = tmp_path / "r.html"
    code = cli_main([
        "compare",
        "--left",  f"csv:{a}",
        "--right", f"csv:{b}",
        "--keys", "id",
        "--partition", "region:value",
        "--report", f"html:{out}",
        "--quiet",
    ])
    assert code == 1
    text = out.read_text()
    # CSS heatmap markers + still no external assets
    assert "hm-grid" in text and "hm-cell" in text
    assert "http://" not in text and "https://" not in text


def test_junit_xml_validates_against_basic_schema(tmp_path: Path):
    """Sanity-check the JUnit shape that CI dashboards expect:
    <testsuites><testsuite ...><testcase ...>...</testcase></testsuite></testsuites>
    plus tests/failures/errors counts as integers, classname & name attrs."""
    import xml.etree.ElementTree as ET
    a, b = _csvs(tmp_path)
    res = compare(CsvFile(str(a)), CsvFile(str(b)), keys=["id"])
    xml = res.to_junit()
    root = ET.fromstring(xml)
    assert root.tag == "testsuites"
    suites = root.findall("testsuite")
    assert len(suites) == 1
    s = suites[0]
    for attr in ("name", "tests", "failures", "errors", "time"):
        assert attr in s.attrib, f"missing testsuite attr: {attr}"
    int(s.attrib["tests"]); int(s.attrib["failures"]); int(s.attrib["errors"])
    cases = s.findall("testcase")
    assert cases, "expected at least one <testcase>"
    for c in cases:
        assert "classname" in c.attrib and "name" in c.attrib
    assert s.findall("testcase/failure"), "mismatch should produce <failure>"


def test_cli_invalid_fail_on_value_rejected(tmp_path: Path):
    a, b = _csvs(tmp_path, with_diff=False)
    code = cli_main([
        "compare",
        "--left",  f"csv:{a}",
        "--right", f"csv:{b}",
        "--keys", "id",
        "--fail-on", "bogus",
        "--quiet",
    ])
    assert code != 0


def test_cli_invalid_mode_value_rejected(tmp_path: Path):
    a, b = _csvs(tmp_path, with_diff=False)
    code = cli_main([
        "compare",
        "--left",  f"csv:{a}",
        "--right", f"csv:{b}",
        "--keys", "id",
        "--mode", "definitely-not-a-mode",
        "--quiet",
    ])
    assert code != 0


def test_cli_legacy_missing_companion_arg_rejected(tmp_path: Path):
    """Legacy --left-type csv without --left-path must error explicitly,
    matching the pre-typer 0.3.x behavior. No silent KeyErrors / TypeErrors."""
    a, _ = _csvs(tmp_path)
    code = cli_main([
        "compare",
        "--left-type", "csv",  # no --left-path!
        "--right-type", "csv", "--right-path", str(a),
        "--keys", "id",
        "--quiet",
    ])
    assert code != 0


def test_cli_legacy_postgres_requires_table_or_query(tmp_path: Path):
    a, _ = _csvs(tmp_path)
    code = cli_main([
        "compare",
        "--left-type", "postgres", "--left-conn", "postgresql://x/y",
        # no --left-table, no --left-query
        "--right-type", "csv", "--right-path", str(a),
        "--keys", "id",
        "--quiet",
    ])
    assert code != 0


def test_cli_verbose_emits_per_partition_metrics(tmp_path: Path, caplog):
    """With --verbose + --partition, each partition's metrics get logged."""
    import logging as _lg
    a = tmp_path / "a.csv"; b = tmp_path / "b.csv"
    a.write_text("id,region,v\n1,EU,1\n2,EU,2\n3,US,3\n4,US,4\n")
    b.write_text("id,region,v\n1,EU,1\n2,EU,99\n3,US,3\n4,US,4\n")
    with caplog.at_level(_lg.INFO, logger="fastrecon.cli"):
        code = cli_main([
            "compare",
            "--left",  f"csv:{a}",
            "--right", f"csv:{b}",
            "--keys", "id",
            "--partition", "region:value",
            "--verbose",
            "--quiet",
        ])
    assert code == 1
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "rows scanned" in text
    assert "partition 'EU'" in text and "partition 'US'" in text


def test_console_script_installed_or_module_runnable(tmp_path: Path):
    # Run via python -m fastrecon.cli to avoid relying on installed entry point
    a, b = _csvs(tmp_path, with_diff=False)
    proc = subprocess.run(
        [sys.executable, "-m", "fastrecon.cli",
         "--left-type", "csv", "--left-path", str(a),
         "--right-type", "csv", "--right-path", str(b),
         "--keys", "id", "--quiet"],
        env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
