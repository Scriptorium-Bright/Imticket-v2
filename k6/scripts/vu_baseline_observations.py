"""Analyze Target VU runs from k6's timestamped raw points."""
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from common import APIS, metric_values, read_json, result_time, write_json
from pilot_observations import distribution

COUNTERS = (
    'iteration_started', 'journey_started', 'journey_finished', 'iterations', 'first_request_started',
    'http_attempted', 'http_completed', 'http_reqs', 'dropped_iterations',
    'reservation_success', 'booking_completed', 'payment_applied', 'business_failures', 'system_failures',
    'seats_confirmed', 'booking_journey_started', 'booking_journey_completed', 'booking_journey_sold_out',
    'booking_journey_system_failed', 'booking_journey_abandoned', 'booking_journey_guard_stopped',
    'booking_journey_business_terminated', 'seat_selection_attempts', 'pre_reserve_attempts',
    'seat_conflicts', 'reselections', 'seat_selection_fallbacks', 'reselection_stale_refreshes',
)
NATIVE_TIMINGS = ('http_req_blocked', 'http_req_connecting', 'http_req_sending',
                  'http_req_waiting', 'http_req_receiving', 'http_req_duration')


def dispatch_observation(starts):
    first, last = min(starts, default=None), max(starts, default=None)
    bins = Counter(str(int(at - first)) for at in starts) if first is not None else Counter()
    return {'count': len(starts), 'first': iso(first), 'last': iso(last),
            'spread_seconds': last - first if first is not None else None,
            'counts_by_elapsed_second': dict(sorted(bins.items(), key=lambda item: int(item[0])))}


def transport_reason(code, message):
    text = message.lower()
    if 'connection refused' in text:
        return 'connection_refused'
    if 'connection reset' in text:
        return 'connection_reset'
    if ('connect' in text or 'dial' in text) and ('timeout' in text or 'timed out' in text):
        return 'connect_timeout'
    if code == '1050' or 'timeout' in text or 'timed out' in text:
        return 'timeout'
    return 'network_error'
TRENDS = (
    'iteration_duration', 'journey_duration', 'booking_duration', 'successful_booking_duration',
    'sold_out_journey_duration', 'failed_journey_duration', 'retries_per_journey',
    'retries_per_successful_booking',
)


def epoch(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def iso(value):
    return result_time(value) if value is not None else None


def analyze(run_dir, metadata, config):
    run_dir = Path(run_dir)
    phase = metadata['phase']
    model = config['workloadModel']
    target = config['measurement']['vus']
    summary = read_json(run_dir / 'k6-summary.json')
    events, samples = defaultdict(list), defaultdict(list)
    errors, system_reasons, business_reasons, http_errors, selection_regions = [], Counter(), Counter(), Counter(), Counter()
    scenario_start, workload_end = None, None
    transport, transport_codes, transport_messages = Counter(), Counter(), Counter()
    points_path = run_dir / 'k6-points.json'
    with points_path.open(encoding='utf-8') as source:
        for number, line in enumerate(source, 1):
            try:
                point = json.loads(line)
                if point.get('type') != 'Point':
                    continue
                name, data = point['metric'], point['data']
                tags, at, value = data.get('tags', {}), epoch(data['time']), data['value']
                if type(value) not in {int, float} or not math.isfinite(value):
                    errors.append({'line': number, 'error': 'non-finite-value'})
                    continue
                if name in COUNTERS and tags.get('scenario') in {None, phase} and value:
                    events[name].append((at, value, tags))
                if name in TRENDS or name in {'vus', 'vus_max'} or name in NATIVE_TIMINGS:
                    samples[name].append((at, value, tags))
                if name == 'http_req_duration':
                    if tags.get('api') in APIS:
                        samples['api:' + tags['api']].append((at, value, tags))
                if name == 'scenario_start_epoch_ms':
                    scenario_start = value / 1000
                elif name == 'workload_end_epoch_ms':
                    workload_end = at
                elif name == 'system_failures' and value:
                    system_reasons[tags.get('reason', 'untagged')] += value
                elif name == 'business_failures' and value:
                    business_reasons[tags.get('reason', 'untagged')] += value
                elif name == 'seat_choices' and value:
                    requested = tags.get('requested_region', tags.get('region', 'untagged'))
                    selection_regions[requested + '->' + tags.get('region', 'untagged')] += value
                elif name == 'http_reqs' and 500 <= int(tags.get('status', '0')) < 600:
                    http_errors['http_5xx'] += value
                # k6 attaches error_code to HTTP business conflicts too (for
                # example 1409 for an HTTP 409). Only status=0 represents a
                # transport failure; expected-response failures are reported
                # separately from the Rate metric and business/system counters.
                elif name == 'http_reqs' and str(tags.get('status', '0')) == '0':
                    code = str(tags.get('error_code', 'unknown'))
                    message = str(tags.get('error', 'not-recorded'))
                    category = transport_reason(code, message)
                    transport['status_0'] += value
                    transport[category] += value
                    transport_codes[code] += value
                    transport_messages[message] += value
                    http_errors['transport'] += value
            except (ValueError, KeyError, TypeError, OverflowError):
                errors.append({'line': number, 'error': 'unreadable-point'})

    totals = {name: sum(value for _, value, _ in events[name]) for name in COUNTERS}
    # Single Attempt does not emit Reselection-only metrics. Preserve explicit zero counts in the report.
    actual_starts = [at for at, value, _ in events['journey_started'] for _ in range(int(value))]
    actual_iteration_starts = [at for at, value, _ in events['iteration_started'] for _ in range(int(value))]
    first_requests = [at for at, value, _ in events['first_request_started'] for _ in range(int(value))]
    finishes = [at for at, value, _ in events['journey_finished'] for _ in range(int(value))]
    completions = [at for at, value, _ in events['iterations'] for _ in range(int(value))]
    first_start = min(actual_starts) if actual_starts else None
    last_start = max(actual_starts) if actual_starts else None
    first_iteration_start = min(actual_iteration_starts) if actual_iteration_starts else None
    last_finish = max(finishes) if finishes else max(completions, default=None)
    unfinished = max(0, int(totals['iteration_started'] - totals['iterations']))
    duration = max(0, last_finish - (first_iteration_start if first_iteration_start is not None else first_start)) if last_finish is not None and (first_iteration_start is not None or first_start is not None) else None
    spread = max(0, last_start - first_start) if first_start is not None and last_start is not None else None

    def trend(name, predicate=lambda _tags: True):
        return distribution([value for _, value, tags in samples[name] if predicate(tags)])

    latency = {'http_req_duration': trend('http_req_duration')}
    latency['iteration_duration'] = trend('iteration_duration', lambda tags: tags.get('scenario') == phase)
    latency['journey_duration'] = trend('journey_duration')
    latency['successful_booking_duration'] = trend('successful_booking_duration') if model == 'reselection' else trend('booking_duration')
    if model == 'reselection':
        latency['sold_out_journey_duration'] = trend('sold_out_journey_duration')
        latency['failed_journey_duration'] = trend('failed_journey_duration')
    else:
        latency['failed_journey_duration'] = trend('journey_duration', lambda tags: tags.get('outcome') == 'system-failure')
    # API populations come from the API-tagged raw request points.
    api_latency = {api: distribution([value for _, value, _ in samples['api:' + api]]) for api in APIS}
    start_bins = Counter()
    if first_start is not None:
        for at in actual_starts:
            start_bins[str(int(at - first_start))] += 1

    scenario_reference = scenario_start if scenario_start is not None else first_iteration_start
    metadata.update(
        workload_started_at=iso(scenario_reference),
        first_journey_started_at=iso(first_start), last_journey_started_at=iso(last_start),
        journey_start_spread_seconds=spread,
        last_journey_finished_at=iso(last_finish), drain_ended_at=iso(workload_end or last_finish),
        workload_ended_at=iso(workload_end or last_finish),
        measurement_started_at=iso(first_iteration_start or first_start),
        measurement_ended_at=iso(workload_end or last_finish),
        actual_iteration_starts=int(totals['iteration_started']), actual_journey_starts=int(totals['journey_started']),
        actual_journey_finishes=int(totals['journey_finished']), completed_iterations=int(totals['iterations']),
        unfinished_iterations=unfinished, observed_start_rate_within_spread=(totals['journey_started'] / spread if spread and spread > 0 else None),
        completed_journey_throughput_per_second=(totals['iterations'] / duration if duration and duration > 0 else None),
        http_rps=(totals['http_reqs'] / duration if duration and duration > 0 else None),
        first_request_dispatch=dispatch_observation(first_requests),
    )
    observations = {
        'schema_version': 1, 'workload_axis': 'target-vus', 'target_vus': target,
        'workload_model': model, 'phase': phase, 'counts': {key: int(value) for key, value in totals.items()},
        'timing': {
            'scenario_start': iso(scenario_reference), 'first_journey_start': iso(first_start),
            'last_journey_start': iso(last_start), 'journey_start_spread_seconds': spread,
            'last_journey_finished': iso(last_finish), 'drain_end': iso(workload_end or last_finish),
            'journey_window_seconds': duration,
        },
        'traffic': {
            'observed_start_rate_within_spread': metadata['observed_start_rate_within_spread'],
            'completed_journey_throughput_per_second': metadata['completed_journey_throughput_per_second'],
            'http_rps': metadata['http_rps'], 'http_requests': int(totals['http_reqs']),
            'start_counts_by_elapsed_second': dict(sorted(start_bins.items(), key=lambda item: int(item[0]))),
            'max_active_vus': max((value for _, value, _ in samples['vus']), default=None),
            'max_vus_observed': max((value for _, value, _ in samples['vus_max']), default=None),
        },
        'latency_ms': latency, 'api_latency_ms': api_latency,
        'native_http_timings_ms': {name: trend(name) for name in NATIVE_TIMINGS},
        'first_request_dispatch': {**dispatch_observation(first_requests),
            'definition': 'Client Counter point immediately before the first seat-map http.request per Journey; not wire or server receipt time.'},
        'transport': {'counts': dict(transport), 'error_codes': dict(transport_codes), 'error_messages': dict(transport_messages),
                      'classification': 'status=0 HTTP request samples; native error_code and error text preserved'},
        'business_failure_reasons': dict(business_reasons), 'system_failure_reasons': dict(system_reasons),
        'seat_selection_regions': dict(selection_regions),
        'http_5xx_requests': int(http_errors['http_5xx']), 'http_transport_error_samples': int(sum(http_errors.values()) - http_errors['http_5xx']),
        'raw_point_errors': errors,
    }
    if model == 'reselection':
        observations['retry_distributions'] = {
            'retries_per_journey': trend('retries_per_journey'),
            'retries_per_successful_booking': trend('retries_per_successful_booking'),
        }
        soldout = events['booking_journey_sold_out']
        observations['first_no_selectable_seat_at'] = iso(min((at for at, _, _ in soldout), default=None))
        observations['outcome_counts'] = {name: int(totals[name]) for name in (
            'booking_journey_completed', 'booking_journey_sold_out', 'booking_journey_system_failed',
            'booking_journey_abandoned', 'booking_journey_guard_stopped', 'booking_journey_business_terminated')}

    inventory = read_json(run_dir / 'seat-inventory.json') if (run_dir / 'seat-inventory.json').exists() else {}
    db = inventory.get('db') or {}
    initial = db.get('initial_seat_count')
    seat_events = sorted((at, value) for at, value, _ in events['seats_confirmed'] if value > 0)
    cumulative, observed_sellout = 0, None
    if initial:
        for at, count in seat_events:
            cumulative += int(count)
            if cumulative >= initial:
                observed_sellout = at
                break
    full_sellout = bool(inventory.get('status') == 'verified' and db.get('oversell_invariant') == 'passed'
                        and initial and db.get('unique_sold_seat_count') == initial)
    observations['seat_inventory_timing'] = {
        'full_sell_out_verified_by_db': full_sellout,
        'client_observed_full_sell_out_at': iso(observed_sellout) if full_sellout and observed_sellout is not None else None,
        'time_from_workload_start_to_client_observed_full_sell_out_seconds': (observed_sellout - scenario_reference) if full_sellout and observed_sellout is not None and scenario_reference is not None else None,
        'time_from_first_journey_start_to_client_observed_full_sell_out_seconds': (observed_sellout - first_start) if full_sellout and observed_sellout is not None and first_start is not None else None,
        'final_available_seats': db.get('available_seat_count'),
        'note': 'Sell-out time is the timestamp of the client-observed completed booking that reaches initial seat count, and is reported only when the final DB snapshot verifies every initial seat is uniquely confirmed.'
    }

    checks = {}
    def check(name, passed, details):
        checks[name] = {'status': 'passed' if passed else 'failed', 'details': details}
    check('configured_target_vus', target == metadata.get('configured_target_vus'), {'configured': metadata.get('configured_target_vus'), 'profile': target})
    check('target_vu_iteration_accounting', totals['iteration_started'] == target and totals['journey_started'] == target
          and totals['journey_finished'] == target and totals['iterations'] == target, {
              'target_vus': target, 'iteration_started': totals['iteration_started'], 'journey_started': totals['journey_started'],
              'journey_finished': totals['journey_finished'], 'iterations': totals['iterations']})
    if model == 'single-attempt':
        expected_terminal = totals['booking_completed'] + totals['business_failures'] + totals['system_failures']
        check('single_attempt_terminal_accounting', totals['journey_finished'] == expected_terminal,
              {'journey_finished': totals['journey_finished'], 'booking_completed': totals['booking_completed'],
               'business_failures': totals['business_failures'], 'system_failures': totals['system_failures']})
    check('no_unfinished_iterations', unfinished == 0, unfinished)
    check('no_dropped_iterations', metric_values(summary, 'dropped_iterations').get('count') == 0, totals['dropped_iterations'])
    check('native_vu_capacity_observed', observations['traffic']['max_vus_observed'] == target, {
        'target_vus': target, 'max_active_vus': observations['traffic']['max_active_vus'],
        'max_vus_observed': observations['traffic']['max_vus_observed']})
    check('raw_points', points_path.exists() and not errors, errors)
    if metadata.get('reproducibility_required'):
        check('one_first_request_per_journey', len(first_requests) == target, {'target': target, 'first_requests': len(first_requests)})
        check('native_http_timings_accounting', all(len(samples[name]) == totals['http_reqs'] for name in NATIVE_TIMINGS),
              {name: len(samples[name]) for name in NATIVE_TIMINGS})
    check('inventory_and_oversell_audit', inventory.get('status') == 'verified' and db.get('oversell_invariant') == 'passed', {
        'inventory_status': inventory.get('status'), 'oversell_invariant': db.get('oversell_invariant')})
    if model == 'reselection':
        terminal = sum(observations['outcome_counts'].values())
        check('reselection_terminal_accounting', totals['booking_journey_started'] == target and terminal == target,
              {'started': totals['booking_journey_started'], 'terminal_outcomes': terminal})
        check('no_safety_guard_termination', totals['booking_journey_guard_stopped'] == 0,
              int(totals['booking_journey_guard_stopped']))
    checks['system_failures_are_observations'] = {'status': 'observed', 'count': int(totals['system_failures']), 'details': dict(system_reasons)}
    checks['business_failures_are_observations'] = {'status': 'observed', 'count': int(totals['business_failures']), 'details': dict(business_reasons)}
    observations['validation'] = {'status': 'failed' if any(item['status'] == 'failed' for item in checks.values()) else 'passed', 'checks': checks}
    write_json(run_dir / 'vu-baseline-observations.json', observations)
    metadata['vu_baseline_validation'] = observations['validation']
    return observations
