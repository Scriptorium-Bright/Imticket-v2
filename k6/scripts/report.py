"""Generate factual experiment conditions and measured values, without interpretation."""
import argparse
from pathlib import Path

from common import APIS, measured, metric_values, read_json, result_time


def report_metric_values(summary, name):
    # Retain old raw experiment files while displaying the current metric name.
    if ((name == "http_attempted" or name.startswith("http_attempted{"))
            and name not in summary.get("metrics", {})):
        name = name.replace("http_attempted", "http_offered", 1)
    return metric_values(summary, name)


def generate_report(run_dir):
    run_dir = Path(run_dir)
    metadata = read_json(run_dir / "run-metadata.json")
    if metadata.get('measurement_role') == 'harness-diagnostic':
        from harness_observations import diagnostic_report
        return diagnostic_report(run_dir)
    if metadata.get('workloadAxis') == 'target-vus' and metadata.get('phase') == 'baseline':
        from vu_baseline_report import generate_vu_report
        return generate_vu_report(run_dir, metadata)
    if metadata.get('test_kind') in {'pilot', 'baseline', 'journey-smoke'}:
        from pilot_report import generate_pilot_report
        return generate_pilot_report(run_dir, metadata)
    summary_file = run_dir / "k6-summary.json"
    summary = read_json(summary_file) if summary_file.exists() else {}
    collection_file = run_dir / "prometheus" / "collection-status.json"
    collection = read_json(collection_file) if collection_file.exists() else {"status": "not-run", "queries": []}
    profile = metadata.get("profile_settings") or {}
    execution = metadata.get("effective_execution") or metadata.get("execution") or {}
    fixture_conditions = metadata.get("fixture", {}).get("conditions") or {}
    pairs = [
        ("Run ID", metadata["run_id"]), ("Run number", metadata["run_number"]),
        ("Report time zone", "Asia/Seoul (KST, UTC+09:00)"),
        ("Test kind / phase", f"{metadata['test_kind']} / {metadata['phase']}"),
        ("Profile", metadata["profile"]), ("Git commit", metadata.get("git_commit")),
        ("performanceTimeId", metadata["performance_time_id"]),
        ("Auth scope", "preissued token pool; login/register/fixture preparation excluded"),
        ("Token pool size", metadata.get("auth", {}).get("token_count")),
        ("Declared distinct members", metadata.get("auth", {}).get("declared_distinct_members")),
        ("Token selection", metadata.get("auth", {}).get("selection")),
        ("VU / Member mapping", "none"),
        ("Configured smoke VUs", execution.get("vus")),
        ("Executor", execution.get("executor")),
        ("Iterations/VU", execution.get("iterations")),
        ("HTTP timeout", metadata.get("http_timeout")),
        ("Smoke maxDuration", execution.get("maxDuration") or "k6 default (10m upper bound)"),
        ("Smoke gracefulStop", execution.get("gracefulStop") or "k6 default (30s)"),
        ("Observed seat count", metadata.get("observed_seat_count")),
        ("Profile seat count", profile.get("seatCount")),
        ("Reference attemptors (not exact iterations)", profile.get("referenceAttemptors")),
        ("Profile Peak Journey Arrival Rate (starts/s)", profile.get("peakJourneyArrivalRate")),
        ("Applied Journey arrival rate", "N/A (smoke: one journey per VU)"),
        ("Profile hot seat ratio", profile.get("hotSeatRatio")),
        ("Profile hot traffic ratio", profile.get("hotTrafficRatio")),
        ("Profile 1/2/3 seat selection ratio", profile.get("seatSelectionRatio")),
        ("Applied smoke seats/Journey", metadata.get("smoke_seats_per_journey", metadata.get("smoke_seats_per_user"))),
        ("Applied smoke seat strategy", "ID-ordered disjoint AVAILABLE slots"),
        ("k6 process start (KST)", result_time(metadata.get("started_at"))),
        ("k6 process end (KST)", result_time(metadata.get("ended_at"))),
        ("First Journey start (KST)", result_time(metadata.get("measurement_started_at"))),
        ("Last Journey finish (KST)", result_time(metadata.get("measurement_ended_at"))),
        ("k6 exit code", metadata.get("k6_exit_code")),
        ("Runner status", metadata.get("status")),
        ("k6 version", metadata.get("k6_version")),
        ("Fixture ID", metadata.get("fixture", {}).get("fixture_id")),
        ("Fixture preparation start (KST)", result_time(metadata.get("fixture", {}).get("prepared_started_at"))),
        ("Fixture preparation end (KST)", result_time(metadata.get("fixture", {}).get("prepared_ended_at"))),
        ("Fixture initial state", metadata.get("fixture", {}).get("initial_state")),
        ("Fixture section / grade", f"{fixture_conditions.get('section', 'N/A')} / {fixture_conditions.get('grade', 'N/A')}"),
        ("Fixture rows / seats per row", f"{fixture_conditions.get('rows', 'N/A')} / {fixture_conditions.get('seatsPerRow', 'N/A')}"),
        ("Fixture seat price (KRW)", fixture_conditions.get("price")),
    ]
    lines = ["# Imticket-v2 experiment", "", "## Conditions", "", "| Item | Value |", "|---|---|"]
    for key, value in pairs:
        lines.append(f"| {key} | {str(value if value is not None else 'N/A').replace('|', '/')} |")
    duration_ms = summary.get("state", {}).get("testRunDurationMs")
    lines += ["", "## Counters and rates", "",
              f"Rate denominator: k6 testRunDuration = {measured(duration_ms)} ms.", "",
              "| Metric | Count | /s |", "|---|---:|---:|"]
    for name in ["journey_started", "journey_finished", "http_attempted", "http_completed", "http_contract_success",
                 "reservation_success", "payment_applied", "booking_completed", "business_failures", "system_failures",
                 "api_business_failures", "api_system_failures", "dropped_iterations", "iterations"]:
        values = report_metric_values(summary, name)
        lines.append(f"| {name} | {measured(values.get('count'))} | {measured(values.get('rate'))} |")
    lines += ["", "## Latency", "", "All values in ms. p50 is k6 med. No samples: N/A.", "",
              "| Metric | Samples | p50 | p95 | p99 |", "|---|---:|---:|---:|---:|"]
    for name in ["http_req_duration", "journey_duration", "booking_duration"] + [f"http_req_duration{{api:{api}}}" for api in APIS]:
        values = metric_values(summary, name)
        sampled = values.get("count", 0) > 0
        quantiles = [measured(values.get(key)) if sampled else "N/A" for key in ("med", "p(95)", "p(99)")]
        lines.append(f"| {name} | {measured(values.get('count'))} | {' | '.join(quantiles)} |")
    lines += ["", "## API counters", "", "| API | Attempted | Completed | Business Failure | System Failure |",
              "|---|---:|---:|---:|---:|"]
    for api in APIS:
        values = [measured(report_metric_values(summary, f"{name}{{api:{api}}}").get("count"))
                  for name in ("http_attempted", "http_completed", "api_business_failures", "api_system_failures")]
        lines.append(f"| {api} | {' | '.join(values)} |")
    lines += ["", "## Prometheus collection", "",
              f"- Status: {collection['status']}",
              f"- query_range start (KST): {result_time(collection.get('start')) or 'N/A'}",
              f"- query_range end (KST): {result_time(collection.get('end')) or 'N/A'}",
              f"- Step (seconds): {collection.get('step_seconds', 'N/A')}",
              f"- Verified metric names / queries: {len(collection.get('queries', []))}",
              f"- Collection errors: {len(collection.get('errors', []))}",
              "- Current repository monitoring cannot automatically collect DB Lock Wait / MySQL Host / OCI Host metrics.",
              "", "## Files", "", "- run-metadata.json", "- fixture-manifest.json", "- fixture-initial-seats.json", "- k6-summary.json", "- k6-summary.txt",
              "- k6-points.json (NDJSON with timestamps and API tags)", "- k6.log",
              "- prometheus/collection-status.json", "- prometheus/actuator-prometheus.txt (when discovery succeeds)",
              "- prometheus/verified-metric-names.json (when discovery succeeds)",
              "- prometheus/<verified metric name>.json (raw query_range responses)", ""]
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    generate_report(parser.parse_args().run_dir)
