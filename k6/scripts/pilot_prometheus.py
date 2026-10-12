"""Verified metric queries and post-load collection; no exporter or dashboard changes."""
import json
import math
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

from collect_prometheus import error_description, exposed_names, fetch
from common import K6_ROOT, read_json, result_now, result_time, write_json
from pilot_config import seconds

INVENTORY = K6_ROOT / "docs" / "metric-inventory.json"
GROUPS = {
    "http": ["http_server_requests_seconds_count", "http_server_requests_seconds_sum", "http_server_requests_seconds_bucket"],
    "tomcat": ["tomcat_threads_busy_threads", "tomcat_threads_config_max_threads", "tomcat_threads_current_threads"],
    "hikari": ["hikaricp_connections_active", "hikaricp_connections_idle", "hikaricp_connections_pending", "hikaricp_connections_max",
               "hikaricp_connections_acquire_seconds_count", "hikaricp_connections_acquire_seconds_sum",
               "hikaricp_connections_acquire_seconds_max", "hikaricp_connections_timeout_total"],
    "jvm": ["process_cpu_usage", "process_cpu_time_ns_total", "jvm_memory_used_bytes", "jvm_memory_committed_bytes",
            "jvm_memory_max_bytes", "jvm_gc_memory_allocated_bytes_total", "jvm_gc_pause_seconds_bucket",
            "jvm_gc_pause_seconds_count", "jvm_gc_pause_seconds_sum", "jvm_gc_pause_seconds_max",
            "jvm_threads_live_threads", "jvm_threads_peak_threads", "jvm_threads_daemon_threads",
            "jvm_threads_states_threads", "jvm_threads_started_threads_total"],
}
UNAVAILABLE = ["DB Lock Wait", "MySQL Host", "OCI Host", "Hikari acquisition histogram", "deadlocked-thread metric"]


def collection_error(error, step):
    reason = getattr(error, 'reason', error)
    if isinstance(error, HTTPError):
        category = 'http-query-failure'
    elif isinstance(reason, TimeoutError) or 'timed out' in str(reason).lower():
        category = 'query-timeout'
    elif isinstance(error, (json.JSONDecodeError, UnicodeError)) or step == 'parse-query-response':
        category = 'parse-failure'
    elif isinstance(error, (ValueError, KeyError, TypeError)):
        category = 'validation-logic-failure'
    elif isinstance(error, (URLError, OSError)):
        category = 'http-query-failure'
    else:
        category = 'unknown'
    return {'step': step, 'classification': category, 'exception_class': type(error).__name__,
            'reason_class': type(reason).__name__, 'reason_repr': repr(reason), 'error': error_description(error)}


def query_plan(config):
    inventory = read_json(INVENTORY)
    verified = {name for group in inventory["groups"].values() for name in group}
    missing = sorted({name for names in GROUPS.values() for name in names} - verified)
    if missing:
        raise ValueError("Pilot query uses unverified metric names: " + ', '.join(missing))
    labels = 'job=' + json.dumps(config["job"]) + ',application=' + json.dumps(config["application"])

    def selector(name, extra=""):
        if name not in verified:
            raise ValueError("Unverified Prometheus metric")
        return name + '{' + labels + (',' + extra if extra else '') + '}'

    http_filter = 'uri=~"/api/(seats|reservations|payments)(/.*)?"'
    plan = {}
    for group, names in GROUPS.items():
        plan[group] = []
        for name in names:
            extra = http_filter if group == "http" else 'area="heap"' if name.startswith("jvm_memory_") else ''
            plan[group].append({"id": name, "expression": selector(name, extra), "metric_names": [name],
                                "kind": "raw", "required": True})
    window = config["rateWindow"]

    def derived(group, key, expression, names, unit):
        plan[group].append({"id": key, "expression": expression, "metric_names": names, "kind": "windowed",
                            "window_seconds": seconds(window), "unit": unit, "required": False})

    count = selector("http_server_requests_seconds_count", http_filter)
    bucket = selector("http_server_requests_seconds_bucket", http_filter)
    derived("http", "booking_http_throughput", f"sum by (method, uri, status) (rate({count}[{window}]))",
            ["http_server_requests_seconds_count"], "requests/s")
    for quantile in (.50, .95, .99):
        derived("http", f"booking_http_p{round(quantile * 100)}",
                f"histogram_quantile({quantile}, sum by (le, method, uri) (rate({bucket}[{window}])))",
                ["http_server_requests_seconds_bucket"], "seconds")
    failed = selector("http_server_requests_seconds_count", http_filter + ',status=~"5.."')
    derived("http", "booking_http_5xx", f"sum(rate({failed}[{window}]))", ["http_server_requests_seconds_count"], "requests/s")
    acquire_sum = selector("hikaricp_connections_acquire_seconds_sum")
    acquire_count = selector("hikaricp_connections_acquire_seconds_count")
    derived("hikari", "acquisition_mean", f"rate({acquire_sum}[{window}]) / rate({acquire_count}[{window}])",
            ["hikaricp_connections_acquire_seconds_sum", "hikaricp_connections_acquire_seconds_count"], "seconds")
    allocation = selector("jvm_gc_memory_allocated_bytes_total")
    derived("jvm", "allocation_rate", f"rate({allocation}[{window}])", ["jvm_gc_memory_allocated_bytes_total"], "bytes/s")
    pause = selector("jvm_gc_pause_seconds_bucket")
    derived("jvm", "gc_pause_p95", f"histogram_quantile(0.95, sum by (le) (rate({pause}[{window}])))",
            ["jvm_gc_pause_seconds_bucket"], "seconds")
    return plan


def epoch(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def wait_for_scrape(url, config, required_epoch):
    """Only invoked after k6 exits; bounded waiting for the final/context scrape."""
    deadline = time.monotonic() + config["settleTimeoutSeconds"]
    state = {"status": "waiting", "required_scrape_epoch": required_epoch}
    while True:
        result = json.loads(fetch(url + '/api/v1/targets', config["queryTimeoutSeconds"]))
        targets = [t for t in result.get('data', {}).get('activeTargets', [])
                   if t.get('labels', {}).get('job') == config["job"]
                   and t.get('labels', {}).get('application', config["application"]) == config["application"]]
        if not targets:
            state.update(status="target-not-found")
            return state
        entries = [{"health": t.get('health'), "last_scrape": t.get('lastScrape'),
                    "scrape_interval": t.get('scrapeInterval'), "scrape_timeout": t.get('scrapeTimeout')}
                   for t in targets]
        ready = all(t['health'] == 'up' and t['last_scrape'] and epoch(t['last_scrape']) >= required_epoch for t in entries)
        state.update(targets=entries, status="fresh" if ready else "waiting")
        if ready:
            return state
        if time.monotonic() >= deadline:
            state.update(status="settle-timeout")
            return state
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def temporal_coverage(entry):
    """Describe in-window density separately from whether query data was collected."""
    points = entry.get('distinct_finite_in_window_evaluation_times', 0)
    if points >= 2:
        return 'multi-sample-in-window'
    if points == 1:
        return 'single-sample-in-window'
    if entry.get('finite_query_range_points', 0):
        return 'context-only'
    return 'no-finite-query-range-values'


def coverage_summary(queries):
    required = [q for q in queries if q.get('required')]
    counts = {name: 0 for name in ('multi-sample-in-window', 'single-sample-in-window',
                                   'context-only', 'no-finite-query-range-values')}
    for query in required:
        counts[query.get('temporal_coverage', 'no-finite-query-range-values')] += 1
    if counts['no-finite-query-range-values']:
        status = 'query-data-gaps'
    elif counts['context-only'] == len(required):
        status = 'context-only'
    elif counts['single-sample-in-window'] or counts['context-only']:
        status = 'sparse-in-window'
    else:
        status = 'multi-sample-in-window'
    return {'status': status, 'required_query_count': len(required), **counts,
            'evaluation_points_are_not_independent_scrapes': True,
            'collection_validity_depends_on_in_window_density': False}


def annotate_query_result(entry, response, start_epoch, end_epoch, query_start, query_end):
    """Attach acquisition and temporal-coverage facts without conflating them."""
    success = response.get('status') == 'success' and response.get('data', {}).get('resultType') == 'matrix'
    series = response.get('data', {}).get('result', []) if success else []
    entry.update(status=('collected' if series else 'empty') if success else 'api-error',
                 series_count=len(series))
    range_values = [(float(t), float(v)) for item in series for t, v in item.get('values', [])
                    if query_start <= float(t) <= query_end]
    measured_values = [(t, v) for t, v in range_values if start_epoch <= t <= end_epoch]
    before_values = [(t, v) for t, v in range_values if t < start_epoch]
    after_values = [(t, v) for t, v in range_values if t > end_epoch]
    finite_range = [(t, v) for t, v in range_values if math.isfinite(v)]
    finite = [(t, v) for t, v in measured_values if math.isfinite(v)]
    finite_before = [(t, v) for t, v in before_values if math.isfinite(v)]
    finite_after = [(t, v) for t, v in after_values if math.isfinite(v)]
    entry.update(
        evaluation_timestamps=sorted({t for t, _ in range_values}),
        in_window_evaluation_timestamps=sorted({t for t, _ in measured_values}),
        query_range_evaluation_points=len(range_values), finite_query_range_points=len(finite_range),
        distinct_query_range_evaluation_times=len({t for t, _ in range_values}),
        in_window_evaluation_points=len(measured_values), finite_in_window_points=len(finite),
        distinct_in_window_evaluation_times=len({t for t, _ in measured_values}),
        distinct_finite_in_window_evaluation_times=len({t for t, _ in finite}),
        pre_window_evaluation_points=len(before_values), finite_pre_window_points=len(finite_before),
        distinct_pre_window_evaluation_times=len({t for t, _ in before_values}),
        post_window_evaluation_points=len(after_values), finite_post_window_points=len(finite_after),
        distinct_post_window_evaluation_times=len({t for t, _ in after_values}))
    entry['temporal_coverage'] = temporal_coverage(entry)
    if success and series and not finite_range:
        entry['status'] = 'no-finite-query-range-values'
    return entry


def collect_pilot(run_dir, base_url, prometheus_url, config, metadata):
    directory = Path(run_dir) / 'prometheus'
    directory.mkdir(exist_ok=True)
    plan = query_plan(config)
    write_json(directory / 'query-plan.json', plan)
    status = {"status": "not-configured", "collected_at": result_now(), "queries": [], "errors": [],
              "job": config["job"], "step_seconds": config["stepSeconds"], "unavailable": UNAVAILABLE,
              "inventory_file": str(INVENTORY), "queries_executed_after_k6_exit": True}
    write_json(directory / 'collection-status.json', status)
    start, end = result_time(metadata.get('measurement_started_at')), result_time(metadata.get('measurement_ended_at'))
    if not start or not end:
        status['status'] = 'no-measurement-window'
    elif not prometheus_url:
        pass
    else:
        url = prometheus_url.rstrip('/')
        start_epoch, end_epoch = epoch(start), epoch(end)
        query_start = start_epoch - config['contextBeforeSeconds']
        query_end = end_epoch + config['contextAfterSeconds']
        status.update(start=start, end=end, measurement_start_epoch=start_epoch, measurement_end_epoch=end_epoch,
                      query_start_epoch=query_start, query_end_epoch=query_end,
                      context_before_seconds=config['contextBeforeSeconds'], context_after_seconds=config['contextAfterSeconds'],
                      scrape_interval_seconds=config['scrapeIntervalSeconds'])
        last_step = 'actuator-discovery'
        try:
            exposition = fetch(base_url.rstrip('/') + '/actuator/prometheus', config['queryTimeoutSeconds'])
            (directory / 'actuator-prometheus.txt').write_text(exposition, encoding='utf-8')
            exposed = exposed_names(exposition)
            write_json(directory / 'verified-metric-names.json', exposed)
            last_step = 'final-scrape'
            status['scrape'] = wait_for_scrape(url, config, query_end)
            if status['scrape']['status'] != 'fresh':
                status['errors'].append({"step": "final-scrape", "error": status['scrape']['status'],
                                         'classification': 'prometheus-target-unavailable'})
            if any(t.get('scrape_interval') and seconds(t['scrape_interval']) != config['scrapeIntervalSeconds']
                   for t in status['scrape'].get('targets', [])):
                status['errors'].append({"step": "scrape-interval", "error": "actual interval differs from config"})
            for group, queries in plan.items():
                group_data = {"group": group, "measurement_start_epoch": start_epoch, "measurement_end_epoch": end_epoch,
                              "query_start_epoch": query_start, "query_end_epoch": query_end,
                              "step_seconds": config['stepSeconds'], "queries": []}
                for spec in queries:
                    entry = {**spec, "file": group + '.json', "status": "missing-at-actuator"}
                    status['queries'].append(entry)
                    if any(name not in exposed for name in spec['metric_names']):
                        group_data['queries'].append(entry.copy())
                        continue
                    parameters = {"query": spec['expression'], "start": query_start, "end": query_end, "step": config['stepSeconds']}
                    entry.update(parameters=parameters)
                    last_step = 'query-range:' + spec['id']
                    raw = fetch(url + '/api/v1/query_range?' + urlencode(parameters), config['queryTimeoutSeconds'])
                    last_step = 'parse-query-response'
                    response = json.loads(raw)
                    last_step = 'annotate-query-response'
                    annotate_query_result(entry, response, start_epoch, end_epoch, query_start, query_end)
                    group_data['queries'].append({**entry, "response": response})
                    write_json(directory / (group + '.json'), group_data)
                    write_json(directory / 'collection-status.json', status)
                write_json(directory / (group + '.json'), group_data)
            required = [q for q in status['queries'] if q['required']]
            status['temporal_coverage'] = coverage_summary(status['queries'])
            status['status'] = 'collected' if not status['errors'] and all(q['status'] == 'collected' for q in required) else 'partial'
        except (OSError, ValueError, TimeoutError, KeyError, TypeError) as error:
            evidence = collection_error(error, last_step)
            status['errors'].append(evidence)
            if 'entry' in locals():
                entry.update(status='collection-failed', failure=evidence)
                if 'group_data' in locals():
                    group_data['queries'].append(entry.copy())
                    write_json(directory / (group + '.json'), group_data)
            status['status'] = 'collection-failed'
        for query in status['queries']:
            if query['required'] and query['status'] != 'collected' and 'failure' not in query:
                query['failure'] = {'classification': 'query-data-unavailable', 'status': query['status']}
    status['collection_finished_at'] = result_now()
    write_json(directory / 'collection-status.json', status)
    return status


def reclassify_saved_collection(run_dir):
    """Re-evaluate saved query_range responses under the current coverage policy, without network access."""
    directory = Path(run_dir) / 'prometheus'
    status_path = directory / 'collection-status.json'
    status = read_json(status_path)
    start_epoch, end_epoch = status.get('measurement_start_epoch'), status.get('measurement_end_epoch')
    query_start, query_end = status.get('query_start_epoch'), status.get('query_end_epoch')
    if None in (start_epoch, end_epoch, query_start, query_end):
        raise ValueError('Saved Prometheus collection has no complete query-range boundaries')
    previous_status = status.get('status')
    by_id = {}
    for group in GROUPS:
        group_path = directory / (group + '.json')
        if not group_path.is_file():
            continue
        group_data = read_json(group_path)
        for entry in group_data.get('queries', []):
            response = entry.get('response')
            if response is not None:
                annotate_query_result(entry, response, start_epoch, end_epoch, query_start, query_end)
            by_id[entry.get('id')] = {key: value for key, value in entry.items() if key != 'response'}
        write_json(group_path, group_data)
    updated_queries = []
    for query in status.get('queries', []):
        updated_queries.append(by_id.get(query.get('id'), query))
    status['queries'] = updated_queries
    required = [query for query in updated_queries if query.get('required')]
    status['temporal_coverage'] = coverage_summary(updated_queries)
    status['status'] = ('collected' if not status.get('errors') and status.get('scrape', {}).get('status') == 'fresh'
                        and all(query.get('status') == 'collected' for query in required) else 'partial')
    status['previous_collection_status'] = previous_status
    status['classification_revision'] = 'prometheus-temporal-coverage-v1'
    status['reclassified_at'] = result_now()
    write_json(status_path, status)
    return status
