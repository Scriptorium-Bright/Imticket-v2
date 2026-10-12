"""Discover exposed sample names, then collect raw query_range results after k6."""
import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from common import http_url, read_json, result_now, result_time, write_json


def fetch(url, timeout):
    with urlopen(Request(url, headers={"Accept": "application/json, text/plain"}), timeout=timeout) as response:
        return response.read().decode("utf-8")


def error_description(error):
    if isinstance(error, HTTPError):
        return f"HTTP {error.code}"
    # Do not copy URLs/headers/response bodies into error messages.
    return type(error).__name__


def exposed_names(exposition):
    names = set()
    for line in exposition.splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{|\s)", line)
        if match:
            names.add(match.group(1))
    return sorted(names)


def collect(run_dir, base_url, prometheus_url, step, job, timeout):
    run_dir = Path(run_dir)
    output = run_dir / "prometheus"
    output.mkdir(exist_ok=True)
    metadata = read_json(run_dir / "run-metadata.json")
    status = {
        "status": "not-configured", "collected_at": result_now(),
        "prometheus_url": prometheus_url or None, "job": job, "step_seconds": step,
        "queries": [], "errors": [],
        "unavailable_in_repository_monitoring": ["DB Lock Wait", "MySQL Host Metric", "OCI Host Metric"],
    }
    if not prometheus_url:
        write_json(output / "collection-status.json", status)
        return status
    start = result_time(metadata.get("measurement_started_at"))
    end = result_time(metadata.get("measurement_ended_at"))
    if not start or not end:
        status.update(status="no-measurement-window")
        write_json(output / "collection-status.json", status)
        return status
    status.update(status="collecting", start=start, end=end)
    base_url = http_url(base_url, "BASE_URL")
    prometheus_url = http_url(prometheus_url, "PROMETHEUS_URL")
    try:
        # Exactly one direct app scrape, after load ends, before any query is constructed.
        exposition = fetch(base_url + "/actuator/prometheus", timeout)
        (output / "actuator-prometheus.txt").write_text(exposition, encoding="utf-8")
        names = exposed_names(exposition)
        if not names:
            raise ValueError("No exposed sample names")
        write_json(output / "verified-metric-names.json", names)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
        status.update(status="discovery-failed")
        status["errors"].append({"step": "actuator-discovery", "error": error_description(error)})
        write_json(output / "collection-status.json", status)
        return status

    start_epoch = datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()
    end_epoch = datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp()
    for name in names:
        # Raw series only. Every concrete name is from the live exposition above.
        query = '{__name__=' + json.dumps(name) + ',job=' + json.dumps(job) + '}'
        parameters = {"query": query, "start": start_epoch, "end": end_epoch, "step": step}
        entry = {"metric": name, "query": query, "parameters": parameters, "file": name + ".json"}
        status["queries"].append(entry)
        try:
            raw = fetch(prometheus_url + "/api/v1/query_range?" + urlencode(parameters), timeout)
            (output / entry["file"]).write_text(raw, encoding="utf-8")
            result = json.loads(raw)
            if not isinstance(result, dict) or not isinstance(result.get("data"), dict) or result.get("status") != "success" or result["data"].get("resultType") != "matrix":
                entry["status"] = "api-error"
                status["errors"].append({"metric": name, "error": "Prometheus returned an unsuccessful range response"})
            else:
                series = result["data"].get("result", [])
                entry.update(status="collected" if series else "empty", series_count=len(series))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
            entry["status"] = "request-failed"
            status["errors"].append({"metric": name, "error": error_description(error)})
            if isinstance(error, (HTTPError, URLError, TimeoutError, OSError)):
                # One unreachable/denied endpoint would otherwise incur a timeout
                # for EVERY discovered series and delay fixture cleanup for minutes.
                status["skipped_metric_count"] = len(names) - len(status["queries"])
                write_json(output / "collection-status.json", status)
                break
        write_json(output / "collection-status.json", status)
    status["status"] = "partial" if status["errors"] else (
        "collected" if any(q["status"] == "collected" for q in status["queries"]) else "empty"
    )
    status["collection_finished_at"] = result_now()
    write_json(output / "collection-status.json", status)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    metadata = read_json(args.run_dir / "run-metadata.json")
    config = metadata["prometheus"]
    result = collect(args.run_dir, os.environ.get("BASE_URL") or metadata["base_url"],
                     os.environ.get("PROMETHEUS_URL") or config["url"], config["step_seconds"],
                     config["job"], config["timeout_seconds"])
    print(f"Prometheus: {result['status']}")
    return 0 if result["status"] == "collected" else 1


if __name__ == "__main__":
    raise SystemExit(main())
