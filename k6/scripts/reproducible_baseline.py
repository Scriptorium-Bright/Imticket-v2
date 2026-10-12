"""Finite 15-run Preflight -> 60 new runs. Stop on defects; no retries/config changes."""
import argparse
import fcntl
import os
import subprocess
import sys
from pathlib import Path

from baseline_environment import capture as capture_server
from common import K6_ROOT, REPO_ROOT, file_sha256, http_url, read_json, result_now, result_stamp, write_json
from measurement_environment import capture_local, assert_environment, git_identity
from pilot_config import load_config
from reproducibility_report import anomaly_evidence, write_campaign_report
from report import generate_report
from vu_baseline_series import inspect_run

PROFILES = ('rapper-low', 'musical-low', 'ballad-low', 'overseas-low', 'idol-low',
            'ballad-high', 'rapper-high', 'musical-high', 'idol-high', 'overseas-high')
MODELS = ('single-attempt', 'reselection')
TARGETS = dict(zip(PROFILES, (400, 500, 500, 700, 800, 2000, 2500, 3000, 5000, 6000)))
PREFLIGHT = (('rapper-low', 'single-attempt'), ('idol-low', 'reselection'),
             ('musical-high', 'single-attempt'), ('idol-high', 'reselection'), ('overseas-high', 'reselection'))


def save(root, state):
    write_json(root / 'series-results.json', state)
    write_campaign_report(root, state)


def prepare(exploratory_index):
    base_url = http_url(os.environ.get('BASE_URL'), 'BASE_URL')
    root = K6_ROOT / 'results' / (result_stamp() + '_reproducible-target-vu-campaign')
    (root / 'configs').mkdir(parents=True)
    state = {'status': 'preparing', 'created_at': result_now(), 'results': [], 'active_run': None}
    write_json(root / 'series-results.json', state)
    try:
        local = capture_local(base_url)
        write_json(root / 'load-generator-initial.json', local)
        assert_environment(local)
        server = capture_server()
        write_json(root / 'server-environment-initial.json', server)
        old = read_json(exploratory_index)['accepted_runs']
        configs = {}
        for profile in PROFILES:
            for model in MODELS:
                accepted = [r for r in old if r['profile'] == profile and r['workloadModel'] == model and r['run_number'] == 1 and r['valid']]
                if len(accepted) != 1 or accepted[0]['target_vus'] != TARGETS[profile]:
                    raise ValueError('Expected one Exploratory Run 1 source: ' + profile + '/' + model)
                config = read_json(Path(accepted[0]['directory']) / 'pilot-config.json')
                if config['measurement']['executor'] != 'per-vu-iterations' or config['measurement']['iterations'] != 1 or config['requestTimeout'] != '10s':
                    raise ValueError('Source workload definition mismatch')
                guard = '5m' if profile in ('idol-high', 'overseas-high') else '60s'
                if model == 'reselection' and config['reselection']['safetyGuard']['maxJourneyDuration'] != guard:
                    raise ValueError('Source safety guard mismatch')
                path = root / 'configs' / f'{profile}.{model}.json'
                write_json(path, config)
                _, effective = load_config(path, kind='baseline')
                if effective != config:
                    raise ValueError('Source config does not reproduce its effective values')
                configs[profile + '/' + model] = {'path': str(path), 'sha256': file_sha256(path),
                                                'exploratory_source': accepted[0]['directory']}
        classification = {'Exploratory Baseline': [{'directory': r['directory'], 'profile': r['profile'],
            'workloadModel': r['workloadModel'], 'run': r['run_number']} for r in old], 'Stress Observation': []}
        for path in (K6_ROOT / 'results').glob('*/run-metadata.json'):
            m = read_json(path)
            if m.get('configured_target_vus') in (8000, 10000):
                classification['Stress Observation'].append({'directory': str(path.parent), 'target_vus': m['configured_target_vus'],
                    'status': m.get('status'), 'phase': m.get('phase'), 'measurement_role': m.get('measurement_role')})
        write_json(root / 'existing-results-classification.json', classification)
        plan = {'series_id': root.name, 'harness_sha': local['identity']['git']['sha'],
                'exploratory_index': str(exploratory_index.resolve()), 'configs': configs,
                'preflight_combinations': [list(pair) for pair in PREFLIGHT], 'profile_order': list(PROFILES),
                'models': list(MODELS), 'repetitions': 3, 'preflight_measured_runs': 15, 'new_final_measured_runs': 60,
                'local_reference': local, 'server_reference': server,
                'anomaly_policy': {'fastSpreadSeconds': 1, 'outlierSpreadSeconds': 6,
                    'definition': 'Two spreads <1s and one >=6s within the same three-repeat combination, for Journey or first-request dispatch. Explicit input-shape anomaly, not performance SLA.'},
                'policy': 'No retry, cooldown, tuning, or config mutation. Business/server outcome variance is observed. Ambiguous evidence passes.'}
        write_json(root / 'series-plan.json', plan)
        state.update(status='prepared', harness_sha=plan['harness_sha'])
        save(root, state)
        return root
    except Exception as error:
        state.update(status='environment-preflight-failed', failure={'exception_class': type(error).__name__,
            'exception_str': str(error), 'classification': 'environment-or-config-preflight', 'automatic_retry': False})
        save(root, state)
        print('Preparation stopped; artifacts: ' + str(root), flush=True)
        raise


def execute_one(root, state, plan, category, profile, model, number):
    attempt = root / 'attempts' / ('preflight' if category == 'Preflight' else 'new-final') / f'{profile}_{model}_run-{number:02d}'
    attempt.mkdir(parents=True, exist_ok=False)
    config_record = plan['configs'][profile + '/' + model]
    record = {'category': category, 'profile': profile, 'workloadModel': model, 'run_number': number,
              'attempt_directory': str(attempt), 'started_at': result_now(), 'valid': False, 'directory': None, 'issues': []}
    state['active_run'] = record
    save(root, state)
    try:
        if any(file_sha256(item['path']) != item['sha256'] for item in plan['configs'].values()):
            raise ValueError('Frozen workload config changed')
        local = capture_local(os.environ['BASE_URL'])
        write_json(attempt / 'local-before.json', local)
        assert_environment(local, plan['local_reference'])
        server = capture_server()
        write_json(attempt / 'server-before.json', server)
        if server['comparison_sha256'] != plan['server_reference']['comparison_sha256']:
            raise ValueError('Server identity/configuration changed')
        context = {'series_id': plan['series_id'], 'category': category,
                   'local_reference': plan['local_reference'], 'server_reference': server}
        write_json(attempt / 'series-context.json', context)
        environment = dict(os.environ, K6_STRICT_SERIES='1', K6_SERIES_CONTEXT=str(attempt / 'series-context.json'),
                           K6_RUN_ATTEMPT_DIR=str(attempt))
        environment.pop('HARNESS_DIAGNOSTIC', None)
        environment.pop('K6_SERVER_TESTS', None)
        command = [sys.executable, str(K6_ROOT / 'scripts' / 'run-experiment.py'), 'baseline',
                   '--config', config_record['path'], '--run-number', str(number)]
        print(f'실행: {category} / {profile} / {model} / Run {number}', flush=True)
        with (attempt / 'runner.log').open('w', encoding='utf-8') as log:
            process = subprocess.run(command, env=environment, cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
        record['runner_exit_code'] = process.returncode
        sequence_path = attempt / 'preparation-sequence.json'
        sequence = read_json(sequence_path) if sequence_path.exists() else []
        directories = {entry['directory'] for entry in sequence if entry['action'] == 'run-artifact'}
        measured = [Path(path) for path in directories if (Path(path) / 'run-metadata.json').exists()
                    and read_json(Path(path) / 'run-metadata.json')['phase'] == 'baseline']
        if len(measured) == 1:
            directory = measured[0]
            record['directory'] = str(directory)
            result = inspect_run(directory, read_json(config_record['path']))
            record['issues'].extend(result['issues'])
            m = read_json(directory / 'run-metadata.json')
            o = read_json(directory / 'vu-baseline-observations.json')
            g = read_json(directory / 'load-generator-observations.json')
            if m['configured_target_vus'] != TARGETS[profile] or o['traffic']['max_vus_observed'] != TARGETS[profile]:
                record['issues'].append('expected-target-vu-mismatch')
            if m.get('harness_git_sha') != plan['harness_sha'] or m.get('working_tree_dirty') or m.get('harness_dirty'):
                record['issues'].append('git-sha-or-dirty-mismatch')
            if m.get('measurement_category') != category or g.get('docker_off_during_observation') is not True:
                record['issues'].append('category-or-docker-observation-mismatch')
            auth = read_json(attempt / 'auth-baseline.json')
            if auth['status'] != 'completed' or auth['completed_authentication_count'] != TARGETS[profile] or auth['failure_count']:
                record['issues'].append('jwt-preparation-invalid')
        else:
            record['issues'].append('measured-workload-not-created')
        if process.returncode:
            record['issues'].append('runner-exit-' + str(process.returncode))
        after_local = capture_local(os.environ['BASE_URL'])
        write_json(attempt / 'local-after.json', after_local)
        assert_environment(after_local, plan['local_reference'])
        after_server = capture_server()
        write_json(attempt / 'server-after.json', after_server)
        if after_server['comparison_sha256'] != plan['server_reference']['comparison_sha256']:
            record['issues'].append('server-environment-mismatch')
        record['valid'] = not record['issues']
    except Exception as error:
        record['issues'].append('environment-or-harness-exception')
        record['exception'] = {'class': type(error).__name__, 'repr': repr(error), 'str': str(error)}
    record['ended_at'] = result_now()
    write_json(attempt / 'validation.json', record)
    if record['directory']:
        metadata_path = Path(record['directory']) / 'run-metadata.json'
        metadata = read_json(metadata_path)
        metadata['series_validation'] = {'valid': record['valid'], 'issues': record['issues'], 'category': category,
                                         'environment_artifacts': str(attempt)}
        write_json(metadata_path, metadata)
        try:
            generate_report(record['directory'])
        except Exception as error:
            record['issues'].append('report-generation-failed')
            record['valid'] = False
            record['report_exception'] = {'class': type(error).__name__, 'str': str(error)}
            write_json(attempt / 'validation.json', record)
    state['results'].append(record)
    state['active_run'] = None
    if not record['valid']:
        state.update(status='stopped-invalid', failure=record, ended_at=result_now())
    save(root, state)
    print(f"결과: {'valid' if record['valid'] else 'INVALID'} / {profile} / {model} / Run {number}", flush=True)
    return record['valid']


def execute(root):
    root = root.resolve()
    with (root / 'execution.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan, state = read_json(root / 'series-plan.json'), read_json(root / 'series-results.json')
        if state['status'] != 'prepared' or state['results']:
            raise ValueError('Campaign is one-shot; resume/retry after a failure is forbidden')
        state.update(status='preflight-running', started_at=result_now())
        save(root, state)
        for number in (1, 2, 3):
            for profile, model in plan['preflight_combinations']:
                if not execute_one(root, state, plan, 'Preflight', profile, model, number):
                    return 2
        evidence = anomaly_evidence(state['results'], plan['anomaly_policy'])
        write_json(root / 'preflight-decision.json', {'status': 'FAILED' if evidence else 'PASSED',
            'at': result_now(), 'measured_runs': 15, 'hard_fail_evidence': evidence,
            'business_variance_is_a_failure_condition': False, 'ambiguous_evidence_passes': True})
        state['preflight_evidence'] = evidence
        if evidence:
            state.update(status='preflight-hard-failed', ended_at=result_now())
            save(root, state)
            return 2
        print('Preflight PASSED: 15/15. 새로운 Final Baseline 시작.', flush=True)
        state.update(status='new-final-running')
        save(root, state)
        for number in (1, 2, 3):
            for profile in plan['profile_order']:
                for model in plan['models']:
                    if not execute_one(root, state, plan, 'New Final Baseline', profile, model, number):
                        return 2
            print(f'New Final Baseline Run {number} = 20/20 valid', flush=True)
        state.update(status='completed', ended_at=result_now())
        save(root, state)
        print('New Final Baseline = 60/60 valid. 집계: ' + str(root / 'report.md'), flush=True)
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--exploratory-index', type=Path)
    modes.add_argument('--execute', type=Path)
    args = parser.parse_args()
    root = args.execute or prepare(args.exploratory_index)
    print('Campaign directory: ' + str(root), flush=True)
    return execute(root)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print('Campaign stopped: ' + type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(2)
