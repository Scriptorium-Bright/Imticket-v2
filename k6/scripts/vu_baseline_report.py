"""Factual report for Target VU Baseline runs; no Arrival Rate axis or diagnosis."""
from pathlib import Path

from common import APIS, measured, metric_values, read_json, result_time
from pilot_report import cell, prometheus_tables, read_optional, table
from pilot_prometheus import UNAVAILABLE


def generate_vu_report(run_dir, metadata=None):
    run_dir = Path(run_dir)
    metadata = metadata or read_json(run_dir / 'run-metadata.json')
    summary = read_optional(run_dir / 'k6-summary.json', {})
    observations = read_optional(run_dir / 'vu-baseline-observations.json', {})
    collection = read_optional(run_dir / 'prometheus' / 'collection-status.json', {'status': 'not-run', 'queries': [], 'errors': []})
    inventory = read_optional(run_dir / 'seat-inventory.json', {})
    db = inventory.get('db') or {}
    api_inventory = inventory.get('api') or {}
    reselection = metadata.get('workloadModel') == 'reselection'
    counts = observations.get('counts', {})
    timing = observations.get('timing', {})
    traffic = observations.get('traffic', {})
    latency_obs = observations.get('latency_ms', {})
    api_latency = observations.get('api_latency_ms', {})
    lines = [
        '# ' + metadata.get('measurement_category', 'Target VU Baseline'), '',
        'Workload axis: Target Users / Target VUs. Arrival Rate is not an input to this run.', '',
        '## Conditions', '',
    ]
    table(lines, ['Item', 'Value'], [
        ['Run ID', metadata.get('run_id')], ['Profile', metadata.get('profile')],
        ['Measurement category / series', [metadata.get('measurement_category'), metadata.get('measurement_series_id')]],
        ['Harness Git SHA / dirty', [metadata.get('harness_git_sha', metadata.get('git_commit')), metadata.get('harness_dirty', metadata.get('working_tree_dirty'))]],
        ['Application checkout Git SHA', metadata.get('application_git_sha')],
        ['Workload model', metadata.get('workloadModel')], ['Workload axis', metadata.get('workloadAxis')],
        ['Target VUs / users', metadata.get('configured_target_vus')], ['Seat count', metadata.get('seat_count')],
        ['performanceTimeId', metadata.get('performance_time_id')],
        ['Hot Seat range / traffic ratio', [metadata.get('hot_seat_ratio'), metadata.get('hot_traffic_ratio')]],
        ['1/2/3 seat ratio', metadata.get('seat_selection_ratio')], ['Random seed', metadata.get('random_seed')],
        ['Token pool size / unique Members', [metadata.get('auth', {}).get('token_count'), metadata.get('auth', {}).get('declared_distinct_members')]],
        ['Token mapping', metadata.get('auth', {}).get('selection')],
        ['Configured executor', metadata.get('execution')], ['Request timeout', metadata.get('http_timeout')],
        ['Warm-up config', metadata.get('warmup')], ['Warm-up run', metadata.get('warmup_run')],
        ['Fixture ID / initial state', [metadata.get('fixture', {}).get('fixture_id'), metadata.get('fixture', {}).get('initial_state')]],
        ['Fixture preparation start / end', [metadata.get('fixture', {}).get('prepared_started_at'), metadata.get('fixture', {}).get('prepared_ended_at')]],
        ['k6 process start / end', [result_time(metadata.get('started_at')), result_time(metadata.get('ended_at'))]],
        ['Git commit / dirty', [metadata.get('git_commit'), metadata.get('working_tree_dirty')]],
    ])
    lines.extend(['', '## Journey Start and Traffic', ''])
    table(lines, ['Measurement', 'Observed value'], [
        ['Target VUs', metadata.get('configured_target_vus')],
        ['iteration_started', counts.get('iteration_started')], ['journey_started', counts.get('journey_started')],
        ['First Journey start (KST)', timing.get('first_journey_start')],
        ['Last Journey start (KST)', timing.get('last_journey_start')],
        ['Journey start spread (seconds)', timing.get('journey_start_spread_seconds')],
        ['Observed starts / second within start spread', observations.get('traffic', {}).get('observed_start_rate_within_spread')],
        ['Start counts by elapsed second', traffic.get('start_counts_by_elapsed_second')],
        ['Max active VUs / observed VU ceiling', [traffic.get('max_active_vus'), traffic.get('max_vus_observed')]],
        ['Completed iterations', counts.get('iterations')], ['journey_finished', counts.get('journey_finished')],
        ['Unfinished iterations', metadata.get('unfinished_iterations')],
        ['Completed Journey throughput (/s)', observations.get('traffic', {}).get('completed_journey_throughput_per_second')],
        ['http_reqs', counts.get('http_reqs')], ['Actual HTTP RPS', observations.get('traffic', {}).get('http_rps')],
        ['http_attempted / http_completed', [counts.get('http_attempted'), counts.get('http_completed')]],
        ['dropped_iterations', counts.get('dropped_iterations')],
        ['First request dispatch (client marker)', observations.get('first_request_dispatch')],
    ])
    lines.extend(['', '## Latency', '', 'Milliseconds. Percentiles are k6 summary values; sample counts are shown alongside them.'])

    def summary_row(name, label=None, samples=None):
        values = metric_values(summary, name)
        count = samples if samples is not None else values.get('count')
        present = count is not None and count > 0
        return [label or name, count, *([measured(values.get(k)) for k in ('med', 'p(95)', 'p(99)', 'max')] if present else ['N/A'] * 4)]

    latency_rows = [summary_row('http_req_duration', 'Overall HTTP', metric_values(summary, 'http_req_duration').get('count')),
                    summary_row(f'iteration_duration{{scenario:{metadata.get("phase")}}}', 'Iteration'),
                    summary_row('journey_duration', 'Journey', metric_values(summary, 'journey_duration').get('count'))]
    if reselection:
        latency_rows.extend([
            summary_row('successful_booking_duration', 'Successful Booking E2E'),
            summary_row('sold_out_journey_duration', 'SOLD_OUT Journey E2E'),
            summary_row('failed_journey_duration', 'System Failure Journey E2E'),
        ])
    else:
        latency_rows.append(summary_row('booking_duration', 'Successful Booking'))
    for api in APIS:
        latency_rows.append(summary_row(f'http_req_duration{{api:{api}}}', api, api_latency.get(api, {}).get('count', 0)))
    table(lines, ['Metric / API', 'Samples', 'p50', 'p95', 'p99', 'max'], latency_rows)
    if not reselection and latency_obs.get('failed_journey_duration'):
        value = latency_obs['failed_journey_duration']
        table(lines, ['System Failure Journey', 'Samples', 'p50', 'p95', 'p99', 'max'],
              [['journey_duration filtered by outcome=system-failure', value.get('count'), *[value.get(k) for k in ('p50', 'p95', 'p99', 'max')]]])
    lines.extend(['', '## Native HTTP Timings and Transport', ''])
    table(lines, ['Native timing (ms)', 'Samples', 'p50', 'p95', 'p99', 'max'],
          [[name, value.get('count'), *[value.get(k) for k in ('p50', 'p95', 'p99', 'max')]]
           for name, value in observations.get('native_http_timings_ms', {}).items()])
    table(lines, ['Transport observation', 'Value'], [[name, value] for name, value in observations.get('transport', {}).items()])
    preparation = metadata.get('auth_preparation') or {}
    if preparation:
        lines.extend(['', '## JWT Preparation (excluded from measurement)', ''])
        table(lines, ['Observation', 'Value'], [[key, preparation.get(key)] for key in
              ('phase', 'target_authentication_count', 'completed_authentication_count', 'elapsed_seconds', 'failure_count', 'status', 'started_at', 'ended_at')])

    lines.extend(['', '## Business and System Results', ''])
    table(lines, ['Measurement', 'Count'], [
        ['reservation_success', counts.get('reservation_success')], ['booking_completed', counts.get('booking_completed')],
        ['payment_applied', counts.get('payment_applied')], ['business_failures', counts.get('business_failures')],
        ['Business Failure reasons', observations.get('business_failure_reasons')],
        ['system_failures', counts.get('system_failures')], ['System Failure reasons', observations.get('system_failure_reasons')],
        ['HTTP 5xx requests', observations.get('http_5xx_requests')],
        ['HTTP transport error samples (status=0)', observations.get('http_transport_error_samples')],
        ['http_req_failed rate (fraction of all HTTP samples)', metric_values(summary, 'http_req_failed').get('rate')],
        ['http_req_failed true / false samples', [metric_values(summary, 'http_req_failed').get('passes'), metric_values(summary, 'http_req_failed').get('fails')]],
    ])
    lines.append('\n`http_req_failed` is the fraction of HTTP samples that are non-zero (failed expected-response or transport samples). Its true/false counts are shown in that order. An HTTP 409 SEAT_UNAVAILABLE contributes to the true count, but is a Business Conflict; in Reselection it is an internal retry event. Transport errors are counted separately from samples with status=0, and System Failure uses the journey-level system_failures metric.')
    confirmed = metric_values(summary, 'seats_confirmed').get('count')
    completed = metric_values(summary, 'booking_completed').get('count')
    table(lines, ['Booking / seat values', 'Observed'], [
        ['seats_confirmed (client completed bookings)', confirmed],
        ['average_seats_per_completed_booking', measured(confirmed / completed) if confirmed and completed else None],
    ])
    if reselection:
        lines.extend(['', '## Reselection', ''])
        table(lines, ['Journey / contention counter', 'Count'], [[name, counts.get(name, 0)] for name in (
            'booking_journey_started', 'booking_journey_completed', 'booking_journey_sold_out',
            'booking_journey_system_failed', 'booking_journey_abandoned', 'booking_journey_guard_stopped',
            'seat_selection_attempts', 'pre_reserve_attempts', 'seat_conflicts', 'reselections',
            'seat_selection_fallbacks', 'reselection_stale_refreshes')])
        table(lines, ['Retry distribution', 'Samples', 'avg', 'p50', 'p95', 'p99', 'max'], [
            [name, values.get('count'), *[measured(values.get(k)) for k in ('avg', 'p50', 'p95', 'p99', 'max')]]
            for name, values in observations.get('retry_distributions', {}).items()])
        table(lines, ['Requested region → selected region', 'Selection count'], observations.get('seat_selection_regions', {}).items())
        lines.append(f"\nFirst NO_SELECTABLE_SEAT / SOLD_OUT outcome: {observations.get('first_no_selectable_seat_at') or 'N/A'} (KST).")

    lines.extend(['', '## Seat Inventory and Sell-out', ''])
    db_status = db.get('status')
    table(lines, ['Measurement', 'Post-run API snapshot', 'DB consistent snapshot'], [
        ['Initial seats', inventory.get('initial_seat_count'), db.get('initial_seat_count')],
        ['AVAILABLE', api_inventory.get('available_seat_count'), db.get('available_seat_count')],
        ['LOCKED', api_inventory.get('locked_seat_count'), db.get('locked_seat_count')],
        ['RESERVED', api_inventory.get('reserved_seat_count'), db.get('reserved_seat_count')],
        ['Confirmed seat assignments', None, db.get('confirmed_seat_assignments')],
        ['Unique confirmed Seat IDs', None, db.get('unique_sold_seat_count')],
        ['Confirmed bookings', None, db.get('confirmed_booking_count')],
        ['Duplicate/oversold Seat IDs', None, db.get('duplicate_oversold_seat_count')],
        ['Sell-through rate', None, db.get('seat_sell_through_rate')],
        ['DB inventory audit / oversell invariant', db_status, db.get('oversell_invariant')],
        ['Full sell-out verified', observations.get('seat_inventory_timing', {}).get('full_sell_out_verified_by_db')],
        ['Client-observed full sell-out timestamp', observations.get('seat_inventory_timing', {}).get('client_observed_full_sell_out_at')],
        ['Seconds from workload start to full sell-out', observations.get('seat_inventory_timing', {}).get('time_from_workload_start_to_client_observed_full_sell_out_seconds')],
        ['Seconds from first Journey start to full sell-out', observations.get('seat_inventory_timing', {}).get('time_from_first_journey_start_to_client_observed_full_sell_out_seconds')],
    ])
    lines.append('\nFull sell-out timing is reported only when the final DB snapshot confirms every initial Seat ID is uniquely confirmed. The event timestamp is the client-observed completion of the booking that reaches the initial seat count, not a DB commit timestamp.')

    lines.extend(['', '## Load Generator', ''])
    generator = read_optional(run_dir / 'load-generator-observations.json', {})
    table(lines, ['Measurement', 'Value'], [
        ['Observation status', generator.get('status')], ['Samples', len(generator.get('samples', []))],
        ['k6 process CPU max (%)', generator.get('k6_process_cpu_percent_max')],
        ['k6 process RSS max (bytes)', generator.get('k6_process_rss_bytes_max')],
        ['Host memory/swap summary', generator.get('host_memory_summary')],
        ['Docker OFF during sampled observations', generator.get('docker_off_during_observation')],
    ])

    lines.extend(['', '## Workload Validation', ''])
    validation = observations.get('validation', {})
    table(lines, ['Item', 'Status'], [['Overall validation', validation.get('status')]])
    if metadata.get('series_validation'):
        table(lines, ['Series validation', 'Value'], [[key, value] for key, value in metadata['series_validation'].items()])
    table(lines, ['Check', 'Status', 'Details'], [[name, check.get('status'), check.get('details')]
        for name, check in validation.get('checks', {}).items()])
    prom_check = metadata.get('vu_baseline_validation', {}).get('checks', {}).get('prometheus_collection')
    if prom_check:
        table(lines, ['Prometheus collection', 'Status', 'Details'], [['collection', prom_check.get('status'), prom_check.get('details')]])
    lines.extend(['', '## Prometheus Collection', ''])
    window = metadata.get('prometheus_query_range') or {}
    table(lines, ['Item', 'Value'], [
        ['Status', collection.get('status')],
        ['Measurement start / end (KST)', [result_time(window.get('measurement_start_epoch')), result_time(window.get('measurement_end_epoch'))]],
        ['Query range start / end (KST)', [result_time(window.get('query_start_epoch')), result_time(window.get('query_end_epoch'))]],
        ['Scrape interval / query step (seconds)', [metadata.get('prometheus', {}).get('settings', {}).get('scrapeIntervalSeconds'), metadata.get('prometheus', {}).get('settings', {}).get('stepSeconds')]],
        ['Collection errors', collection.get('errors')], ['Unavailable collectors', UNAVAILABLE],
    ])
    prometheus_tables(lines, run_dir, metadata, collection)
    lines.extend(['', '## Artifacts', ''])
    lines.extend('- ' + path.name for path in sorted(run_dir.iterdir()) if path.is_file() and path.name != 'report.md')
    lines.append('')
    (run_dir / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
