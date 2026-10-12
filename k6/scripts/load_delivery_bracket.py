"""Fixed-condition arrival bracketing; never increases VUs or rewrites reference artifacts."""
import argparse
import copy
import json
import math
import subprocess
import sys
from pathlib import Path

from baseline_batch import ARTIFACTS
from baseline_environment import capture
from common import K6_ROOT, file_sha256, measured, read_json, result_now, result_stamp, write_json
from pilot_config import load_config
from pilot_observations import OUTCOME_TRENDS
from pilot_runner import plan

PASS = 'PASS'
SERVER_FAILURE = 'VALID MEASUREMENT / SERVER FAILURE OBSERVED'
FAIL = 'LOAD DELIVERY FAILED'
OUTCOME_CHECKS = {'no_system_failures', 'system_failure_observation', 'no_safety_guard_termination',
                  'sample_policy', 'latency_samples_present'}
DELIVERY_CHECKS = {'arrival_boundary', 'configured_slot_accounting', 'no_dropped_iterations',
                   'no_unfinished_iterations', 'starts_after_arrival', 'arrival_stage_1',
                   'arrival_stage_2', 'arrival_stage_3'}
INTEGRITY_CHECKS = {'summary_saved', 'raw_points', 'journey_accounting', 'required_metrics',
                    'raw_summary_counts', 'vus_samples', 'latency_samples_present', 'sample_policy',
                    'seat_inventory', 'prometheus_collection'}


def fixed_sources():
    paths = [*K6_ROOT.glob('*.js'), *K6_ROOT.glob('lib/*.js')]
    paths.extend(K6_ROOT / name for name in ('config/profiles.json', 'config/fixture-template.json',
                 'docs/metric-inventory.json', 'scripts/pilot_config.py', 'scripts/pilot_runner.py',
                 'scripts/run-experiment.py', 'scripts/prepare-fixture.py', 'scripts/preparation.py',
                 'scripts/test_accounts.py', 'scripts/fixture_state.py', 'scripts/fixture_cleanup.py',
                 'scripts/seat_inventory.py', 'scripts/metrics_tunnel.py', 'scripts/pilot_prometheus.py'))
    return {str(path.relative_to(K6_ROOT)): file_sha256(path) for path in sorted(paths)}


def condition(config):
    """Only the two nonzero measured targets may differ between candidate configs."""
    fixed = copy.deepcopy(config)
    fixed['measurement']['stages'][0]['target'] = '<candidate peak>'
    fixed['measurement']['stages'][1]['target'] = '<candidate peak>'
    return fixed


def candidate_config(config, peak):
    if type(peak) is not int or peak <= 0:
        raise ValueError('Candidate peak must be a positive integer')
    execution = config['measurement']
    if (execution['startRate'] != 0 or execution['timeUnit'] != '1s'
            or len(execution['stages']) != 3
            or [s['duration'] for s in execution['stages']] != ['60s'] * 3
            or execution['stages'][2]['target'] != 0):
        raise ValueError('Bracketing retains the reviewed 180s ramp shape')
    result = copy.deepcopy(config)
    for stage in result['measurement']['stages'][:2]:
        stage['target'] = peak
    return result


def midpoint(lower, upper, increment=5):
    # Halfway ties round upwards, explicitly avoiding Python's bankers rounding.
    peak = math.floor(((lower + upper) / 2) / increment + .5) * increment
    if not lower < peak < upper:
        raise ValueError('No rounded candidate strictly inside the bracket')
    return peak


def next_candidate(lower, upper, anchor=20, resolution=20, increment=5):
    if lower is None:
        return anchor
    return None if upper - lower <= resolution else midpoint(lower, upper, increment)


def demand(observation, rate):
    journey = observation['latency_ms']['journey_duration']
    iteration = observation['latency_ms']['iteration_duration']
    return {'peak': rate, 'journey_p50_product': rate * journey['p50'] / 1000,
            'journey_p95_product': rate * journey['p95'] / 1000,
            'journey_mean_product': rate * journey['avg'] / 1000,
            'iteration_mean_product': rate * iteration['avg'] / 1000,
            'basis': 'Stress-case projection using invalid High-run durations, not a prediction at this rate. '
                     'Percentile products are not a concurrency confidence interval; actual VU occupancy includes iteration overhead.'}


def review_plan(path):
    declaration = read_json(path)
    if (declaration.get('schemaVersion') != 1 or declaration['profile'] != 'musical-high'
            or declaration['lowerAnchorPeak'] != 20 or declaration['upperFailingPeak'] != 150
            or {m['workloadModel'] for m in declaration['models']} != {'single-attempt', 'reselection'}
            or len(declaration['models']) != 2):
        raise ValueError('This search fixes musical-high and reviews each model separately')
    for name in ('roundingIncrement', 'resolution'):
        if type(declaration[name]) is not int or declaration[name] <= 0:
            raise ValueError(name + ' must be a positive integer')
    first = midpoint(20, 150, declaration['roundingIncrement'])
    entries, fingerprints, sources = [], set(), fixed_sources()
    for model in declaration['models']:
        source, config = load_config(K6_ROOT / model['config'], kind='baseline')
        if config['profile'] != declaration['profile'] or config['workloadModel'] != model['workloadModel']:
            raise ValueError('Model declaration differs from config')
        reference_dir = K6_ROOT / model['failingReference']
        reference = read_json(reference_dir / 'run-metadata.json')
        reference_config = read_json(reference_dir / 'pilot-config.json')
        observation = read_json(reference_dir / 'pilot-observations.json')
        environment = read_json(reference_dir / 'server-environment.json')
        if condition(config) != condition(reference_config) or not environment['unchanged']:
            raise ValueError('High reference has different fixed conditions')
        if any(reference['source_sha256'].get(name) != sha for name, sha in sources.items()):
            raise ValueError('Workload/fixture/auth/collection source differs from the High reference')
        if (max(s['target'] for s in reference['execution']['stages']) != 150
                or observation['count_totals']['dropped_iterations'] <= 0):
            raise ValueError('Upper reference must be an observed failed delivery at 150/s')
        fingerprints.add(environment['before']['comparison_sha256'])
        context = read_json(K6_ROOT / model['passingContext'] / 'run-metadata.json')
        anchor = candidate_config(config, declaration['lowerAnchorPeak'])
        candidate = candidate_config(config, first)
        entries.append({**model, 'config_source': str(source), 'config_sha256': file_sha256(source),
                        'fixed_config': config, 'fixture_seat_count': plan(config, 'baseline')['seat_count'],
                        'passing_context_profile': context['profile'],
                        'passing_context_is_same_condition_anchor': False,
                        'highest_valid_delivered_peak': None, 'first_invalid_delivered_peak': 150,
                        'upper_reference_requires_same_server_fingerprint': True,
                        'anchor_effective_config': anchor, 'first_midpoint_effective_config': candidate,
                        'rough_vu_demand': [demand(observation, rate) for rate in (150, first)],
                        'reference_journey_latency_ms': observation['latency_ms']['journey_duration'],
                        'reference_iteration_latency_ms': observation['latency_ms']['iteration_duration'],
                        'reference_active_vu_max': observation['gauge_observations']['vus']['max'],
                        'reference_dropped_iterations': observation['count_totals']['dropped_iterations'],
                        'reference_unobserved_configured_slots': observation['unobserved_configured_slots']})
    if len(fingerprints) != 1:
        raise ValueError('Reference server identities differ')
    a, b = [m['fixed_config'] for m in entries]
    for name in ('profile', 'fixtureTemplate', 'randomSeed', 'requestTimeout', 'auth', 'warmup', 'prometheus', 'validation', 'inventory'):
        if a[name] != b[name]:
            raise ValueError('Models differ in a reviewed common condition: ' + name)
    return {'created_at': result_now(), 'kind': 'load-delivery-bracket', 'mode': 'plan-only; no server access or k6 run',
            'declaration': declaration, 'plan_source': str(path), 'plan_sha256': file_sha256(path),
            'reference_server_fingerprint': fingerprints.pop(), 'models': entries,
            'fixed_source_sha256': sources,
            'fixed_profile_settings': read_json(K6_ROOT / 'config/profiles.json')[declaration['profile']],
            'first_new_run_per_model': 20, 'first_midpoint_after_anchor_passes': first,
            'rounding_rule': 'Nearest ' + str(declaration['roundingIncrement']) + '; halfway ties upwards',
            'stop_resolution': 'FAIL peak minus highest PASS peak <= ' + str(declaration['resolution']) + ' journey/s; search resolution only',
            'repeats': 'One per search candidate. Three-run endpoint confirmation only after narrowing and review; never after generator stop.',
            'automatic_stop': 'Ceiling reached plus failed generation, non-arrival integrity failure, server/config drift, or anchor failure',
            'duration_per_candidate': 'Separate 60s warm-up + 180s measured arrival, actual drain (90s allowance each), preparation/audit/collection/cleanup',
            'profile_note': 'The musical-low 20/s PASS is context, not a matched musical-high lower endpoint.'}


def inspect_run(directory):
    """New read-only verdict; does not change historic Pilot/Baseline validation."""
    directory = Path(directory)
    metadata = read_json(directory / 'run-metadata.json')
    observations = read_json(directory / 'pilot-observations.json')
    inventory = read_json(directory / 'seat-inventory.json')
    cleanup = read_json(directory / 'pilot-cleanup.json')
    prometheus = read_json(directory / 'prometheus/collection-status.json')
    execution = observations['configured_execution']
    if metadata['phase'] != 'baseline':
        raise ValueError('Delivery classification is Baseline-only')
    checks = observations['validation']['checks']
    delivery_failures = {name: checks.get(name, {'status': 'missing'}) for name in DELIVERY_CHECKS
                         if checks.get(name, {}).get('status') != 'passed'}
    integrity_failures = {name: value for name, value in checks.items()
                          if name not in OUTCOME_CHECKS | DELIVERY_CHECKS and value['status'] != 'passed'}
    required = set(INTEGRITY_CHECKS)
    if metadata['workloadModel'] == 'reselection':
        required.update(('reselection_accounting', 'retry_samples'))
        required.update('latency_accounting_' + name for name in OUTCOME_TRENDS)
    integrity_failures.update({name: {'status': 'missing'} for name in required - checks.keys()})
    missing = [name for name in (*ARTIFACTS, 'server-environment.json') if not (directory / name).is_file()]
    if missing:
        integrity_failures['artifacts'] = missing
    initial = read_json(directory / 'fixture-initial-seats.json')
    manifest = read_json(directory / 'fixture-manifest.json')
    warm = metadata.get('warmup_run') or {}
    isolated = bool(warm.get('performance_time_id')) and warm['performance_time_id'] != metadata['performance_time_id']
    fixture_valid = (len(initial) == metadata['seat_count'] == manifest['verified_seat_count']
                     and len({s['id'] for s in initial}) == len(initial)
                     and all(s['status'] == 'AVAILABLE' for s in initial))
    for name, valid in [('fixture_initial_state', fixture_valid), ('fixture_isolation', isolated),
                        ('fixture_cleanup', not cleanup['errors']),
                        ('prometheus_collection', metadata['prometheus_collection_status'] == prometheus['status'] == 'collected'
                         and prometheus['queries_executed_after_k6_exit']),
                        ('inventory_audit', inventory['status'] == 'verified' and inventory['oversell_invariant'] == 'passed'),
                        ('k6_process_completed', metadata['k6_exit_code'] in {0, 99})]:
        if not valid:
            integrity_failures[name] = 'failed'
    if (directory / 'server-environment.json').exists() and not read_json(directory / 'server-environment.json')['unchanged']:
        integrity_failures['server_environment'] = 'changed'
    counts = observations['count_totals']
    # Zero payment/reservation samples after early failure or sell-out do not mean arrival was undelivered.
    expected_samples = {'journey_duration': counts['journey_finished'], 'iteration_duration': counts['iterations'],
                        'http_req_duration': counts['http_reqs']}
    for name, expected in expected_samples.items():
        actual = observations['latency_ms'][name]['count']
        if actual != expected or actual <= 0:
            integrity_failures['latency_sample_accounting_' + name] = {'expected': expected, 'actual': actual}
    if counts['dropped_iterations']:
        delivery_failures['no_dropped_iterations'] = {'status': 'failed', 'count': counts['dropped_iterations']}
    active, ceiling = observations['gauge_observations']['vus']['max'], execution['maxVUs']
    ceiling_reached = active is not None and active >= ceiling
    valid = not delivery_failures and not integrity_failures
    classification = (SERVER_FAILURE if counts['system_failures'] else PASS) if valid else FAIL
    warnings = []
    if checks.get('sample_policy', {}).get('status') != 'passed':
        warnings.append('API sample screening is insufficient; preserve counts/N/A percentiles separately from arrival delivery.')
    if ceiling_reached:
        warnings.append('Active VUs reached the configured ceiling; generator limitation is possible.')
    if counts.get('booking_journey_guard_stopped', 0):
        warnings.append('Technical safety guard termination observed separately; it is not a dropped/unfinished iteration.')
    generator_stop = ceiling_reached and bool(delivery_failures)
    return {'directory': str(directory), 'profile': metadata['profile'], 'workloadModel': metadata['workloadModel'],
            'run_number': metadata['run_number'], 'target_peak': max(s['target'] for s in execution['stages']),
            'configured_average_starts_per_second': observations['configured_expected_starts'] / observations['configured_arrival_duration_seconds'],
            'classification': classification, 'valid_load_delivery': valid,
            'failure_scope': 'measurement-integrity' if integrity_failures else 'arrival-generation' if delivery_failures else None,
            'usable_as_failed_rate_endpoint': bool(delivery_failures) and not integrity_failures,
            'delivery_failures': delivery_failures, 'integrity_failures': integrity_failures,
            'ceiling_reached': ceiling_reached, 'generator_stop': generator_stop,
            'stop_automatic_execution': generator_stop or bool(integrity_failures), 'warnings': warnings,
            'k6_exit_code': metadata['k6_exit_code'], 'legacy_validation_status': observations['validation']['status'],
            'api_sample_screening': checks.get('sample_policy'),
            'iteration_meaning': metadata['iteration_meaning'], 'counts': counts,
            'observed_starts_per_second': observations['observed_iteration_start_rate_per_second'],
            'completed_iterations_per_second': observations['completed_iteration_throughput_per_second'],
            'http_throughput_per_second': observations['http_throughput_per_second'],
            'active_vu_max': active, 'configured_vu_ceiling': ceiling, 'fixture_isolated': isolated,
            'unobserved_configured_slots': observations['unobserved_configured_slots'],
            'latency_ms': observations['latency_ms'], 'api_latency_ms': observations['api_latency_ms'],
            'successful_booking_latency_ms': observations['successful_booking_latency_ms'],
            'outcome_latency_ms': observations['outcome_latency_ms'], 'retry_distributions': observations['retry_distributions'],
            'business_failure_reasons': observations['business_failure_reasons'],
            'system_failure_reasons': observations['system_failure_reasons'], 'http_5xx_requests': observations['http_5xx_requests'],
            'seat_selection_regions': observations['seat_selection_regions'], 'stage_observations': observations['stage_observations'],
            'inventory': inventory, 'prometheus_status': metadata['prometheus_collection_status'],
            'rough_vu_demand_at_peak': demand(observations, max(s['target'] for s in execution['stages']))}


def write_report(directory, state):
    lines = ['# Load-delivery Bracketing', '', 'KST. Peak is iteration starts/s, not HTTP RPS. This is not server maximum throughput.',
             '', 'Status: ' + state['status'], '', 'Fixed musical-high: 1500 seats, Hot range 20%, Hot traffic 65%, 1/2/3 seats 50/35/15.',
             '180s arrival: 60s ramp up / 60s hold / 60s ramp down. Configured average = peak × 2/3.',
             'Round to nearest 5 (ties upwards); interval width <=20 journey/s is search resolution only.',
             'The previous musical-low 20/s PASS is not a matched lower endpoint.', '']
    for model, endpoints in state['brackets'].items():
        lines.append(f"- {model}: highest valid delivered peak={endpoints['highest_valid_delivered_peak']}; first invalid delivered peak={endpoints['first_invalid_delivered_peak']}")
    for run in state['runs']:
        c, db = run['counts'], run['inventory'].get('db') or {}
        lines.extend(['', '## ' + run['workloadModel'] + ' / peak ' + str(run['target_peak']), '',
                      f"Classification: {run['classification']} ({run['failure_scope'] or 'valid measurement'}). Legacy validation: {run['legacy_validation_status']}.",
                      '', '| Observation | Value |', '|---|---|'])
        values = [('Configured average starts/s', run['configured_average_starts_per_second']),
                  ('Observed starts', c['iteration_started']), ('Observed starts/s', run['observed_starts_per_second']),
                  ('Completed iterations/s (arrival + actual drain)', run['completed_iterations_per_second']),
                  ('Dropped iterations', c['dropped_iterations']), ('Unobserved configured slots', run['unobserved_configured_slots']),
                  ('Active VU max / ceiling', f"{run['active_vu_max']} / {run['configured_vu_ceiling']}"),
                  ('HTTP requests/s (arrival + actual drain)', run['http_throughput_per_second']),
                  ('System failures', c['system_failures']), ('Business failures', c['business_failures']),
                  ('Booking completed', c['booking_completed']), ('Client seats confirmed', c.get('seats_confirmed')),
                  ('DB confirmed assignments', db.get('confirmed_seat_assignments')), ('DB unique sold seats', db.get('unique_sold_seat_count')),
                  ('Sell-through (DB unique sold / initial)', db.get('seat_sell_through_rate')),
                  ('Oversell invariant', db.get('oversell_invariant')), ('Prometheus', run['prometheus_status'])]
        values.extend((name, c.get(name, 0)) for name in ('booking_journey_completed', 'booking_journey_sold_out', 'booking_journey_system_failed',
                      'booking_journey_guard_stopped', 'seat_conflicts', 'reselections', 'pre_reserve_attempts', 'seat_selection_attempts',
                      'seat_selection_fallbacks', 'reselection_stale_refreshes') if run['workloadModel'] == 'reselection')
        for name, value in values:
            lines.append('| ' + name + ' | ' + (measured(value) if isinstance(value, (int, float)) or value is None else str(value)) + ' |')
        lines.extend(['', '| Latency / retry population | Count | avg | p50 | p95 | p99 | max |', '|---|---|---|---|---|---|---|'])
        populations = {**run['latency_ms'], **{'API ' + a: v for a, v in run['api_latency_ms'].items()}}
        populations.update(run['outcome_latency_ms'] if run['workloadModel'] == 'reselection' else {'Successful Booking': run['successful_booking_latency_ms']})
        populations.update(run['retry_distributions'])
        for name, values in populations.items():
            lines.append('| ' + name + ' | ' + ' | '.join(measured(values[k]) for k in ('count', 'avg', 'p50', 'p95', 'p99', 'max')) + ' |')
        lines.extend(['', 'Latency units: ms; retry distributions: retries/journey.', '',
                      'Business reasons: ' + json.dumps(run['business_failure_reasons']),
                      'System reasons: ' + json.dumps(run['system_failure_reasons']),
                      'Selection regions: ' + json.dumps(run['seat_selection_regions']),
                      'Delivery failures: ' + json.dumps(run['delivery_failures']),
                      'Integrity failures: ' + json.dumps(run['integrity_failures']),
                      'Warnings: ' + ' '.join(run['warnings']),
                      '', '[Full run report](' + str(Path(run['directory']) / 'report.md') + ')'])
    lines.extend(['', *state['stop_reasons'], ''])
    (directory / 'report.md').write_text('\n'.join(lines), encoding='utf-8')


def execute(preview):
    """Sequential search. On the first generator stop, no other candidate/model/repeat starts."""
    directory = K6_ROOT / 'results' / (result_stamp() + '_load-delivery_bracket')
    directory.mkdir(parents=True)
    write_json(directory / 'bracket-plan.json', preview)
    initial = capture()
    write_json(directory / 'server-environment-initial.json', initial)
    state = {'started_at': result_now(), 'status': 'running', 'runs': [], 'stop_reasons': [],
             'brackets': {m['workloadModel']: {'highest_valid_delivered_peak': None, 'first_invalid_delivered_peak': 150}
                          for m in preview['models']}}
    print('Bracket directory: ' + str(directory), flush=True)
    settings = preview['declaration']
    if initial['comparison_sha256'] != preview['reference_server_fingerprint']:
        state['stop_reasons'].append('Server fingerprint differs from the 150/s references; no run started and neither reference is a matched endpoint.')
        for endpoints in state['brackets'].values():
            endpoints['first_invalid_delivered_peak'] = None
    try:
        for entry in preview['models']:
            endpoints = state['brackets'][entry['workloadModel']]
            while not state['stop_reasons']:
                if (file_sha256(preview['plan_source']) != preview['plan_sha256']
                        or any(file_sha256(m['config_source']) != m['config_sha256'] for m in preview['models'])
                        or fixed_sources() != preview['fixed_source_sha256']):
                    state['stop_reasons'].append('Frozen declaration/config changed; no subsequent run started.')
                    break
                peak = next_candidate(endpoints['highest_valid_delivered_peak'], endpoints['first_invalid_delivered_peak'],
                                      settings['lowerAnchorPeak'], settings['resolution'], settings['roundingIncrement'])
                if peak is None:
                    break
                before = capture()
                if before['comparison_sha256'] != initial['comparison_sha256']:
                    state['stop_reasons'].append('Server identity/config drift before candidate; no run started.')
                    break
                candidate = candidate_config(entry['fixed_config'], peak)
                config_path = directory / f"{entry['workloadModel']}-peak-{peak}.json"
                write_json(config_path, candidate)
                existing = set((K6_ROOT / 'results').iterdir())
                print(f"Candidate: {entry['workloadModel']} peak={peak}/s, average={peak * 2 / 3:.3f}/s; VUs unchanged", flush=True)
                process = subprocess.run([sys.executable, str(K6_ROOT / 'scripts/run-experiment.py'), 'baseline',
                                          '--config', str(config_path), '--run-number', '1'], cwd=K6_ROOT.parent, check=False)
                created = set((K6_ROOT / 'results').iterdir()) - existing
                runs = [p for p in created if (p / 'run-metadata.json').exists()
                        and read_json(p / 'run-metadata.json')['phase'] == 'baseline']
                if len(runs) != 1:
                    state['stop_reasons'].append('Expected one measured artifact; runner exit ' + str(process.returncode))
                    break
                after = capture()
                unchanged = before['comparison_sha256'] == after['comparison_sha256'] == initial['comparison_sha256']
                write_json(runs[0] / 'server-environment.json', {'before': before, 'after': after, 'unchanged': unchanged})
                run = inspect_run(runs[0])
                if condition(read_json(runs[0] / 'pilot-config.json')) != condition(entry['fixed_config']):
                    raise ValueError('Measured fixed conditions differ from the frozen plan')
                write_json(runs[0] / 'load-delivery-validation.json', run)
                state['runs'].append(run)
                if run['valid_load_delivery']:
                    endpoints['highest_valid_delivered_peak'] = peak
                elif run['usable_as_failed_rate_endpoint']:
                    endpoints['first_invalid_delivered_peak'] = peak
                if run['stop_automatic_execution']:
                    state['stop_reasons'].append(f"Stopped at {run['workloadModel']} {peak}/s: "
                                                + ('VU ceiling plus failed arrival generation.' if run['generator_stop'] else 'Measurement integrity needs review.'))
                elif endpoints['highest_valid_delivered_peak'] is None:
                    state['stop_reasons'].append('The matched 20/s anchor failed; no PASS/FAIL bracket has been established.')
                write_json(directory / 'bracket-results.json', state)
                write_report(directory, state)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        state['stop_reasons'].append('Candidate orchestration failed: ' + type(error).__name__ + '; no subsequent run started.')
    finally:
        state.update(status='stopped-for-review' if state['stop_reasons'] else 'narrowed-awaiting-endpoint-repeats', ended_at=result_now())
        write_json(directory / 'bracket-results.json', state)
        write_report(directory, state)
    print('Bracket report: ' + str(directory / 'report.md'), flush=True)
    return 1 if state['stop_reasons'] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=K6_ROOT / 'config/load-delivery-bracket.json')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--plan', action='store_true')
    modes.add_argument('--execute', action='store_true')
    modes.add_argument('--review-run', type=Path, help='Read-only classification; never changes the referenced run')
    args = parser.parse_args()
    if args.review_run:
        print(json.dumps(inspect_run(args.review_run), ensure_ascii=False, indent=2))
        return 0
    preview = review_plan(args.config.resolve())
    if not args.execute:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0
    return execute(preview)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print('Cannot bracket load delivery: ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(2)
