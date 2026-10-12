"""Separate configured arrivals, observed starts and completions using raw event times."""
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from common import APIS, metric_values, read_json, result_time, write_json
from pilot_config import configured_starts, discrete_arrivals, execution_for_phase, seconds, stage_schedule

COUNTERS = ("iteration_started", "journey_started", "journey_finished", "iterations", "http_attempted", "http_completed",
            "http_reqs", "booking_completed", "reservation_success", "payment_applied", "business_failures", "system_failures", "dropped_iterations")
REQUIRED = (*COUNTERS, "iteration_duration", "journey_duration", "http_req_duration", "vus", "vus_max")
RESELECTION_COUNTERS = ('booking_journey_started', 'booking_journey_completed', 'booking_journey_sold_out',
                       'booking_journey_system_failed', 'booking_journey_abandoned', 'booking_journey_guard_stopped',
                       'booking_journey_business_terminated', 'seat_selection_attempts', 'pre_reserve_attempts',
                       'seat_conflicts', 'reselections', 'seat_selection_fallbacks', 'reselection_stale_refreshes')
OUTCOME_TRENDS = ('successful_booking_duration', 'sold_out_journey_duration', 'failed_journey_duration',
                 'abandoned_journey_duration', 'guard_stopped_journey_duration', 'business_terminated_journey_duration')
RETRY_TRENDS = ('retries_per_journey', 'retries_per_successful_booking')


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def iso(value):
    return result_time(value)


def distribution(values):
    values = sorted(values)
    if not values:
        return {"count": 0, "avg": None, "min": None, "p50": None, "p95": None, "p99": None, "max": None}

    def quantile(p):
        offset = (len(values) - 1) * p
        lower, upper = math.floor(offset), math.ceil(offset)
        return values[lower] + (values[upper] - values[lower]) * (offset - lower)

    return {"count": len(values), "avg": sum(values) / len(values), "min": values[0], "p50": quantile(.5), "p95": quantile(.95), "p99": quantile(.99), "max": values[-1]}


def analyze(run_dir, metadata, config):
    run_dir = Path(run_dir)
    phase = metadata['phase']
    execution = execution_for_phase(config, phase)
    arrival = execution['executor'] == 'ramping-arrival-rate'
    model = metadata.get('workloadModel', config.get('workloadModel', 'single-attempt'))
    reselection = model == 'reselection'
    schedule = stage_schedule(execution) if arrival else []
    summary_path = run_dir / 'k6-summary.json'
    summary = read_json(summary_path) if summary_path.exists() else {}
    counters = (*COUNTERS, *(('seats_confirmed',) if 'seats_confirmed' in summary.get('metrics', {}) else ()))
    if reselection:
        counters = (*counters, *RESELECTION_COUNTERS)
    events, samples = defaultdict(list), defaultdict(list)
    system_reasons, business_reasons, api_business_reasons, selection_regions = Counter(), Counter(), Counter(), Counter()
    start, workload_end = None, None
    point_errors, http_5xx = [], 0
    points_path = run_dir / 'k6-points.json'
    if points_path.exists():
        with points_path.open(encoding='utf-8') as stream:
            for number, line in enumerate(stream, 1):
                try:
                    point = json.loads(line)
                    if point.get('type') != 'Point':
                        continue
                    data, name = point['data'], point['metric']
                    t, v, tags = timestamp(data['time']), data['value'], data.get('tags', {})
                    if type(v) not in {int, float} or not math.isfinite(v):
                        point_errors.append({"line": number, "error": "non-finite-value"})
                        continue
                    if name in counters and (tags.get('scenario') in {None, phase}):
                        if v:
                            events[name].append((t, v))
                    if name in {'vus', 'vus_max', 'journey_duration', 'booking_duration', *OUTCOME_TRENDS, *RETRY_TRENDS}:
                        samples[name].append((t, v))
                    if name == 'iteration_duration' and tags.get('scenario') == phase:
                        samples[name].append((t, v))
                    if name == 'http_req_duration' and tags.get('api') in APIS:
                        samples[name].append((t, v))
                        samples['latency:' + tags['api']].append((t, v))
                    if name == 'scenario_start_epoch_ms':
                        start = v / 1000
                    elif name == 'workload_end_epoch_ms':
                        # Raw point time avoids Date.now() millisecond rounding before the last completion.
                        workload_end = t
                    elif name == 'system_failures' and v:
                        system_reasons[tags.get('reason', 'untagged')] += v
                    elif name == 'business_failures' and v:
                        business_reasons[tags.get('reason', 'untagged')] += v
                    elif name == 'api_business_failures' and v:
                        api_business_reasons[tags.get('reason', 'untagged')] += v
                    elif name == 'seat_choices' and v:
                        selection_regions[tags.get('requested_region', tags.get('region', 'untagged')) + '->' + tags.get('region', 'untagged')] += v
                    elif name == 'http_reqs' and 500 <= int(tags.get('status', '0')) < 600:
                        http_5xx += v
                except (ValueError, KeyError, TypeError, OverflowError):
                    point_errors.append({"line": number, "error": "unreadable-point"})
    total = {name: sum(v for _, v in events[name]) for name in counters}
    unfinished = max(0, total['iteration_started'] - total['iterations'])
    arrival_duration = sum(seconds(s['duration']) for s in execution['stages']) if arrival else None
    planned_end = start + arrival_duration if start is not None and arrival else None
    last_completed = max((t for t, _ in events['iterations']), default=None)
    last_journey = max((t for t, _ in events['journey_finished']), default=None)
    slots = discrete_arrivals(schedule) if arrival else {'configured_slots': None, 'last_slot_offset_seconds': None}
    nominal_reached = bool(planned_end is not None and workload_end is not None and workload_end >= planned_end - .01)
    last_dispatch = max((t for name in ('iteration_started', 'dropped_iterations') for t, _ in events[name]), default=None)
    last_slot = start + slots['last_slot_offset_seconds'] if start is not None and slots['last_slot_offset_seconds'] is not None else None
    early_exhausted = bool(arrival and metadata.get('k6_exit_code') in {0, 99} and workload_end is not None
                           and last_slot is not None and workload_end >= last_slot - .01
                           and slots['last_slot_offset_seconds'] < arrival_duration - .01
                           and total['iteration_started'] + total['dropped_iterations'] >= slots['configured_slots'])
    # Warm-up scheduling jitter is an observation, not a millisecond deadline SLO.
    # Preserve the old arrival-measurement policy outside the preparation phase.
    end_allowance = 0 if phase == 'warmup' else .01
    late_dispatches = sum(v for t, v in events['iteration_started'] if planned_end is not None and t > planned_end + end_allowance)
    late_drop_samples = sum(v for t, v in events['dropped_iterations'] if planned_end is not None and t > planned_end + end_allowance)
    dispatched_slots = total['iteration_started'] + total['dropped_iterations']
    unobserved_slots = max(0, slots['configured_slots'] - dispatched_slots) if arrival else None
    # A native deadline-boundary slot may never be dispatched. Larger gaps are not native dropped samples.
    boundary_allowance = 1 if arrival and last_slot is not None and abs(last_slot - planned_end) < .01 else 0
    slot_accounting_valid = bool(arrival and unobserved_slots <= boundary_allowance)
    arrival_finished = bool((nominal_reached or early_exhausted) and not late_dispatches and slot_accounting_valid)
    # Drain can cross the nominal end even when dispatch ended early. It must not move the arrival boundary.
    arrival_end = (last_dispatch if early_exhausted else planned_end) if arrival_finished else None
    end_status = ('not-confirmed-late-dispatch' if late_dispatches else 'not-confirmed-dispatch-slot-gap' if nominal_reached and not slot_accounting_valid else
                  ('discrete-arrivals-exhausted-before-nominal-end' if last_dispatch < planned_end else 'discrete-arrivals-exhausted-after-nominal-end') if early_exhausted else 'schedule-completed' if nominal_reached else 'not-confirmed')
    if phase == 'warmup' and arrival:
        arrival_finished = bool(start is not None and last_dispatch is not None and workload_end is not None
                                and slot_accounting_valid and metadata.get('k6_exit_code') in {0, 99})
        arrival_end = last_dispatch if arrival_finished else None
        end_status = 'warmup-observed-dispatch-boundary' if arrival_finished else 'not-confirmed-warmup-accounting-or-teardown'
    metadata.update(workload_started_at=iso(start), arrival_started_at=iso(start) if arrival else None,
                    arrival_planned_ended_at=iso(planned_end), arrival_ended_at=iso(arrival_end),
                    arrival_end_status=end_status if arrival else 'not-applicable (contract smoke)',
                    arrival_end_basis=(('not-confirmed: late dispatch or configured-slot accounting gap; native scheduler shutdown timestamp is not exposed' if not arrival_finished else
                                       'last observed start/drop after all discrete slots were dispatched; native scheduler shutdown timestamp is not exposed' if early_exhausted else
                                       'nominal schedule boundary reached') if arrival else 'not-applicable (contract smoke)'),
                    last_observed_arrival_dispatch_at=iso(last_dispatch) if arrival else None, configured_last_arrival_slot_at=iso(last_slot),
                    last_dispatch_after_planned_end_seconds=max(0, last_dispatch - planned_end) if last_dispatch is not None and planned_end is not None else None,
                    last_iteration_completed_at=iso(last_completed), last_journey_finished_at=iso(last_journey),
                    drain_ended_at=iso(workload_end), workload_ended_at=iso(workload_end),
                    measurement_started_at=iso(start), measurement_ended_at=iso(workload_end) or result_time(metadata.get('ended_at')),
                    measurement_completed_at=result_time(metadata.get('ended_at')),
                    measurement_end_basis='k6-teardown-boundary' if workload_end else 'process-end-upper-bound',
                    actual_iteration_starts=total['iteration_started'], actual_journey_starts=total['journey_started'],
                    actual_journey_finishes=total['journey_finished'], completed_iterations=total['iterations'],
                    unfinished_iterations=unfinished,
                    unfinished_count_basis='raw iteration_started minus k6 iterations; not a native runtime interruption counter')
    if phase == 'warmup' and arrival:
        metadata['arrival_end_basis'] = 'Last observed dispatch proxy after slot accounting and k6 teardown; native scheduler shutdown is not exposed. No millisecond lateness failure rule.'
    stages, bins = [], []
    if start is not None:
        for stage in schedule:
            a, b = start + stage['start_offset_seconds'], start + stage['end_offset_seconds']
            stage_total = {name: sum(v for t, v in events[name] if a <= t < b) for name in counters}
            duration = stage['duration_seconds']
            stages.append({**stage, "started_at": iso(a), "ended_at": iso(b),
                           "observed_iteration_starts": stage_total['iteration_started'],
                           "observed_iteration_start_rate_per_second": stage_total['iteration_started'] / duration,
                           "completed_iterations": stage_total['iterations'],
                           "completed_iteration_throughput_per_second": stage_total['iterations'] / duration,
                           "event_counts": stage_total,
                           "api_latency_ms": {api: distribution([v for t, v in samples['latency:' + api] if a <= t < b]) for api in APIS}})
        end = workload_end if workload_end is not None else timestamp(metadata['ended_at'])
        # Buckets anchored at scenario start; completion events keep their own timestamps.
        bucket_count = max(1, math.ceil(max(0, end - start)))
        bin_counts = [Counter() for _ in range(bucket_count)]
        for name, points in events.items():
            for t, v in points:
                i = math.floor(t - start)
                if 0 <= i < bucket_count:
                    bin_counts[i][name] += v
        for i, counts in enumerate(bin_counts):
            a, b = float(i), min(i + 1., max(0, end - start))
            width = b - a
            if width <= 0:
                continue
            configured = configured_starts(schedule, a, b) if arrival else None
            boundary = arrival_end - start if arrival_end is not None else None
            interval = ('arrival' if b <= boundary else 'drain' if a >= boundary else 'arrival-and-drain') if boundary is not None else 'unconfirmed-arrival'
            bins.append({"started_at": iso(start + a), "ended_at": iso(start + b), "duration_seconds": width,
                         "interval": interval if arrival else 'contract-smoke',
                         "configured_expected_starts": configured,
                         "configured_iteration_arrival_rate_per_second": configured / width if arrival else None,
                         "observed_iteration_starts": counts['iteration_started'],
                         "observed_iteration_start_rate_per_second": counts['iteration_started'] / width,
                         "completed_iterations": counts['iterations'],
                         "completed_iteration_throughput_per_second": counts['iterations'] / width,
                         "event_counts": {name: counts[name] for name in counters}})
    in_arrival = sum(v for t, v in events['iteration_started'] if start is not None and start <= t < (planned_end or start))
    before_arrival = sum(v for t, v in events['iteration_started'] if start is not None and t < start)
    at_or_after_end = sum(v for t, v in events['iteration_started'] if planned_end is not None and t >= planned_end)
    actual_duration = workload_end - start if start is not None and workload_end is not None else None
    observations = {
        "schema_version": 2, 'workloadModel': model, "configured_execution": execution, "configured_arrival_duration_seconds": arrival_duration,
        "configured_discrete_arrivals": slots,
        "observed_dispatch_slots": dispatched_slots if arrival else None,
        "unobserved_configured_slots": unobserved_slots,
        "deadline_boundary_slot_allowance": boundary_allowance,
        "dispatch_events_after_nominal_end_allowance": late_dispatches,
        "native_dropped_samples_emitted_after_nominal_end_allowance": late_drop_samples,
        "late_dispatch_is_validity_condition": phase != 'warmup',
        "last_dispatch_after_planned_end_seconds": metadata['last_dispatch_after_planned_end_seconds'],
        "configured_expected_starts": sum(s['configured_expected_starts'] for s in schedule) if arrival else None,
        "observed_iteration_starts": total['iteration_started'], "observed_starts_in_arrival_window": in_arrival if arrival else None,
        "start_events_before_nominal_arrival": before_arrival if arrival else None, "start_events_at_or_after_nominal_arrival_end": at_or_after_end if arrival else None,
        "observed_iteration_start_rate_per_second": total['iteration_started'] / arrival_duration if nominal_reached or early_exhausted else None,
        "observed_iteration_start_rate_in_nominal_arrival_window_per_second": in_arrival / arrival_duration if nominal_reached or early_exhausted else None,
        "completed_iterations": total['iterations'],
        "completed_iteration_throughput_per_second": total['iterations'] / actual_duration if actual_duration and actual_duration > 0 else None,
        "completed_throughput_denominator_seconds": actual_duration,
        "http_throughput_per_second": total['http_reqs'] / actual_duration if actual_duration and actual_duration > 0 else None,
        "http_completed_throughput_per_second": total['http_completed'] / actual_duration if actual_duration and actual_duration > 0 else None,
        "completed_booking_throughput_per_second": total['booking_completed'] / actual_duration if actual_duration and actual_duration > 0 else None,
        "count_totals": total, "stage_observations": stages, "time_buckets": bins,
        "latency_ms": {name: distribution([v for _, v in samples[name]]) for name in ('http_req_duration', 'journey_duration', 'iteration_duration')},
        "successful_booking_latency_ms": distribution([v for _, v in samples['booking_duration']]),
        "api_latency_ms": {api: distribution([v for _, v in samples['latency:' + api]]) for api in APIS},
        "outcome_latency_ms": {name: distribution([v for _, v in samples[name]]) for name in OUTCOME_TRENDS} if reselection else {},
        "retry_distributions": {name: distribution([v for _, v in samples[name]]) for name in RETRY_TRENDS} if reselection else {},
        "api_business_failure_reasons": dict(api_business_reasons), "seat_selection_regions": dict(selection_regions),
        "gauge_observations": {name: {"samples": len(samples[name]),
                                     "min": min((v for _, v in samples[name]), default=None),
                                     "max": max((v for _, v in samples[name]), default=None),
                                     "last": samples[name][-1][1] if samples[name] else None} for name in ('vus', 'vus_max')},
        "system_failure_reasons": dict(system_reasons), "business_failure_reasons": dict(business_reasons),
        "http_5xx_requests": http_5xx, "raw_point_errors": point_errors,
        "notes": ['Configured rates come from schedule integration; observed starts and completions come from separate raw counters.',
                  'Overall observed starts/s uses the actual phase start counter and configured arrival duration; strict in-window count is retained separately.',
                  'Completed iterations include business/system outcomes and differ from booking_completed.',
                  'Stage/bucket completions and HTTP latency samples use completion-event time, not start-stage cohorts.',
                  'Global completed throughput denominator includes arrival and actual drain; stage/bucket rates use their stated interval.',
                  'k6 1.x may finish after its final discrete slot before the nominal schedule end. That case records the last observed dispatch as the arrival boundary proxy, explicitly not a native scheduler shutdown timestamp.',
                  'Configured schedule rates remain continuous references, including any fractional nominal tail after discrete arrivals are exhausted.']}
    checks = {}
    def check(name, passed, details):
        checks[name] = {"status": "passed" if passed else "failed", "details": details}
    check('summary_saved', bool(summary), 'k6-summary.json exists and is nonempty')
    if arrival:
        check('arrival_boundary', arrival_finished, metadata['arrival_end_status'])
        check('configured_slot_accounting', slot_accounting_valid, {'configured_slots': slots['configured_slots'],
            'observed_started_plus_native_dropped': dispatched_slots, 'unobserved_slots': unobserved_slots,
            'deadline_boundary_allowance': boundary_allowance, 'note': 'Unobserved configured slots are not relabeled as native dropped_iterations.'})
    else:
        check('workload_boundary', workload_end is not None, 'k6 teardown recorded')
    check('raw_points', not point_errors and points_path.exists(), point_errors)
    check('no_unfinished_iterations', unfinished == 0, unfinished)
    check('no_dropped_iterations', metric_values(summary, 'dropped_iterations').get('count') == 0, total['dropped_iterations'])
    if phase == 'baseline':
        checks['system_failure_observation'] = {'status': 'observed', 'count': total['system_failures'],
            'details': dict(system_reasons), 'note': 'Server outcomes are measurements; not a measurement-integrity verdict.'}
    else:
        check('no_system_failures', metric_values(summary, 'system_failures').get('count') == 0, dict(system_reasons))
    terminal_total = (sum(total[n] for n in RESELECTION_COUNTERS[1:7]) if reselection
                      else total['booking_completed'] + total['business_failures'] + total['system_failures'])
    check('journey_accounting', total['iteration_started'] > 0 and total['iteration_started'] == total['journey_started'] == total['journey_finished']
          == total['iterations'] == terminal_total, total)
    if reselection:
        check('reselection_accounting', total['booking_journey_started'] == total['iteration_started']
              and total['booking_journey_completed'] == total['booking_completed']
              and total['booking_journey_system_failed'] == total['system_failures']
              and total['booking_journey_business_terminated'] == total['business_failures'], total)
        check('no_safety_guard_termination', total['booking_journey_guard_stopped'] == 0, total['booking_journey_guard_stopped'])
        for counter, trend in zip(RESELECTION_COUNTERS[1:7], (OUTCOME_TRENDS[0], OUTCOME_TRENDS[1], OUTCOME_TRENDS[2], OUTCOME_TRENDS[3], OUTCOME_TRENDS[4], OUTCOME_TRENDS[5])):
            check('latency_accounting_' + trend, observations['outcome_latency_ms'][trend]['count'] == total[counter], total[counter])
        check('retry_samples', len(samples['retries_per_journey']) == total['journey_finished']
              and len(samples['retries_per_successful_booking']) == total['booking_completed'], observations['retry_distributions'])
    required = (*REQUIRED, *RESELECTION_COUNTERS) if reselection else REQUIRED
    if not arrival:
        # Native VU gauges are emitted periodically; a sub-second contract check can end before the first sample.
        required = tuple(n for n in required if n not in {'vus', 'vus_max'})
        checks['vu_sampling'] = {'status': 'not-required-contract-smoke', 'details': observations['gauge_observations']}
    missing = [name for name in required if name not in summary.get('metrics', {})]
    check('required_metrics', not missing, missing)
    mismatch = [name for name in counters if metric_values(summary, name).get('count') != total[name]]
    check('raw_summary_counts', not mismatch, mismatch)
    if arrival:
        check('vus_samples', all(samples[name] for name in ('vus', 'vus_max')), observations['gauge_observations'])
    called_apis = [a for a in APIS if metric_values(summary, f'http_attempted{{api:{a}}}').get('count', 0) > 0] if reselection else APIS
    check('latency_samples_present', all(observations['api_latency_ms'][api]['count'] > 0 for api in called_apis)
          and all(observations['latency_ms'][name]['count'] > 0 for name in observations['latency_ms']), observations['api_latency_ms'])
    if phase == 'journey-smoke':
        check('smoke_booking_contract', total['booking_completed'] > 0
              and all(observations['api_latency_ms'][api]['count'] > 0 for api in APIS),
              'At least one complete booking exercises all six APIs; Pilot/Baseline do not require all journeys to succeed')
    tolerance = config['validation'].get('arrivalCountTolerance', config['validation'].get('warmupArrivalCountTolerance'))
    for stage in stages:
        expected, actual = stage['configured_expected_starts'], stage['observed_iteration_starts']
        allowed = max(tolerance['absoluteIterations'], expected * tolerance['relative'])
        check('arrival_stage_' + str(stage['stage']), abs(actual - expected) <= allowed,
              {"configured_expected_starts": expected, "observed_starts": actual, "allowed_difference": allowed})
    if arrival and phase == 'warmup':
        checks['starts_after_arrival'] = {'status': 'observed', 'count': late_dispatches,
            'details': metadata['last_dispatch_after_planned_end_seconds'],
            'note': 'Warm-up start lateness is recorded; no millisecond deadline failure rule.'}
    elif arrival:
        check('starts_after_arrival', not any(t > (planned_end or math.inf) + .01 for t, _ in events['iteration_started']), 'No start events later than schedule end + 10ms timestamp allowance')
    minimum = config['validation']['minimumApiSamples']
    sample_status = 'not-assessed' if minimum is None else ('passed' if all(x['count'] >= minimum for x in observations['api_latency_ms'].values()) else 'insufficient')
    checks['sample_policy'] = {"status": 'excluded-' + phase if phase in {'warmup', 'journey-smoke'} else sample_status,
                               "minimum_api_samples": minimum, "api_counts": {a: v['count'] for a, v in observations['api_latency_ms'].items()},
                               "note": 'A chosen minimum is a screening rule, not proof of p99 precision.'}
    observations['validation'] = {"status": 'failed' if any(v['status'] == 'failed' for v in checks.values()) else
                                 'needs-review' if phase not in {'warmup', 'journey-smoke'} and sample_status != 'passed' else 'passed', "checks": checks}
    write_json(run_dir / 'pilot-observations.json', observations)
    metadata['pilot_validation'] = observations['validation']
    return observations
