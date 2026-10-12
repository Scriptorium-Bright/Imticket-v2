"""Offline Reselection progress accounting. Does not contact the application."""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from common import APIS, file_sha256, read_json, result_now, result_stamp, write_json
from vu_baseline_observations import epoch
from pilot_observations import distribution

TERMINALS = {
    'booking_journey_completed': 'success', 'booking_journey_sold_out': 'sold-out',
    'booking_journey_system_failed': 'system-failure', 'booking_journey_guard_stopped': 'safety-guard',
    'booking_journey_abandoned': 'user-abandoned', 'booking_journey_business_terminated': 'business-terminated',
}


def analyze_points(path):
    counters, by_api, by_outcome, samples = Counter(), defaultdict(Counter), defaultdict(Counter), Counter()
    users, vu_samples, parse_errors = {}, [], []
    phase_samples, previous_markers, longest, http_dispatches, http_wall, http_native = defaultdict(list), {}, [], {}, {}, {}
    with Path(path).open(encoding='utf-8') as source:
        for line_number, line in enumerate(source, 1):
            try:
                point = json.loads(line)
                if point.get('type') != 'Point':
                    continue
                name, data = point['metric'], point['data']
                value, tags = data['value'], data.get('tags', {})
                at = epoch(data['time'])
                if name == 'vus':
                    vu_samples.append((at, value))
                samples[name] += 1
                if value:
                    counters[name] += value
                    by_api[name][tags.get('api', 'none')] += value
                # Trends count samples, never sum their duration/retry values as user counts.
                by_outcome[name][tags.get('outcome', 'none')] += (value if name == 'journey_finished' else 1)
                meta = data.get('metadata', {})
                if 'harness_vu' not in meta:
                    continue
                vu = meta['harness_vu']
                row = users.setdefault(vu, {'vu': int(vu), 'counts': Counter(), 'phase': None,
                                          'decision': None, 'iterations_seen': set()})
                row['iterations_seen'].add(meta.get('harness_iteration'))
                if name in {'iteration_started', 'journey_started', 'journey_finished', 'iterations', *TERMINALS}:
                    row['counts'][name] += value
                sequence = int(meta.get('harness_sequence', 0))
                request_key = (vu, meta.get('harness_request'))
                if name == 'http_req_duration':
                    http_native[request_key] = value
                if name == 'harness_phase':
                    started_ms = float(meta['harness_state_started_ms'])
                    previous = previous_markers.get(vu)
                    if previous and sequence > previous['sequence']:
                        elapsed = max(0, started_ms - previous['started_ms'])
                        phase_samples[previous['state']].append(elapsed)
                        span = {**previous, 'elapsed_ms': elapsed, 'next_state': meta['harness_state']}
                        longest.append(span)
                        longest.sort(key=lambda x: x['elapsed_ms'], reverse=True)
                        del longest[20:]
                    if previous is None or sequence > previous['sequence']:
                        previous_markers[vu] = {'vu': int(vu), 'sequence': sequence, 'state': meta['harness_state'],
                                               'started_ms': started_ms, 'api': meta.get('harness_api')}
                    if meta['harness_state'] == 'http.dispatch':
                        http_dispatches[request_key] = started_ms
                    elif meta['harness_state'] == 'http.returned' and request_key in http_dispatches:
                        http_wall[request_key] = max(0, started_ms - http_dispatches[request_key])
                if row['phase'] is None or sequence > row['phase']['sequence']:
                    row['phase'] = {'sequence': sequence, 'last_point_at': data['time'],
                                    **{k.removeprefix('harness_'): v for k, v in meta.items() if k.startswith('harness_')}}
                    row['phase']['sequence'] = sequence
                if meta.get('harness_state') == 'engine.result' or meta.get('harness_state', '').startswith('finalize.'):
                    row['decision'] = {'outcome': meta.get('harness_outcome'), 'reason': meta.get('harness_reason')}
            except (ValueError, KeyError, TypeError) as error:
                parse_errors.append({'line': line_number, 'error': type(error).__name__})
    for row in users.values():
        row['counts'] = dict(row['counts'])
        row['iterations_seen'] = sorted(v for v in row['iterations_seen'] if v is not None)
        row['last_request_native_duration_ms'] = http_native.get((str(row['vu']), row['phase'].get('request')))
    vu_samples.sort()
    gaps = sorted(({'gap_seconds': b[0] - a[0], 'from_epoch': a[0], 'to_epoch': b[0],
                    'vus_before': a[1], 'vus_after': b[1]} for a, b in zip(vu_samples, vu_samples[1:])),
                  key=lambda x: x['gap_seconds'], reverse=True)[:5]
    terminal_count = sum(counters[name] for name in TERMINALS)
    finishes = counters['journey_finished']
    unfinished = [row for row in users.values() if row['counts'].get('journey_started', 0) and not row['counts'].get('journey_finished', 0)]
    unfinished_iterations = [row for row in users.values() if row['counts'].get('iteration_started', 0) and not row['counts'].get('iterations', 0)]
    phase_counts = lambda rows: dict(Counter(row['phase']['state'] for row in rows))
    return {
        'raw_parse_errors': parse_errors, 'raw_points_have_vu_identity': bool(users),
        'counts': {name: counters[name] for name in ('iteration_started', 'journey_started', 'journey_finished', 'iterations',
                    'seat_conflicts', 'reselections', 'reselection_stale_refreshes', 'http_attempted', 'http_reqs', 'http_completed')},
        'terminal_counters': {label: counters[name] for name, label in TERMINALS.items()},
        'terminal_recording_chain': {'terminal_counter_samples': terminal_count, 'retry_samples': samples['retries_per_journey'],
            'journey_finished': finishes, 'journey_duration_samples': samples['journey_duration'],
            'native_iterations': counters['iterations'],
            'terminal_counter_minus_journey_finished': terminal_count - finishes,
            'journey_finished_minus_duration_samples': finishes - samples['journey_duration']},
        'terminal_recording_by_outcome': {label: {'counter': counters[name],
            'retry_samples': by_outcome['retries_per_journey'][label],
            'journey_finished': by_outcome['journey_finished'][label],
            'journey_duration_samples': by_outcome['journey_duration'][label]} for name, label in TERMINALS.items()},
        'guard_last_api_label': dict(by_api['booking_journey_guard_stopped']),
        'api_flow_counts': {api: {name: by_api[name][api] for name in ('http_attempted', 'http_reqs', 'http_completed', 'http_contract_success')}
                            for api in APIS},
        'vu_gauge_largest_gaps': gaps,
        'diagnostic_phase_elapsed_ms': {state: distribution(values) for state, values in phase_samples.items()},
        'longest_observed_phase_intervals': longest,
        'request_wall_vs_native_ms': {'wall_sample_count': len(http_wall), 'matched_count': sum(k in http_native for k in http_wall),
            'wall_minus_native': distribution([value - http_native[key] for key, value in http_wall.items() if key in http_native]),
            'unfinished_http_dispatch_with_native_duration': sum(row['phase']['state'] == 'http.dispatch'
                and row['last_request_native_duration_ms'] is not None for row in unfinished)},
        'per_vu': {'observed_vu_ids': len(users), 'unfinished_journey_count': len(unfinished),
            'unfinished_iteration_count': len(unfinished_iterations),
            'unfinished_journey_last_phase': phase_counts(unfinished),
            'unfinished_iteration_last_phase': phase_counts(unfinished_iterations),
            'unfinished_with_terminal_decision': sum(row['decision'] is not None for row in unfinished),
            'journey_started_count_not_one': [row['vu'] for row in users.values() if row['counts'].get('journey_started', 0) != 1],
            'multiple_iteration_ids': [row['vu'] for row in users.values() if len(row['iterations_seen']) > 1]},
        'users': sorted(users.values(), key=lambda row: row['vu']),
    }


def save_diagnostic(run_dir, output_dir=None):
    run_dir = Path(run_dir).resolve()
    output = Path(output_dir).resolve() if output_dir else run_dir
    output.mkdir(parents=True, exist_ok=True)
    metadata = read_json(run_dir / 'run-metadata.json')
    result = analyze_points(run_dir / 'k6-points.json')
    users = result.pop('users')
    result.update(schema_version=1, reviewed_at=result_now(), source_run=str(run_dir),
                  measurement_role='harness-review', performance_measurement=False,
                  target_vus=metadata.get('configured_target_vus'),
                  source_sha256={name: file_sha256(run_dir / name) for name in ('k6-points.json', 'run-metadata.json', 'k6-summary.json')},
                  execution=metadata.get('effective_execution'), request_timeout=metadata.get('http_timeout'),
                  reselection_policy=metadata.get('reselection'),
                  limitations=['마지막 기록 위치는 스레드 stack이나 실제 대기 원인의 확정이 아니다.',
                      'phase marker와 metadata가 추가된 실행은 기존 Baseline과 성능 수치로 비교하지 않는다.',
                      'VU 식별자가 없는 과거 raw points로 미완료 사용자 각각의 마지막 위치를 복원할 수 없다.'])
    write_json(output / 'harness-observations.json', result)
    if users:
        with (output / 'harness-users.jsonl').open('w', encoding='utf-8') as source:
            for row in users:
                source.write(json.dumps(row, ensure_ascii=False) + '\n')
    diagnostic_report(output)
    return result


def diagnostic_report(directory):
    directory = Path(directory)
    path = directory / 'harness-observations.json'
    if not path.exists():
        (directory / 'report.md').write_text('# Reselection 실행 경로 진단\n\n진단 후처리가 완료되지 않았습니다. Baseline에서는 제외합니다.\n', encoding='utf-8')
        return
    data = read_json(path)
    chain = data['terminal_recording_chain']
    lines = ['# Reselection 실행 경로 검토', '',
        f"대상 Run: `{Path(data['source_run']).name}`", '',
        'Baseline 상태는 19/20 유효 Run으로 유지합니다. 이 검토/진단은 성능 측정에 포함하지 않습니다.', '',
        '## 실행 조건', '',
        f"- Target VUs: {data['target_vus']}", f"- executor: `{data['execution']}`",
        f"- HTTP timeout: `{data['request_timeout']}`", f"- safety guard: `{(data['reselection_policy'] or {}).get('safetyGuard')}`", '',
        '## 종료 기록', '', '| 구간 | 기록 수 |', '|---|---:|',
        *[f'| {key} | {value} |' for key, value in chain.items()], '',
        'terminal counter → duration → retry → outcome metric/log → journey_finished → journey_duration → check → 함수 return → native iterations 순서입니다.', '',
        '종료 유형 결정과 이후 기록은 여러 독립 작업입니다. counter 차이는 종료 유형을 결정하지 못한 사용자 수와 동일하지 않습니다.', '',
        '| 종료 유형 | terminal counter | retry sample | journey_finished | duration sample |', '|---|---:|---:|---:|---:|',
        *[f"| {label} | {row['counter']} | {row['retry_samples']} | {row['journey_finished']} | {row['journey_duration_samples']} |"
          for label, row in data['terminal_recording_by_outcome'].items()], '',
        '## 반복 경로', '',
        f"- seat_conflicts: {data['counts']['seat_conflicts']}", f"- reselections: {data['counts']['reselections']}",
        f"- stale refresh: {data['counts']['reselection_stale_refreshes']}", '',
        '반복은 SEAT_UNAVAILABLE 후 재조회, 또는 직전 충돌 seat set만 남았을 때 stale refresh 경로에 있습니다. timeout/network/5xx는 재시도하지 않고 시스템 오류로 반환합니다.', '',
        '## guard의 API 표시', '', '| 기록된 API 이름 | guard 수 |', '|---|---:|',
        *[f'| {api} | {value} |' for api, value in data['guard_last_api_label'].items()], '',
        '이 API 이름은 HTTP 대기 위치를 뜻하지 않습니다. loop-entry에서는 이전 API 이름이 남고, before-api에서는 아직 호출하지 않은 다음 API 이름이 기록됩니다.', '',
        '## API 흐름', '', '| API | attempted | native requests | response recorded | contract success |', '|---|---:|---:|---:|---:|',
        *[f"| {api} | {row['http_attempted']} | {row['http_reqs']} | {row['http_completed']} | {row['http_contract_success']} |"
          for api, row in data['api_flow_counts'].items()], '',
        '## 사용자별 마지막 기록 위치', '']
    if not data['raw_points_have_vu_identity']:
        lines += ['과거 raw points에 VU/Journey ID가 없습니다. 미완료 사용자 각각의 마지막 위치는 확인할 수 없습니다.']
    else:
        vu = data['per_vu']
        lines += [f"- 식별된 VU: {vu['observed_vu_ids']}", f"- 미완료 Journey: {vu['unfinished_journey_count']}",
                  f"- 미완료 native iteration: {vu['unfinished_iteration_count']}",
                  f"- 미완료 Journey 중 terminal decision 기록 있음: {vu['unfinished_with_terminal_decision']}", '',
                  '| 미완료 Journey의 마지막 phase | 수 |', '|---|---:|',
                  *[f'| {state} | {count} |' for state, count in vu['unfinished_journey_last_phase'].items()], '',
                  '`harness-users.jsonl`에 VU별 마지막 phase, API, checkpoint, retry/attempt 수, terminal decision, completion counter를 저장했습니다.']
        lines += ['', '## 기록된 phase 구간 시간', '',
                  '두 phase 진입 기록 사이의 시간입니다. marker 기록 대기·스케줄링·해당 동작 시간이 함께 포함되므로 API 서버 대기 시간이나 함수 CPU 시간으로 해석하지 않습니다.', '',
                  '| phase | sample | p50 ms | p95 ms | max ms |', '|---|---:|---:|---:|---:|',
                  *[f"| {state} | {values['count']} | {values['p50']:.3f} | {values['p95']:.3f} | {values['max']:.3f} |"
                    for state, values in sorted(data['diagnostic_phase_elapsed_ms'].items(), key=lambda pair: pair[1]['max'], reverse=True)], '',
                  f"HTTP dispatch 위치에서 미완료로 기록됐으나 같은 request의 native HTTP duration은 남아 있는 사용자: {data['request_wall_vs_native_ms']['unfinished_http_dispatch_with_native_duration']}명.", '',
                  'native HTTP duration의 기록 여부와 JavaScript API 반환 위치를 구분합니다. 둘 사이의 차이를 서버 응답 대기로 단정하지 않습니다.']
    lines += ['', '## 확인 한계', '', *[f'- {text}' for text in data['limitations']], '',
              '서버 원인이나 병목을 판정하지 않았습니다. guard·실행 상한·사용자 행동·서버 설정을 변경하지 않았습니다. Run 2·3은 시작하지 않았습니다.', '',
              '## 원본 추적', '',
              *[f'- `{name}` SHA-256: `{digest}`' for name, digest in data['source_sha256'].items()], '']
    (directory / 'report.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or args.run_dir.parent / (result_stamp() + '_reselection_harness-review')
    result = save_diagnostic(args.run_dir, output)
    print(output / 'report.md')
