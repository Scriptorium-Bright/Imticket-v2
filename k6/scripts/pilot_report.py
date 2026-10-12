"""Factual Pilot conditions, event measurements and resource observations."""
import json
import math
from pathlib import Path

from common import APIS, measured, metric_values, read_json, result_time
from pilot_prometheus import UNAVAILABLE


def cell(value):
    if value is None:
        return 'N/A'
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return str(value).replace('|', '/').replace('\n', ' ')


def table(lines, headers, rows):
    lines.extend(['', '| ' + ' | '.join(headers) + ' |', '|' + '|'.join('---' for _ in headers) + '|'])
    lines.extend('| ' + ' | '.join(cell(v) for v in row) + ' |' for row in rows)


def read_optional(path, default):
    return read_json(path) if path.exists() else default


def prometheus_tables(lines, run_dir, metadata, collection):
    window = metadata.get('prometheus_query_range', {})
    start, end = window.get('measurement_start_epoch'), window.get('measurement_end_epoch')
    temporal = collection.get('temporal_coverage') or {}
    if temporal:
        lines.extend(['', '### Temporal coverage', '',
                      'Collection validity reflects successful query acquisition, finite series data in the expanded query range, and a fresh target scrape. In-window point density is reported separately; a short workload is not invalidated because it overlaps fewer than two evaluation timestamps. Query evaluation timestamps are not independent scrape counts.'])
        table(lines, ['Coverage status', 'Required queries', '2+ in-window times', '1 in-window time',
                      'Context only', 'No finite range data'], [[
            temporal.get('status'), temporal.get('required_query_count'), temporal.get('multi-sample-in-window'),
            temporal.get('single-sample-in-window'), temporal.get('context-only'),
            temporal.get('no-finite-query-range-values')]])
    for group in ('http', 'tomcat', 'hikari', 'jvm'):
        lines.extend(['', '## ' + {'http': 'Server HTTP', 'tomcat': 'Tomcat', 'hikari': 'Hikari', 'jvm': 'JVM'}[group], ''])
        artifact = run_dir / 'prometheus' / (group + '.json')
        data = read_optional(artifact, {})
        if not data:
            lines.append('No collected group artifact. Collection status: ' + collection.get('status', 'not-run') + '.')
            continue
        coverage, observations = [], []
        for query in data.get('queries', []):
            series = query.get('response', {}).get('data', {}).get('result', [])
            # Windowed values need a fully contained window; earlier/context points stay in raw artifacts.
            lower = start + query.get('window_seconds', 0) if start is not None else None
            point_count, finite_count, nonfinite_count = 0, 0, 0
            for item in series:
                values = []
                for t, raw_value in item.get('values', []):
                    if lower is None or end is None or not lower <= float(t) <= end:
                        continue
                    point_count += 1
                    value = float(raw_value)
                    if math.isfinite(value):
                        finite_count += 1
                        values.append(value)
                    else:
                        nonfinite_count += 1
                if query['id'].endswith('_bucket'):
                    continue
                labels = {k: v for k, v in item.get('metric', {}).items() if k not in {'__name__', 'instance', 'job', 'application'}}
                observations.append([query['id'], labels, query.get('unit', 'native metric unit'), len(values),
                                     measured(min(values)) if values else 'N/A', measured(max(values)) if values else 'N/A',
                                     measured(values[-1]) if values else 'N/A'])
            coverage.append([query['id'], query.get('status'), query.get('temporal_coverage'), len(series),
                             query.get('finite_query_range_points', 0), point_count, finite_count,
                             [query.get('finite_pre_window_points', 0), query.get('finite_post_window_points', 0)],
                             nonfinite_count])
        table(lines, ['Query', 'Collection', 'Temporal coverage', 'Series', 'Finite in query range',
                      'In-window points', 'Finite in-window', 'Finite pre/post', 'NaN/Inf'], coverage)
        table(lines, ['Query', 'Labels', 'Unit', 'Finite points', 'Min', 'Max', 'Last'], observations)
        lines.append('\nHistogram bucket series are retained in the raw group artifact. Windowed rates/quantiles are displayed only when their full configured rate window fits inside the measurement interval; shorter intervals can therefore show N/A without invalidating raw collection. Missing/NaN values are not converted to zero.')


def generate_pilot_report(run_dir, metadata):
    run_dir = Path(run_dir)
    summary = read_optional(run_dir / 'k6-summary.json', {})
    observations = read_optional(run_dir / 'pilot-observations.json', {})
    collection = read_optional(run_dir / 'prometheus' / 'collection-status.json', {'status': 'not-run'})
    execution = metadata.get('effective_execution') or metadata['execution']
    model = metadata.get('workloadModel', 'single-attempt')
    reselection = model == 'reselection'
    kind = metadata.get('test_kind', 'pilot')
    title = 'Baseline Experiment' if kind == 'baseline' else 'Reselection Smoke' if kind == 'journey-smoke' else 'Pilot Experiment'
    validation_scenario = metadata.get('validation_scenario')
    if validation_scenario:
        title = 'Reselection Conflict Validation'
    lines = ['# ' + (title + ' Warm-up' if metadata['phase'] == 'warmup' else title), '', '## Condition', '',
             'Times are shown in Asia/Seoul (KST, UTC+09:00). Raw k6 timestamps and Prometheus epochs retain their original representation.']
    if validation_scenario:
        lines.extend(['', 'Purpose: one controlled server-side conflict exercises the existing Reselection code path. This is not a performance measurement; its latency samples are not Baseline evidence.'])
    pairs = [('Run ID', metadata['run_id']), ('Run number', metadata['run_number']), ('Phase', metadata['phase']),
             ('Profile', metadata['profile']), ('Git commit', metadata.get('git_commit')), ('k6 version', metadata.get('k6_version')),
             ('Workload model', model), ('Iteration meaning', metadata.get('iteration_meaning', 'one user booking journey' if reselection else 'one booking attempt')),
             ('Reselection policy', metadata.get('reselection')),
             ('Controlled validation setting', validation_scenario),
             ('Seat count', metadata['seat_count']), ('Observed seat count', metadata.get('observed_seat_count')),
             ('performanceTimeId', metadata['performance_time_id']), ('Fixture initial state', metadata['fixture']['initial_state']),
             ('Fixture preparation start', result_time(metadata['fixture']['prepared_started_at'])), ('Fixture preparation end', result_time(metadata['fixture']['prepared_ended_at'])),
             ('Auth scope', 'Preissued JWT pool; login/register/fixture preparation excluded'), ('Token pool size', metadata['auth']['token_count']),
             ('Distinct declared Members', metadata['auth'].get('declared_distinct_members')), ('Token selection', 'iteration-round-robin; no VU/Member mapping'),
             ('Configured execution', execution), ('Configured peak iteration arrival rate (/s)', metadata.get('configured_peak_iteration_arrival_rate')),
             ('Profile peak iteration arrival rate (/s; reference only)', metadata.get('peak_journey_arrival_rate')),
             ('Attempt Scale (reference; not exact iterations)', metadata.get('reference_attemptors')),
             ('Hot seat / traffic ratio', [metadata.get('hot_seat_ratio'), metadata.get('hot_traffic_ratio')]),
             ('1/2/3 seat ratio', metadata.get('seat_selection_ratio')), ('Random seed', metadata.get('random_seed')),
             ('HTTP timeout', metadata.get('http_timeout')), ('Warm-up setting', metadata.get('warmup')), ('Separate warm-up run', metadata.get('warmup_run')),
             ('Warm-up start', (metadata.get('warmup_run') or {}).get('workload_started_at')),
             ('Warm-up end', (metadata.get('warmup_run') or {}).get('workload_ended_at')),
             ('k6 process start', result_time(metadata.get('started_at'))), ('k6 process end', result_time(metadata.get('ended_at'))),
             ('Workload start', result_time(metadata.get('workload_started_at'))), ('Arrival start', result_time(metadata.get('arrival_started_at'))),
             ('Arrival planned end', result_time(metadata.get('arrival_planned_ended_at'))), ('Arrival confirmed end', result_time(metadata.get('arrival_ended_at'))),
             ('Arrival end status', metadata.get('arrival_end_status')), ('Last iteration completed', result_time(metadata.get('last_iteration_completed_at'))),
             ('Arrival end basis', metadata.get('arrival_end_basis')), ('Last observed arrival dispatch', result_time(metadata.get('last_observed_arrival_dispatch_at'))),
             ('Configured last discrete arrival slot', result_time(metadata.get('configured_last_arrival_slot_at'))),
             ('Last Journey finished', result_time(metadata.get('last_journey_finished_at'))), ('Drain / workload end', result_time(metadata.get('drain_ended_at'))),
             ('Measured data start', result_time(metadata.get('measurement_started_at'))), ('Measured data end', result_time(metadata.get('measurement_ended_at'))),
             ('Whole measurement process completed', result_time(metadata.get('measurement_completed_at'))), ('Measurement end basis', metadata.get('measurement_end_basis')),
             ('k6 exit code', metadata.get('k6_exit_code')), ('Runner status', metadata.get('status'))]
    table(lines, ['Item', 'Value'], pairs)
    if validation_scenario:
        validation_result = read_optional(run_dir / 'reselection-validation.json', {})
        lines.extend(['', '## Controlled Reselection Path', ''])
        table(lines, ['Check', 'Passed'], validation_result.get('checks', {}).items())
        table(lines, ['Counter', 'Observed'], validation_result.get('observed_counts', {}).items())
        table(lines, ['Sequence', 'Time (KST)', 'Event', 'Details'],
              [[index, event.get('at'), event.get('event'), {k: v for k, v in event.items() if k not in {'at', 'event'}}]
               for index, event in enumerate(validation_result.get('trace', []), 1)])
        lines.append('\n' + validation_result.get('holder_note', 'Validation trace unavailable.'))
    lines.extend(['', '## Traffic', '', 'Configured arrivals are schedule values. Observed starts and completed iterations are separate timestamped events.'])
    table(lines, ['Measurement', 'Count / schedule', 'Rate (/s)', 'Denominator'], [
        ['Configured Iteration Arrival Rate', observations.get('configured_expected_starts'), 'stage-dependent' if execution.get('executor') == 'ramping-arrival-rate' else 'N/A (contract smoke)', 'configured arrival schedule' if execution.get('executor') == 'ramping-arrival-rate' else 'not applicable'],
        ['Observed Iteration Start Rate', observations.get('observed_iteration_starts'), measured(observations.get('observed_iteration_start_rate_per_second')), observations.get('configured_arrival_duration_seconds')],
        ['Observed starts strictly inside nominal arrival window', observations.get('observed_starts_in_arrival_window'), measured(observations.get('observed_iteration_start_rate_in_nominal_arrival_window_per_second')), observations.get('configured_arrival_duration_seconds')],
        ['Completed Iteration Throughput', observations.get('completed_iterations'), measured(observations.get('completed_iteration_throughput_per_second')), observations.get('completed_throughput_denominator_seconds')],
        ['Client HTTP throughput (native http_reqs)', observations.get('count_totals', {}).get('http_reqs'), measured(observations.get('http_throughput_per_second')), observations.get('completed_throughput_denominator_seconds')],
        ['Client HTTP responses (http_completed; includes 4xx/5xx)', observations.get('count_totals', {}).get('http_completed'), measured(observations.get('http_completed_throughput_per_second')), observations.get('completed_throughput_denominator_seconds')],
        ['Completed Booking throughput', observations.get('count_totals', {}).get('booking_completed'), measured(observations.get('completed_booking_throughput_per_second')), observations.get('completed_throughput_denominator_seconds')]])
    table(lines, ['Stage', 'Seconds', 'Configured start → end (/s)', 'Expected starts', 'Observed starts', 'Observed start /s', 'Completed', 'Completed /s'],
          [[s['stage'], s['duration_seconds'], f"{s['configured_start_rate_per_second']} → {s['configured_end_rate_per_second']}",
            measured(s['configured_expected_starts']), s['observed_iteration_starts'], measured(s['observed_iteration_start_rate_per_second']),
            s['completed_iterations'], measured(s['completed_iteration_throughput_per_second'])] for s in observations.get('stage_observations', [])])
    lines.append('\nCompleted iterations include every recorded terminal outcome, including conflicts in Single Attempt and SOLD_OUT in Reselection. Booking Completed is reported separately. Stage/bucket completions use completion time.')
    table(lines, ['Configured / observed dispatch accounting', 'Count'], [
        ['Configured discrete slots', (observations.get('configured_discrete_arrivals') or {}).get('configured_slots')],
        ['Observed starts + native dropped', observations.get('observed_dispatch_slots')],
        ['Unobserved configured slots (not native dropped)', observations.get('unobserved_configured_slots')],
        ['Permitted final deadline-boundary slot gap', observations.get('deadline_boundary_slot_allowance')],
        ['Iteration start events later than nominal end +10ms', observations.get('dispatch_events_after_nominal_end_allowance')],
        ['Native dropped samples emitted after nominal end +10ms', observations.get('native_dropped_samples_emitted_after_nominal_end_allowance')]])
    lines.append('\nIteration starts use the custom start event time. Native dropped samples may be emitted in batches; their timestamp is not proof of an individual scheduler dispatch time. An unconfirmed arrival boundary remains N/A; last observed event times are retained.')
    table(lines, ['Start counter timing', 'Count'], [
        ['Strict nominal arrival interval [start, end)', observations.get('observed_starts_in_arrival_window')],
        ['Before nominal start', observations.get('start_events_before_nominal_arrival')],
        ['At/after nominal end', observations.get('start_events_at_or_after_nominal_arrival_end')]])
    lines.append('\nThe overall observed start rate uses the phase start counter / configured arrival duration. Strict interval counts are retained separately; raw event timestamps are unchanged.')
    table(lines, ['Counter', 'Count', 'k6 summary average /s'],
          [[n, measured(metric_values(summary, n).get('count')), measured(metric_values(summary, n).get('rate'))]
           for n in ('iteration_started', 'journey_started', 'journey_finished', 'iterations', 'http_attempted', 'http_completed', 'http_reqs', 'dropped_iterations')])
    lines.append('\nSummary counter rates use k6 testRunDuration; their denominator is not the configured arrival interval.')
    table(lines, ['Gauge', 'Raw samples', 'Min', 'Max', 'Last'],
          [[name, v['samples'], measured(v['min']), measured(v['max']), measured(v['last'])] for name, v in observations.get('gauge_observations', {}).items()])
    lines.extend(['', '## Latency', '', 'Milliseconds. p50 is med. No samples: N/A.'])
    latency = []
    for name in (('http_req_duration', 'iteration_duration') if reselection else ('http_req_duration', 'journey_duration', 'iteration_duration', 'booking_duration')):
        values = metric_values(summary, f'iteration_duration{{scenario:{metadata["phase"]}}}' if name == 'iteration_duration' else name)
        sampled = values.get('count', 0) > 0
        latency.append([name, values.get('count'), *[measured(values.get(key)) if sampled else 'N/A' for key in ('med', 'p(95)', 'p(99)', 'max')]])
    for api in APIS:
        values = metric_values(summary, f'http_req_duration{{api:{api}}}')
        sampled = values.get('count', 0) > 0
        latency.append([api, values.get('count'), *[measured(values.get(key)) if sampled else 'N/A' for key in ('med', 'p(95)', 'p(99)', 'max')]])
    table(lines, ['Metric / API', 'Samples', 'p50', 'p95', 'p99', 'max'], latency)
    if reselection:
        lines.extend(['', '## Journey Outcome Latency', '',
                      'Milliseconds. Main user experience metric: successful_booking_duration. SOLD_OUT and System Failure samples are separate populations.'])
        table(lines, ['Outcome metric', 'Count', 'avg', 'p50', 'p95', 'p99', 'max'],
              [[name, values.get('count'), *[measured(values.get(k)) for k in ('avg', 'p50', 'p95', 'p99', 'max')]]
               for name, values in observations.get('outcome_latency_ms', {}).items()])
        lines.append('\nThe preserved journey_duration counter/trend is an aggregate diagnostic; it is not presented as Successful Booking E2E.')
    lines.extend(['', '## Business Result'])
    table(lines, ['Counter', 'Count'], [[name, measured(metric_values(summary, name).get('count'))]
                                      for name in ('reservation_success', 'booking_completed', 'payment_applied', 'business_failures')])
    table(lines, ['Business failure reason', 'Count'], observations.get('business_failure_reasons', {}).items())
    completed = metric_values(summary, 'booking_completed').get('count')
    confirmed = metric_values(summary, 'seats_confirmed').get('count')
    table(lines, ['Booking / Seat measurement', 'Value'], [
        ['booking_completed (client verified final reservation detail)', completed], ['seats_confirmed (in those completed bookings)', confirmed],
        ['average_seats_per_completed_booking', measured(confirmed / completed) if confirmed is not None and completed else None]])
    if reselection:
        lines.extend(['', '## Business Conflict and Reselection', '',
                      'SEAT_UNAVAILABLE is an internal contention event, not a terminal Journey Business/System Failure.'])
        from pilot_observations import RESELECTION_COUNTERS
        table(lines, ['Counter', 'Count'], [[name, metric_values(summary, name).get('count')] for name in RESELECTION_COUNTERS])
        table(lines, ['Retry metric', 'Count', 'avg', 'p50', 'p95', 'p99', 'max'],
              [[name, values.get('count'), *[measured(values.get(k)) for k in ('avg', 'p50', 'p95', 'p99', 'max')]]
               for name, values in observations.get('retry_distributions', {}).items()])
        table(lines, ['Requested → Selected region', 'Selection count'], observations.get('seat_selection_regions', {}).items())
        table(lines, ['API Business event reason (not terminal Journey count)', 'Count'], observations.get('api_business_failure_reasons', {}).items())
        lines.append('\nReselections/retries count fresh seat-map cycles after the first selection, including same-conflicting-set refreshes. User abandonment and technical guard stops are separate outcomes; neither is inferred from a seat conflict.')
    lines.extend(['', '## System Failures'])
    reasons = observations.get('system_failure_reasons', {})
    table(lines, ['Measurement', 'Count'], [
        ['system_failures (Journey)', measured(metric_values(summary, 'system_failures').get('count'))],
        ['HTTP 5xx requests', observations.get('http_5xx_requests')],
        *[[name, reasons.get(name, 0) if observations else None] for name in ('timeout', 'network-error', 'unexpected-response', 'invalid-json', 'invalid-envelope', 'invalid-data', 'request-exception', 'client-exception', 'unsupported-payment-provider')],
        ['Observed unfinished iterations', metadata.get('unfinished_iterations')]])
    lines.append('\nUnfinished count is raw iteration_started minus k6 iterations, not a native runtime interruption counter.')
    table(lines, ['All System failure reasons', 'Count'], reasons.items())
    lines.extend(['', '## HTTP Expected-Response Result', '',
                  '`http_req_failed` is an HTTP-level expected-response/transport failure rate, not the Journey-level System Failure rate.',
                  'The current response callback is `http.expectedStatuses(200)`: non-200 responses and transport failures count as failed HTTP samples.'])
    values = metric_values(summary, 'http_req_failed')
    rate = values.get('rate')
    table(lines, ['HTTP expected-response metric', 'Value'], [
        ['http_req_failed rate (0–1)', rate],
        ['http_req_failed rate (%)', measured(rate * 100) if rate is not None else None],
        ['failed_requests (k6 HTTP rule)', values.get('passes')],
        ['expected_requests (HTTP 200; not Booking Completed)', values.get('fails')]])
    lines.extend(['',
                  'Rate denominator: all samples in `http_req_failed`; `failed_requests / (failed_requests + expected_requests)`. This denominator is HTTP requests, not iterations.',
                  ('`SEAT_UNAVAILABLE` with HTTP 409 is a Business Conflict retry event and also contributes to `http_req_failed`; it does not terminate this Reselection Journey or contribute to `system_failures`.' if reselection else '`SEAT_UNAVAILABLE` with HTTP 409 is a Business Failure and also contributes to `http_req_failed`; it does not contribute to `system_failures`.'),
                  'An expected HTTP 200 response can still fail DTO/Journey validation. Use `business_failures`, `system_failures`, and `booking_completed` for business/system outcomes.',
                  'In the raw k6 Rate summary, `passes` counts non-zero samples (failed HTTP samples here) and `fails` counts zero samples (expected HTTP samples here). Raw summary values/keys and the response callback are unchanged.',
                  'Journey/API counters are not added together.'])
    lines.extend(['', '## Seat Inventory'])
    inventory = read_optional(run_dir / 'seat-inventory.json', {})
    db = inventory.get('db') or {}
    api = inventory.get('api') or {}
    table(lines, ['Measurement', 'API observation', 'DB consistent snapshot'], [
        ['Initial seats', inventory.get('initial_seat_count'), db.get('initial_seat_count')],
        *[[n, api.get(n), db.get(n)] for n in ('available_seat_count', 'locked_seat_count', 'reserved_seat_count')],
        ['Confirmed/sold seat assignments', None, db.get('confirmed_seat_assignments')],
        ['Unique sold Seat IDs', None, db.get('unique_sold_seat_count')],
        ['Confirmed Bookings', None, db.get('confirmed_booking_count')],
        ['Duplicate/oversold Seat IDs', None, db.get('duplicate_oversold_seat_count')],
        ['seat_sell_through_rate', None, db.get('seat_sell_through_rate')]])
    table(lines, ['Inventory audit', 'Value'], [
        ['Status', inventory.get('status', 'not-recorded')], ['Oversell invariant', inventory.get('oversell_invariant', 'not-verified')],
        ['Duplicate Seat → confirmed Reservation IDs', db.get('duplicate_seat_reservations')],
        ['Duplicate Seat / Reservation mapping count', db.get('duplicate_reservation_mapping_count')],
        ['Duplicate Seat / Reservation mappings', db.get('duplicate_reservation_mappings')],
        ['Inspection start', result_time(inventory.get('inspection_started_at'))], ['Inspection end', result_time(inventory.get('inspection_ended_at'))],
        ['Errors', inventory.get('errors')]])
    lines.extend(['', 'The DB audit checks each Seat ID belongs to at most one CONFIRMED reservation, all confirmed Seat IDs belong to the initial fixture, and unique confirmed seats ≤ initial seats.',
                  'Seat sell-through is unique confirmed Seat IDs / initial seats. A seat-map API snapshot alone cannot prove oversell absence; unverified values remain N/A.',
                  'Client seats_confirmed is the seat count in client-completed bookings. The DB snapshot counts all CONFIRMED reservations, including any whose final client response was not completed.',
                  'Inventory inspection occurs after workload/drain and before cleanup. Scheduler/background changes can occur before the snapshot; API/DB observations have different instants.'])
    lines.extend(['', '## Workload Validation'])
    validation = metadata.get('pilot_validation', {'status': 'not-run', 'checks': {}})
    lines.append('\nStatus: ' + validation['status'])
    table(lines, ['Check', 'Status', 'Recorded details'], [[name, value['status'], value] for name, value in validation['checks'].items()])
    lines.append('\nNo all-iterations-success requirement. An API sample minimum is a screening rule; it does not establish percentile precision. Baseline System Failures are recorded outcomes; generator drops, incomplete accounting and missing artifacts are separate measurement checks.')
    lines.extend(['', '## Prometheus Collection'])
    window = metadata.get('prometheus_query_range') or {}
    human_window = {key: result_time(window.get(epoch_key)) for key, epoch_key in (
        ('measurement_start', 'measurement_start_epoch'), ('measurement_end', 'measurement_end_epoch'),
        ('query_start', 'query_start_epoch'), ('query_end', 'query_end_epoch'))}
    table(lines, ['Item', 'Value'], [('Status', collection.get('status')), ('Query range (KST)', human_window),
                                  ('Scrape completion check', collection.get('scrape')), ('Collection errors', collection.get('errors')),
                                  ('Current automatic collection unavailable', UNAVAILABLE)])
    lines.append('\nRaw queries include separate before/after context. Resource tables use the measured window; windowed queries are shown only when their window fits within it. Gauge values remain in their native units. Server series describe the application during that time, including concurrent/background work.')
    prometheus_tables(lines, run_dir, metadata, collection)
    lines.extend(['', '## Artifacts', ''])
    lines.extend('- ' + path.name for path in sorted(run_dir.iterdir()) if path.is_file() and path.name != 'report.md')
    lines.extend(['- report.md', '- prometheus/query-plan.json', '- prometheus/collection-status.json',
                  '- prometheus/http.json, tomcat.json, hikari.json, jvm.json (when collected)',
                  '', 'Per-second configured/observed/completed measurements and per-stage API latency samples are in pilot-observations.json.', ''])
    (run_dir / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
