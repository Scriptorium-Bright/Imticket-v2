"""수용한 Target VU 설정으로 반복 측정한다. Artifact를 보존하고 측정 검증 문제 시 중단한다."""
import argparse
import re
import subprocess
import sys
from pathlib import Path

from baseline_environment import capture
from common import K6_ROOT, file_sha256, measured, read_json, result_now, result_stamp, write_json
from pilot_config import load_config

PROFILES = ('rapper-low', 'musical-low', 'idol-low', 'ballad-low', 'overseas-low',
            'musical-high', 'idol-high', 'rapper-high', 'ballad-high', 'overseas-high')
MODELS = ('single-attempt', 'reselection')
ARTIFACTS = ('run-metadata.json', 'k6-summary.json', 'k6-summary.txt', 'k6-points.json', 'report.md',
             'pilot-config.json', 'fixture-manifest.json', 'fixture-initial-seats.json',
             'fixture-final-seats.json', 'seat-inventory.json', 'seat-inventory-db-snapshot.json',
             'vu-baseline-observations.json', 'load-generator-observations.json', 'pilot-cleanup.json',
             'fixture-cleanup.json', 'prometheus/http.json', 'prometheus/tomcat.json',
             'prometheus/hikari.json', 'prometheus/jvm.json', 'prometheus/collection-status.json', 'k6.log')
FATAL_CLIENT = re.compile(r'panic:|fatal error:|out of memory|cannot allocate memory|too many open files|'
                          r'failed to initialize.*VU|cannot initialize.*VU', re.IGNORECASE)


def inspect_run(directory, expected_config=None):
    directory = Path(directory)
    missing = [name for name in ARTIFACTS if not (directory / name).is_file()]
    if missing:
        return {'directory': str(directory), 'valid': False, 'issues': ['missing-artifacts'], 'missing': missing}
    m = read_json(directory / 'run-metadata.json')
    o = read_json(directory / 'vu-baseline-observations.json')
    config = read_json(directory / 'pilot-config.json')
    inv = read_json(directory / 'seat-inventory.json')
    db = inv.get('db') or {}
    generator = read_json(directory / 'load-generator-observations.json')
    cleanup = read_json(directory / 'pilot-cleanup.json')
    prom = read_json(directory / 'prometheus/collection-status.json')
    initial = read_json(directory / 'fixture-initial-seats.json')
    target = m.get('configured_target_vus')
    c = o.get('counts', {})
    warmup = m.get('warmup_run') or {}
    auth = m.get('auth') or {}
    fatal_client_lines = []
    with (directory / 'k6.log').open(encoding='utf-8', errors='replace') as source:
        for number, line in enumerate(source, 1):
            if FATAL_CLIENT.search(line):
                fatal_client_lines.append(number)
    issues = []
    def require(name, condition):
        if not condition:
            issues.append(name)
    require('target-vu-baseline', m.get('phase') == 'baseline' and m.get('workloadAxis') == 'target-vus')
    require('performance-measurement', m.get('performance_measurement') is not False and m.get('measurement_role') != 'harness-diagnostic')
    require('runner-completed', m.get('status') == 'completed' and m.get('k6_exit_code') == 0)
    require('measurement-validation', o.get('validation', {}).get('status') == 'passed')
    require('one-journey-per-target-vu', target and all(c.get(k) == target for k in
            ('iteration_started', 'journey_started', 'journey_finished', 'iterations')))
    require('no-unfinished-or-dropped', m.get('unfinished_iterations') == 0 and c.get('dropped_iterations') == 0)
    require('no-technical-guard-stop', c.get('booking_journey_guard_stopped', 0) == 0)
    require('unique-member-per-vu', auth.get('token_count') == target and
            auth.get('declared_distinct_members') == target and auth.get('selection') == 'vu-id-to-unique-member')
    require('fresh-initial-inventory', len(initial) == m.get('seat_count') and
            all(seat.get('status') == 'AVAILABLE' for seat in initial) and
            m.get('fixture', {}).get('claim', {}).get('verified_seat_count') == m.get('seat_count'))
    require('separate-warmup-fixture', warmup.get('performance_time_id') and
            warmup['performance_time_id'] != m.get('performance_time_id'))
    require('prometheus-collected', m.get('prometheus_collection_status') == prom.get('status') == 'collected'
            and not prom.get('errors'))
    require('inventory-and-oversell', inv.get('status') == 'verified' and db.get('oversell_invariant') == 'passed'
            and db.get('duplicate_oversold_seat_count') == 0 and db.get('duplicate_reservation_mapping_count') == 0)
    require('cleanup', cleanup.get('fixture_count') == 2 and not cleanup.get('errors'))
    require('load-generator-observed', generator.get('status') == 'collected' and
            generator.get('k6_process_cpu_percent_max') is not None and generator.get('k6_process_rss_bytes_max') is not None)
    require('no-fatal-client-runtime-error', not fatal_client_lines)
    if expected_config is not None:
        require('effective-config-unchanged', config == expected_config)
    return {'directory': str(directory), 'run_id': m['run_id'], 'profile': m['profile'],
            'workloadModel': m['workloadModel'], 'run_number': m['run_number'], 'target_vus': target,
            'valid': not issues, 'issues': issues, 'fatal_client_log_line_numbers': fatal_client_lines,
            'counts': c, 'traffic': o.get('traffic'), 'timing': o.get('timing'),
            'latency_ms': o.get('latency_ms'), 'api_latency_ms': o.get('api_latency_ms'),
            'retry_distributions': o.get('retry_distributions'),
            'business_failure_reasons': o.get('business_failure_reasons'),
            'system_failure_reasons': o.get('system_failure_reasons'),
            'seat_inventory_timing': o.get('seat_inventory_timing'), 'db_inventory': db,
            'client_minus_db_confirmed_seats': c.get('seats_confirmed', 0) - db.get('unique_sold_seat_count', 0),
            'prometheus_status': prom.get('status'), 'load_generator': {k: generator.get(k) for k in
            ('status', 'sampling_interval_seconds', 'k6_process_cpu_percent_max', 'k6_process_rss_bytes_max', 'host_memory_summary')},
            'effective_execution': m.get('effective_execution'), 'request_timeout': m.get('http_timeout')}


def write_report(directory, state):
    lines = ['# Target VU Baseline 반복 측정', '',
             '테스트용 synthetic workload다. 1 VU는 Journey 한 번만 수행한다. 시각은 KST, latency 단위는 ms다.', '',
             '각 행은 별도 Run이다. HTTP RPS와 Journey throughput은 관측 결과다. '
             'Client가 완료로 관측한 좌석 수와 최종 DB 확정 좌석 수는 따로 표시한다.', '',
             '실행 상한: idol-high Single Attempt는 20m, 나머지는 10m. '
             'Reselection guard: overseas-high는 10m, 다른 profile은 60s. '
             'Run 2·3은 수용한 Run 1의 effective config를 그대로 사용한다. Guard 종료가 있는 Run은 제외한다.', '']
    for model in MODELS:
        lines += ['## ' + model, '',
            '| Profile | Run | Target VUs | 시작 | 종료 | HTTP RPS | Journey/s | Journey p50 | p95 | p99 | 성공 Booking p95 | Booking | System 실패 | 좌석 충돌 | 재선택 | Client 완료 좌석 | DB 확정 좌석 | 판매 비율 | 전석 판매 시간(s) | Oversell 검증 | Generator 수집 |',
            '|' + '|'.join(['---'] * 21) + '|']
        runs = [r for r in state['accepted_runs'] if r.get('workloadModel') == model]
        runs.sort(key=lambda r: (PROFILES.index(r['profile']), r['run_number']))
        for r in runs:
            c, t, db = r['counts'], r['traffic'], r['db_inventory']
            j = r['latency_ms']['journey_duration']
            b = r['latency_ms']['successful_booking_duration']
            row = [r['profile'], r['run_number'], r['target_vus'], c['journey_started'], c['journey_finished'],
                   measured(t['http_rps']), measured(t['completed_journey_throughput_per_second']),
                   *[measured(j[k]) for k in ('p50', 'p95', 'p99')], measured(b['p95']),
                   c['booking_completed'], c['system_failures'], c['seat_conflicts'], c['reselections'],
                   c['seats_confirmed'], db['unique_sold_seat_count'], measured(db['seat_sell_through_rate']),
                   measured(r['seat_inventory_timing']['time_from_workload_start_to_client_observed_full_sell_out_seconds']),
                   db['oversell_invariant'], r['load_generator']['status']]
            lines.append('| ' + ' | '.join(str(v) for v in row) + ' |')
    lines += ['', '## 측정 상태', '', '전체 상태: ' + state['status'], '',
              '수용한 측정 Run: ' + str(len(state['accepted_runs'])) + ' / 60.', '',
              '개별 report에는 API sample 수, HTTP와 종료 유형별 latency, retry 분포, '
              '시작 분포, Mac CPU/RSS snapshot, Tomcat/Hikari/JVM 시계열을 보존한다.', '']
    for r in state['accepted_runs']:
        lines.append(f"- [{r['run_id']}](../{r['run_id']}/report.md)")
    lines += ['', '## 제외한 시도', '']
    for r in state.get('excluded_attempts', []):
        lines.append('- ' + Path(r['directory']).name + ': ' + ', '.join(r.get('issues', [])))
    for error in state['errors']:
        lines.append('- ' + error)
    lines += ['', '원인이나 병목을 판정하지 않는다. 서버 timeout/system failure만으로 측정을 무효화하지 않는다.', '']
    (directory / 'report.md').write_text('\n'.join(lines), encoding='utf-8')


def save(directory, state):
    write_json(directory / 'series-results.json', state)
    write_report(directory, state)


def prepare():
    candidates, excluded = {}, []
    for directory in sorted((K6_ROOT / 'results').iterdir()):
        path = directory / 'run-metadata.json'
        if not path.is_file():
            continue
        m = read_json(path)
        if m.get('workloadAxis') != 'target-vus' or m.get('phase') != 'baseline' or m.get('run_number') != 1:
            continue
        result = inspect_run(directory)
        if not result['valid']:
            excluded.append({'directory': str(directory), 'issues': result['issues']})
            continue
        candidates[(m['profile'], m['workloadModel'])] = result
    missing = [(p, m) for p in PROFILES for m in MODELS if (p, m) not in candidates]
    if missing:
        raise ValueError('아직 유효한 Run 1이 없는 조합: ' + str(missing))
    directory = K6_ROOT / 'results' / (result_stamp() + '_target-vu-baseline-series')
    (directory / 'configs').mkdir(parents=True)
    entries, accepted = [], []
    for profile in PROFILES:
        for model in MODELS:
            r = candidates[(profile, model)]
            accepted.append(r)
            config = read_json(Path(r['directory']) / 'pilot-config.json')
            path = directory / 'configs' / (profile + '.' + model + '.json')
            write_json(path, config)
            _, effective = load_config(path, kind='baseline')
            if effective != config:
                raise ValueError('복사한 설정이 수용한 effective config를 재현하지 못함')
            entries.append({'profile': profile, 'workloadModel': model, 'source_run': r['directory'],
                            'config': str(path), 'sha256': file_sha256(path)})
    frozen = {}
    for folder in ('lib', 'scripts'):
        for path in sorted((K6_ROOT / folder).glob('*.py' if folder == 'scripts' else '*.js')):
            frozen[str(path)] = file_sha256(path)
    for path in [*sorted(K6_ROOT.glob('*.js')), K6_ROOT / 'config/profiles.json',
                 K6_ROOT / 'config/fixture-template.json', K6_ROOT / 'docs/metric-inventory.json']:
        frozen[str(path)] = file_sha256(path)
    plan = {'created_at': result_now(), 'workload_axis': 'target-vus', 'entries': entries,
            'repeat_run_numbers': [2, 3], 'frozen_source_sha256': frozen,
            'additional_measured_runs': 40, 'additional_warmup_runs': 40,
            'scope': 'Exact accepted per-profile/model Run 1 configs; no automatic changes or retry after a defect.'}
    write_json(directory / 'series-plan.json', plan)
    state = {'status': 'prepared', 'prepared_at': result_now(), 'accepted_runs': accepted,
             'excluded_attempts': excluded, 'errors': [], 'active_run': None}
    save(directory, state)
    print('반복 측정 directory: ' + str(directory), flush=True)
    return directory


def execute(directory):
    directory = directory.resolve()
    plan = read_json(directory / 'series-plan.json')
    state = read_json(directory / 'series-results.json')
    if state['status'] not in {'prepared', 'running'} or state['active_run'] or state['errors']:
        raise ValueError('후속 실행 전에 저장된 측정 상태를 확인해야 함')
    identity_path = directory / 'server-environment-initial.json'
    identity = read_json(identity_path) if identity_path.exists() else capture()
    if not identity_path.exists():
        write_json(identity_path, identity)
    state.update(status='running', started_at=state.get('started_at') or result_now())
    save(directory, state)
    for run_number in plan['repeat_run_numbers']:
        for entry in plan['entries']:
            if any(r['profile'] == entry['profile'] and r['workloadModel'] == entry['workloadModel'] and
                   r['run_number'] == run_number for r in state['accepted_runs']):
                continue
            paths = {**plan['frozen_source_sha256'], **{r['config']: r['sha256'] for r in plan['entries']}}
            if any(file_sha256(path) != sha for path, sha in paths.items()):
                state['errors'].append('고정 source/config가 바뀌어 후속 Run을 시작하지 않음')
                break
            before = capture()
            if before['comparison_sha256'] != identity['comparison_sha256']:
                state['errors'].append('서버 identity/설정이 바뀌어 후속 Run을 시작하지 않음')
                break
            state['active_run'] = {**entry, 'run_number': run_number, 'started_at': result_now()}
            save(directory, state)
            existing = set((K6_ROOT / 'results').iterdir())
            label = f"{entry['profile']} / {entry['workloadModel']} / Run {run_number}"
            print('실행 시작: ' + label, flush=True)
            command = [sys.executable, str(K6_ROOT / 'scripts/run-experiment.py'), 'baseline',
                       '--config', entry['config'], '--run-number', str(run_number)]
            log_path = directory / f"{entry['profile']}_{entry['workloadModel']}_run-{run_number:02d}.log"
            with log_path.open('w', encoding='utf-8') as log:
                result = subprocess.run(command, cwd=K6_ROOT.parent, stdout=log, stderr=subprocess.STDOUT, check=False)
            new_dirs = set((K6_ROOT / 'results').iterdir()) - existing
            measured_dirs = [p for p in new_dirs if (p / 'run-metadata.json').exists() and
                             read_json(p / 'run-metadata.json').get('phase') == 'baseline']
            if len(measured_dirs) != 1:
                state['errors'].append(label + ': measured artifact 한 개를 확인하지 못함; runner exit ' + str(result.returncode))
                break
            run_dir = measured_dirs[0]
            after = capture()
            unchanged = before['comparison_sha256'] == after['comparison_sha256'] == identity['comparison_sha256']
            write_json(run_dir / 'server-environment.json', {'before': before, 'after': after, 'unchanged': unchanged})
            r = inspect_run(run_dir, read_json(entry['config']))
            r['server_environment_unchanged'] = unchanged
            if result.returncode or not r['valid'] or not unchanged:
                state['excluded_attempts'].append(r)
                state['errors'].append(label + ': 측정 검증에 문제가 있어 후속 Run을 시작하지 않음')
                break
            state['accepted_runs'].append(r)
            state['active_run'] = None
            save(directory, state)
            print('유효 Run 확보: ' + label + ' -> ' + r['run_id'], flush=True)
        if state['errors']:
            break
    state.update(status='completed' if len(state['accepted_runs']) == 60 and not state['errors'] else 'needs-review',
                 ended_at=result_now())
    save(directory, state)
    print('반복 측정 보고서: ' + str(directory / 'report.md'), flush=True)
    return 0 if state['status'] == 'completed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare', action='store_true')
    group.add_argument('--execute', type=Path, metavar='SERIES_DIRECTORY')
    args = parser.parse_args()
    if args.prepare:
        prepare()
        return 0
    return execute(args.execute)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Target VU 반복 측정을 이어갈 수 없음: ' + type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(2)
