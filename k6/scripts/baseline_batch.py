"""Run only a reviewed four-run Baseline batch; freeze files and stop on measurement anomalies."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from common import K6_ROOT, file_sha256, read_json, result_now, result_stamp, write_json
from pilot_config import load_config
from pilot_runner import plan
from baseline_environment import capture

ARTIFACTS = ('run-metadata.json', 'k6-summary.json', 'k6-summary.txt', 'k6-points.json', 'report.md',
    'fixture-manifest.json', 'fixture-initial-seats.json', 'fixture-final-seats.json', 'seat-inventory.json',
    'seat-inventory-db-snapshot.json', 'pilot-observations.json', 'prometheus/http.json', 'prometheus/tomcat.json',
    'prometheus/hikari.json', 'prometheus/jvm.json', 'prometheus/collection-status.json', 'pilot-cleanup.json')


def reviewed_plan(path):
    batch = read_json(path)
    if batch.get('schemaVersion') != 1 or len(batch.get('runs', [])) != 4:
        raise ValueError('First batch must declare exactly four reviewed runs')
    configs, entries = {}, []
    for run in batch['runs']:
        source, config = load_config(K6_ROOT / run['config'], kind='baseline')
        if config['profile'] != run['profile'] or config['workloadModel'] != run['workloadModel']:
            raise ValueError('Batch declaration differs from effective config')
        if run['runNumber'] != 1:
            raise ValueError('The first batch executes Run 1 only')
        configs[str(source)] = file_sha256(source)
        preview = plan(config, 'baseline')
        entries.append({**run, 'config_source': str(source), 'config_sha256': configs[str(source)],
            'effective_config': config, 'seat_count': preview['seat_count'],
            'stage_schedule': preview['stage_schedule'], 'configured_expected_starts': preview['configured_expected_starts'],
            'scrape_opportunities_per_stage': preview['scrape_opportunities_per_stage']})
    for profile in {r['profile'] for r in entries}:
        pair = [r for r in entries if r['profile'] == profile]
        if len(pair) != 2 or {r['workloadModel'] for r in pair} != {'single-attempt', 'reselection'}:
            raise ValueError('Each representative profile must include both separate models')
        a, b = [r['effective_config'] for r in pair]
        for key in ('profile', 'fixtureTemplate', 'randomSeed', 'requestTimeout', 'auth', 'warmup', 'prometheus', 'validation', 'inventory'):
            if a[key] != b[key]:
                raise ValueError('Paired configs differ in common condition: ' + key)
        for key in ('executor', 'timeUnit', 'startRate', 'stages', 'gracefulStop'):
            if a['measurement'][key] != b['measurement'][key]:
                raise ValueError('Paired arrival configs differ: ' + key)
    return {'created_at': result_now(), 'kind': 'baseline-first-batch', 'runs': entries,
        'config_sha256': configs, 'batch_source': str(path), 'batch_sha256': file_sha256(path),
        'measured_runs': 4, 'warmup_runs': 4, 'fixtures': 8,
        'fixed_workload_seconds': 4 * (60 + 180),
        'drain_allowance_seconds': 8 * 90,
        'scope': 'Four runs only. No automatic config adjustment, optimization or expansion to the remaining 56 runs.'}


def inspect_run(directory):
    directory = Path(directory)
    metadata = read_json(directory / 'run-metadata.json')
    observations = read_json(directory / 'pilot-observations.json')
    cleanup = read_json(directory / 'pilot-cleanup.json')
    inventory = read_json(directory / 'seat-inventory.json')
    missing = [name for name in ARTIFACTS if not (directory / name).is_file()]
    checks = observations['validation']['checks']
    failures = {name: value for name, value in checks.items() if value['status'] == 'failed'}
    warm = metadata.get('warmup_run') or {}
    isolated = warm.get('performance_time_id') != metadata['performance_time_id'] and bool(warm.get('performance_time_id'))
    ready = (not missing and not failures and not cleanup['errors'] and isolated
        and metadata['prometheus_collection_status'] == 'collected' and inventory['status'] == 'verified'
        and checks['sample_policy']['status'] == 'passed')
    return {'directory': str(directory), 'run_id': metadata['run_id'], 'profile': metadata['profile'],
        'workloadModel': metadata['workloadModel'], 'run_number': metadata['run_number'],
        'measurement_integrity': 'passed' if ready else 'needs-review', 'missing_artifacts': missing,
        'failed_checks': failures, 'validation': observations['validation']['status'],
        'warmup_isolated': isolated, 'cleanup_errors': cleanup['errors'], 'observations': observations,
        'inventory': inventory, 'prometheus_status': metadata['prometheus_collection_status'],
        'k6_exit_code': metadata['k6_exit_code']}


def write_report(directory, state):
    lines = ['# Baseline First Batch', '', 'Times: Asia/Seoul (KST). Server outcomes are observations; no cause or bottleneck verdict.',
        '', '| Profile | Model | Run | Configured avg /s (peak) | Observed /s | Completed /s | Dropped | Active VU max | Booking | Seats | Sell-through | System | Oversell | Prometheus | Measurement integrity |',
        '|' + '|'.join(['---'] * 15) + '|']
    for run in state['completed_runs']:
        o, inv = run['observations'], run['inventory'].get('db') or {}
        c = o['count_totals']
        e = o['configured_execution']
        avg = o['configured_expected_starts'] / o['configured_arrival_duration_seconds']
        peak = max(e['startRate'], *(s['target'] for s in e['stages']))
        row = [run['profile'], run['workloadModel'], run['run_number'], f'{avg:.3f} ({peak})',
            f"{o['observed_iteration_start_rate_per_second']:.3f}" if o['observed_iteration_start_rate_per_second'] is not None else 'N/A',
            f"{o['completed_iteration_throughput_per_second']:.3f}" if o['completed_iteration_throughput_per_second'] is not None else 'N/A',
            c['dropped_iterations'], o['gauge_observations']['vus']['max'], c['booking_completed'], c.get('seats_confirmed'),
            inv.get('seat_sell_through_rate'), c['system_failures'], inv.get('oversell_invariant'), run['prometheus_status'], run['measurement_integrity']]
        lines.append('| ' + ' | '.join(str(x) for x in row) + ' |')
    lines.extend(['', 'Status: ' + state['status'], '', 'Full API/stage sample counts, latency populations, retry distributions, inventory and resource series are retained in each linked run.', ''])
    for run in state['completed_runs']:
        rel = Path(run['directory']).relative_to(directory.parent)
        lines.append(f"- [{run['run_id']}](../{rel}/report.md)")
    lines.extend(['', 'No remaining profiles run automatically. Expansion requires reviewing generator drops, VU headroom, samples, collection, isolation and invariant checks.', ''])
    (directory / 'report.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, default=K6_ROOT / 'config' / 'baseline-first-batch.json')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--plan', action='store_true')
    group.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    preview = reviewed_plan(args.batch.resolve())
    if not args.execute:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0
    directory = K6_ROOT / 'results' / (result_stamp() + '_baseline_first-batch')
    directory.mkdir(parents=True)
    write_json(directory / 'batch-plan.json', preview)
    identity = capture()
    write_json(directory / 'server-environment-initial.json', identity)
    state = {'status': 'running', 'started_at': result_now(), 'completed_runs': [], 'errors': []}
    print('Batch directory: ' + str(directory), flush=True)
    write_json(directory / 'batch-results.json', state)
    for entry in preview['runs']:
        if any(file_sha256(path) != sha for path, sha in preview['config_sha256'].items()) or file_sha256(args.batch) != preview['batch_sha256']:
            state['errors'].append('Reviewed config changed; no subsequent run started')
            break
        before = capture()
        if before['comparison_sha256'] != identity['comparison_sha256']:
            state['errors'].append('Server identity/configuration changed before run; no subsequent run started')
            break
        existing = set((K6_ROOT / 'results').iterdir())
        command = [sys.executable, str(K6_ROOT / 'scripts' / 'run-experiment.py'), 'baseline', '--config', entry['config_source'], '--run-number', '1']
        result = subprocess.run(command, cwd=K6_ROOT.parent, check=False)
        new_dirs = set((K6_ROOT / 'results').iterdir()) - existing
        measured = [p for p in new_dirs if (p / 'run-metadata.json').exists() and read_json(p / 'run-metadata.json').get('phase') == 'baseline']
        if len(measured) != 1:
            state['errors'].append('Expected exactly one measured run artifact; runner exit ' + str(result.returncode))
            break
        run_dir = measured[0]
        after = capture()
        write_json(run_dir / 'server-environment.json', {'before': before, 'after': after,
            'unchanged': before['comparison_sha256'] == after['comparison_sha256'] == identity['comparison_sha256']})
        run = inspect_run(run_dir)
        run['server_environment_unchanged'] = before['comparison_sha256'] == after['comparison_sha256'] == identity['comparison_sha256']
        state['completed_runs'].append(run)
        if result.returncode or run['measurement_integrity'] != 'passed' or not run['server_environment_unchanged']:
            state['errors'].append('First-batch measurement requires review; configuration remains fixed and later runs were not started')
            break
        write_json(directory / 'batch-results.json', state)
        write_report(directory, state)
    state.update(status='completed' if len(state['completed_runs']) == 4 and not state['errors'] else 'needs-review', ended_at=result_now())
    write_json(directory / 'batch-results.json', state)
    write_report(directory, state)
    print('Batch report: ' + str(directory / 'report.md'), flush=True)
    return 0 if state['status'] == 'completed' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print('Cannot run Baseline batch: ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(2)
