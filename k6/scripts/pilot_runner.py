"""One Pilot, fresh warm-up/measured fixtures and existing preparation/cleanup helpers."""
import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from common import K6_ROOT, http_url, positive_integer, read_json, write_json, result_now
from fixture_cleanup import clean_fixture, probe_database
from metrics_tunnel import prometheus_tunnel
from pilot_config import load_config, pending_settings, stage_schedule, token_window
from pilot_prometheus import query_plan
from preparation import token_pool_from_env
from test_accounts import prepare_accounts


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), K6_ROOT / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def plan(config, kind='pilot'):
    profile = read_json(K6_ROOT / 'config' / 'profiles.json')[config['profile']]
    arrival_axis = config.get('workloadAxis', 'iteration-arrival-rate') != 'target-vus'
    ready = arrival_axis and config['measurement']['startRate'] is not None and config['measurement']['stages'] is not None
    stages = stage_schedule(config['measurement']) if ready and kind != 'journey-smoke' else []
    return {"mode": "plan-only; no HTTP/SSH/k6 run", "config": config, "seat_count": profile['seatCount'],
            "workload_axis": config.get('workloadAxis', 'iteration-arrival-rate'),
            "target_vus": profile.get('targetUsers') if not arrival_axis else None,
            "stage_schedule": stages, "configured_expected_starts": sum(s['configured_expected_starts'] for s in stages) if stages else None,
            "scrape_opportunities_per_stage": [s['duration_seconds'] / config['prometheus']['scrapeIntervalSeconds'] for s in stages],
            "short_stage_warnings": [s['stage'] for s in stages if s['duration_seconds'] < 6 * config['prometheus']['scrapeIntervalSeconds']],
            "workloadModel": config['workloadModel'], "experiment_kind": kind,
            "pending_settings": pending_settings(config, kind),
            "sample_sufficiency": "not-assessed" if config['validation']['minimumApiSamples'] is None else "explicit minimum; not a p99 precision guarantee",
            "prometheus_queries": query_plan(config['prometheus'])}


def execute_pilot(args):
    kind = getattr(args, 'kind', 'pilot')
    config_path, config = load_config(args.config, args.profile, args.users, allow_pending=args.plan, kind=kind)
    if getattr(args, 'validate_seat_conflict', False):
        if kind != 'journey-smoke' or config['workloadModel'] != 'reselection':
            raise ValueError('Controlled conflict validation requires Reselection journey-smoke')
        if config['auth']['tokenPoolSize'] is None or config['auth']['tokenPoolSize'] < 2:
            raise ValueError('Controlled conflict validation requires two prepared Members (subject and holder)')
        seats = read_json(K6_ROOT / 'config' / 'profiles.json')[config['profile']]['seatCount']
        config['validationScenario'] = {'type': 'controlled-seat-conflict', 'performanceMeasurement': False,
            'subjectJourneys': 1, 'holderRole': 'pre-reserve subject-selected seat before first subject pre-reserve',
            'expectedConflicts': 1, 'profileOverrides': {'hotSeatRatio': 1 / seats,
                'hotTrafficRatio': 1, 'seatSelectionRatio': [1, 0, 0]}}
        config['smoke'].update(vus=1, iterations=1)
        config['warmup'] = {'enabled': False, 'execution': None}
        config['reselection']['thinkTime']['enabled'] = False
        config['reselection']['abandonment']['enabled'] = False
    if getattr(args, 'workload_model', None) and args.workload_model != config['workloadModel']:
        raise ValueError('--workload-model differs from the reviewed config.workloadModel')
    run_number = positive_integer(args.run_number, 'run number')
    repeat = positive_integer(getattr(args, 'repeat', 1), 'repeat')
    if getattr(args, 'harness_diagnostic', False):
        if kind != 'baseline' or config.get('workloadAxis') != 'target-vus' or config['workloadModel'] != 'reselection' or repeat != 1:
            raise ValueError('Harness diagnostic requires one Target-VU Reselection run')
    if repeat != 1 and kind != 'baseline':
        raise ValueError('--repeat is Baseline-only; no automatic repeat of Smoke/Pilot')
    preview = plan(config, kind)
    if 'validationScenario' in config:
        preview['experiment_kind'] = 'reselection-validation'
        preview['validation_scenario'] = config['validationScenario']
    preview['repeat'] = repeat
    if getattr(args, 'harness_diagnostic', False):
        preview.update(measurement_role='harness-diagnostic', performance_measurement=False,
                       diagnostic_output='phase markers and per-VU metadata; config, guard and timeouts unchanged')
    if args.plan:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0
    for number in range(run_number, run_number + repeat):
        current = SimpleNamespace(**vars(args))
        current.kind, current.run_number = kind, number
        result = execute_one(current, config_path, config)
        if result:
            return result
    return 0


def execute_one(args, config_path, config):
    kind, run_number = args.kind, args.run_number
    base_url = http_url(os.environ.get('BASE_URL'), 'BASE_URL')
    if not shutil.which('k6'):
        raise ValueError('k6 executable not found')
    version = subprocess.run(['k6', 'version'], capture_output=True, text=True, check=True).stdout
    if not version.startswith('k6 v1.'):
        raise ValueError('Pilot uses the verified k6 1.x summary schema')
    builder, runner = module('prepare-fixture'), module('run-experiment')
    builder.fixture_conditions(read_json(K6_ROOT / 'config' / 'profiles.json')[config['profile']], read_json(config['fixtureTemplate']))
    if not args.keep_data or config['inventory']['mode'] == 'db':
        probe_database()
    fixtures, runs, cleanup_errors = [], [], []
    warmup_ref = None
    initial_env = {key: os.environ.get(key) for key in ('TEST_ACCESS_TOKEN', 'TEST_TOKENS_FILE', 'TEST_ACCOUNTS_FILE', 'PERFORMANCE_TIME_ID', 'WORKLOAD_PROFILE', 'K6_AUTH_OBSERVATION_FILE', 'K6_PREPARATION_PHASE')}
    attempt_dir = Path(os.environ['K6_RUN_ATTEMPT_DIR']) if os.environ.get('K6_RUN_ATTEMPT_DIR') else None
    sequence = []
    def record(phase, action, **details):
        sequence.append({'at': result_now(), 'phase': phase, 'action': action, **details})
        if attempt_dir:
            write_json(attempt_dir / 'preparation-sequence.json', sequence)
    phase = 'environment'
    try:
        with prometheus_tunnel():
            phases = ['journey-smoke'] if kind == 'journey-smoke' else (['warmup', kind] if config['warmup']['enabled'] else [kind])
            for phase in phases:
                record(phase, 'phase-start')
                os.environ.pop('TEST_ACCESS_TOKEN', None)
                os.environ.pop('PERFORMANCE_TIME_ID', None)
                os.environ['WORKLOAD_PROFILE'] = config['profile']
                if config['auth']['mode'] == 'automatic':
                    if attempt_dir:
                        os.environ['K6_AUTH_OBSERVATION_FILE'] = str(attempt_dir / f'auth-{phase}.json')
                        os.environ['K6_PREPARATION_PHASE'] = phase
                    account_count = (config['warmup'].get('tokenPoolSize', config['auth']['tokenPoolSize'])
                                     if phase == 'warmup' else config['auth']['tokenPoolSize'])
                    account_store = args.accounts
                    if config.get('workloadAxis') == 'target-vus' and phase == 'warmup':
                        namespace = hashlib.sha256(base_url.encode()).hexdigest()[:16]
                        account_store = Path(args.accounts).expanduser().resolve() if args.accounts else K6_ROOT / 'fixtures' / f'accounts-{namespace}.local.json'
                        account_store = account_store.with_name(account_store.stem + '-warmup' + account_store.suffix)
                    record(phase, 'JWT-preparation-start', target_authentication_count=account_count)
                    accounts, tokens = prepare_accounts(base_url, account_count, config['auth']['accountWorkers'], account_store)
                    record(phase, 'JWT-preparation-end', target_authentication_count=account_count)
                    os.environ.update(TEST_TOKENS_FILE=str(tokens), TEST_ACCOUNTS_FILE=str(accounts))
                elif not os.environ.get('TEST_TOKENS_FILE'):
                    raise ValueError('Preissued mode requires TEST_TOKENS_FILE')
                token_window(token_pool_from_env(), config, phase)
                try:
                    record(phase, 'fixture-preparation-start')
                    fixture = builder.prepare(config['profile'], config['fixtureTemplate'], 30)
                    fixtures.append(fixture)
                    record(phase, 'fixture-preparation-end', manifest=str(fixture))
                except (ValueError, OSError) as error:
                    partial = getattr(error, 'fixture_manifest_path', None)
                    if partial:
                        fixtures.append(partial)
                    raise
                if len(fixtures) > 1 and read_json(fixtures[-1])['performance_time_id'] == read_json(fixtures[-2])['performance_time_id']:
                    raise ValueError('Warm-up and measured Pilot must have different performanceTimeId values')
                arguments = SimpleNamespace(kind=kind, phase=phase, profile=config['profile'], fixture=str(fixture),
                                            run_number=run_number, pilot_settings=config, config_path=config_path, warmup_run=warmup_ref,
                                            harness_diagnostic=getattr(args, 'harness_diagnostic', False))
                claim = fixture.parent / 'measurement-claim.json'
                try:
                    record(phase, 'workload-runner-start')
                    result = runner.run(arguments)
                finally:
                    if claim.exists():
                        run_id = read_json(claim)['run_id']
                        run_path = K6_ROOT / 'results' / run_id
                        if run_path not in runs:
                            runs.append(run_path)
                        record(phase, 'run-artifact', directory=str(run_path))
                        if phase == 'warmup' and (run_path / 'run-metadata.json').exists():
                            warmup_metadata = read_json(run_path / 'run-metadata.json')
                            warmup_ref = {"run_id": run_id, "directory": str(run_path),
                                          "performance_time_id": read_json(fixture)['performance_time_id'],
                                          **{key: warmup_metadata.get(key) for key in (
                                              'workload_started_at', 'workload_ended_at', 'started_at', 'ended_at')}}
                if result:
                    record(phase, 'workload-runner-failed', exit_code=result)
                    return result
                record(phase, 'workload-runner-end', exit_code=result)
            return 0
    except Exception as error:
        if attempt_dir:
            write_json(attempt_dir / 'runner-failure.json', {'at': result_now(), 'phase': phase,
                'exception_class': type(error).__name__, 'exception_repr': repr(error), 'exception_str': str(error),
                'details': getattr(error, 'details', None), 'automatic_retry': False})
        # A thrown post-processing error does not mean k6 is still active.
        # Mark claimed run artifacts stopped before the cleanup guard checks them.
        for run in runs:
            metadata_path = run / 'run-metadata.json'
            if metadata_path.exists():
                metadata = read_json(metadata_path)
                if metadata.get('status') in {'preflight', 'running', 'postprocessing'}:
                    metadata.update(status='post-processing-failed', runner_error=type(error).__name__)
                    write_json(metadata_path, metadata)
        raise
    finally:
        if not args.keep_data:
            for fixture in reversed(fixtures):
                try:
                    clean_fixture(fixture, 'delete')
                except (ValueError, OSError, subprocess.SubprocessError) as error:
                    cleanup_errors.append({"manifest": str(fixture), "error_type": type(error).__name__, 'error': str(error)})
            for run in runs:
                write_json(run / 'pilot-cleanup.json', {"errors": cleanup_errors, "fixture_count": len(fixtures)})
        record(phase, 'cleanup-end', errors=cleanup_errors, fixture_count=len(fixtures))
        for key, value in initial_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if cleanup_errors:
            raise ValueError('Pilot data saved, but fixture cleanup failed; inspect pilot-cleanup.json and manifests')
