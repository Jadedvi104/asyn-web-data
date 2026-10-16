from __future__ import annotations

import csv
import json

from webcollect.cli import main
from webcollect.storage import Store


def invoke_json(capsys, args, *, expected_exit=0):
    assert main(args) == expected_exit
    captured = capsys.readouterr()
    return json.loads(captured.out)


def test_cli_crawl_status_export_and_idempotent_resume(local_site, tmp_path, capsys):
    database = str(tmp_path / "collector.db")
    summary = invoke_json(capsys, [
        "--db", database, "crawl", "--seed", local_site.url("/page/1"),
        "--allow-domain", "127.0.0.1", "--depth", "0", "--rps", "1000",
    ])
    job_id = summary["job_id"]
    assert summary["pages"] == 1
    assert summary["status"] == "completed"
    status = invoke_json(capsys, ["--db", database, "status", "--job", job_id])
    assert status["counts"]["completed"] == 1
    assert status["total_requests"] >= 1
    assert status["bytes_received"] > 0
    assert status["http_statuses"]["200"] >= 1
    assert status["elapsed_seconds"] > 0
    assert status["throughput_pages_per_second"] > 0

    jsonl_path = tmp_path / "pages.jsonl"
    exported = invoke_json(capsys, [
        "--db", database, "export", "--job", job_id, "--format", "jsonl",
        "--output", str(jsonl_path),
    ])
    assert exported["pages"] == 1
    results = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert results[0]["url"] == local_site.url("/page/1")
    assert results[0]["title"] == "Fixture page"
    assert isinstance(results[0]["outbound_links"], list)

    csv_path = tmp_path / "pages.csv"
    invoke_json(capsys, [
        "--db", database, "export", "--job", job_id, "--format", "csv",
        "--output", str(csv_path),
    ])
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["url"] == results[0]["url"]
    assert rows[0]["title"] == results[0]["title"]
    assert json.loads(rows[0]["outbound_links"]) == results[0]["outbound_links"]

    resumed = invoke_json(capsys, ["--db", database, "resume", "--job", job_id])
    assert resumed["pages"] == 1
    assert local_site.count("/page/1") == 1


def test_cli_robots_decision_and_terminal_failure_exit_codes(local_site, tmp_path, capsys):
    decision = invoke_json(capsys, ["check-robots", local_site.url("/blocked")], expected_exit=1)
    assert decision["allowed"] is False
    assert local_site.count("/blocked") == 0

    summary = invoke_json(capsys, [
        "--db", str(tmp_path / "failed.db"), "crawl", "--seed", local_site.url("/always-fails"),
        "--allow-domain", "127.0.0.1", "--depth", "0", "--rps", "1000",
        "--max-attempts", "1",
    ], expected_exit=1)
    assert summary["counts"]["failed"] == 1
    assert summary["errors"] >= 1
    assert summary["http_statuses"]["503"] == 1
    failure = summary["recent_failures"][0]
    assert failure["code"] == "http_status"
    assert failure["status"] == 503
    assert failure["url_normalized"] == local_site.url("/always-fails")


def test_cli_unknown_job_is_reported(local_site, tmp_path, capsys):
    assert main(["--db", str(tmp_path / "empty.db"), "status", "--job", "missing"]) == 2
    captured = capsys.readouterr()
    assert "Unknown job" in captured.err


def test_web09_local_benchmark_reports_policy_equivalent_measurements(local_site, tmp_path, capsys):
    report = invoke_json(capsys, [
        "--db", str(tmp_path / "benchmark.db"), "benchmark", "--seed", local_site.url("/fanout"),
        "--mode", "compare", "--rps", "1000", "--max-pages", "9",
        "--concurrency", "4", "--per-host-concurrency", "2",
    ])
    assert report["sequential"]["pages"] == report["async"]["pages"] == 9
    for mode in ("sequential", "async"):
        assert report[mode]["elapsed_seconds"] > 0
        assert report[mode]["throughput_pages_per_second"] > 0
    assert report["conditions"]["rps"] == 1000
    assert report["conditions"]["per_host_concurrency"] == 2
    with Store(tmp_path / "benchmark.db") as store:
        sequential = store.get_job(report["sequential"]["job_id"])["config"]
        concurrent = store.get_job(report["async"]["job_id"])["config"]
    assert sequential.pop("concurrency") == 1
    assert concurrent.pop("concurrency") == 4
    assert sequential == concurrent
    assert report["speedup"] > 0
    # Wall-clock speed is reported, not asserted: loaded CI machines vary.
    assert local_site.count("/fanout") == 2
