"""Saved-artifact comparisons; numeric outcomes are observations, never acceptance rules."""
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path

from common import measured, read_json, write_json


def server_max(directory, group, metric):
    path = Path(directory) / 'prometheus' / (group + '.json')
    if not path.exists():
        return None
    document = read_json(path)
    start, end = document['measurement_start_epoch'], document['measurement_end_epoch']
    entry = next((q for q in document['queries'] if q['id'] == metric), {})
    by_time = defaultdict(float)
    for series in entry.get('response', {}).get('data', {}).get('result', []):
        for timestamp, text in series.get('values', []):
            value = float(text)
            if start <= float(timestamp) <= end and math.isfinite(value):
                by_time[float(timestamp)] += value
    return max(by_time.values(), default=None)


def run_values(directory):
    directory = Path(directory)
    o = read_json(directory / 'vu-baseline-observations.json')
    m = read_json(directory / 'run-metadata.json')
    g = read_json(directory / 'load-generator-observations.json')
    db = read_json(directory / 'seat-inventory.json')['db']
    values = {'Target VUs': m['configured_target_vus'],
              'Journey start spread (s)': o['timing']['journey_start_spread_seconds'],
              'First request spread (s)': o.get('first_request_dispatch', {}).get('spread_seconds'),
              'HTTP RPS': o['traffic']['http_rps'],
              'Journey throughput (/s)': o['traffic']['completed_journey_throughput_per_second'],
              'Max active VUs (sampled)': o['traffic']['max_active_vus'],
              'Max VU capacity': o['traffic']['max_vus_observed'],
              'Unfinished': m['unfinished_iterations'],
              'k6 CPU max (%)': g['k6_process_cpu_percent_max'],
              'k6 RSS max (bytes)': g['k6_process_rss_bytes_max'],
              'Free memory min (bytes)': g['host_memory_summary']['minimum_free_bytes'],
              'Swap max (bytes)': g['host_memory_summary']['maximum_swap_used_bytes'],
              'Memory free percent min (OS)': g['host_memory_summary'].get('minimum_system_wide_memory_free_percent'),
              'Tomcat busy max': server_max(directory, 'tomcat', 'tomcat_threads_busy_threads'),
              'Tomcat current max': server_max(directory, 'tomcat', 'tomcat_threads_current_threads'),
              'Tomcat configured max': server_max(directory, 'tomcat', 'tomcat_threads_config_max_threads'),
              'Hikari active max': server_max(directory, 'hikari', 'hikaricp_connections_active'),
              'Hikari idle max': server_max(directory, 'hikari', 'hikaricp_connections_idle'),
              'Hikari pending max': server_max(directory, 'hikari', 'hikaricp_connections_pending'),
              'Hikari max': server_max(directory, 'hikari', 'hikaricp_connections_max'),
              'Hikari acquisition max (s)': server_max(directory, 'hikari', 'hikaricp_connections_acquire_seconds_max'),
              'JVM process CPU max': server_max(directory, 'jvm', 'process_cpu_usage'),
              'JVM heap used max (bytes)': server_max(directory, 'jvm', 'jvm_memory_used_bytes'),
              'JVM allocation max (bytes/s)': server_max(directory, 'jvm', 'allocation_rate'),
              'JVM GC p95 max (s)': server_max(directory, 'jvm', 'gc_pause_p95'),
              'JVM live threads max': server_max(directory, 'jvm', 'jvm_threads_live_threads'),
              'Client confirmed seats': o['counts']['seats_confirmed'],
              'DB confirmed seats': db['unique_sold_seat_count'],
              'AVAILABLE': db['available_seat_count'], 'LOCKED': db['locked_seat_count'], 'RESERVED': db['reserved_seat_count'],
              'Sell-through': db['seat_sell_through_rate'],
              'Duplicate confirmed Seat IDs': db['duplicate_oversold_seat_count'],
              'Duplicate reservation mapping': db['duplicate_reservation_mapping_count'],
              'Full sell-out': o['seat_inventory_timing']['full_sell_out_verified_by_db'],
              'Full sell-out time (s)': o['seat_inventory_timing']['time_from_workload_start_to_client_observed_full_sell_out_seconds']}
    for name in ('iteration_started', 'journey_started', 'journey_finished', 'iterations', 'dropped_iterations',
                 'booking_completed', 'reservation_success', 'business_failures', 'system_failures', 'seat_conflicts',
                 'reselections', 'seat_selection_fallbacks', 'booking_journey_sold_out', 'booking_journey_guard_stopped'):
        values[name] = o['counts'].get(name, 0)
    for name, distribution in o['latency_ms'].items():
        for quantile in ('count', 'p50', 'p95', 'p99', 'max'):
            values[name + ' ' + quantile] = distribution.get(quantile)
    for name, distribution in o.get('native_http_timings_ms', {}).items():
        for quantile in ('p50', 'p95', 'p99', 'max'):
            values[name + ' ' + quantile] = distribution.get(quantile)
    for api, distribution in o['api_latency_ms'].items():
        for quantile in ('count', 'p50', 'p95', 'p99', 'max'):
            values[api + ' ' + quantile] = distribution.get(quantile)
    for name, distribution in o.get('retry_distributions', {}).items():
        for quantile in ('count', 'avg', 'p50', 'p95', 'p99', 'max'):
            values[name + ' ' + quantile] = distribution.get(quantile)
    for reason in ('status_0', 'timeout', 'network_error', 'connection_reset', 'connection_refused', 'connect_timeout'):
        values['Transport ' + reason] = o.get('transport', {}).get('counts', {}).get(reason, 0)
    return values


def anomaly_evidence(results, policy):
    """Only concrete burst-input changes fail; outcomes/timing/RPS never enter this decision."""
    by_combo = defaultdict(list)
    for result in results:
        by_combo[(result['profile'], result['workloadModel'])].append(result)
    evidence = []
    for (profile, model), entries in by_combo.items():
        if len(entries) != 3 or not all(e['valid'] for e in entries):
            continue
        values = [run_values(e['directory']) for e in entries]
        for field in ('Journey start spread (s)', 'First request spread (s)'):
            spans = [v[field] for v in values]
            if all(v is not None for v in spans) and sum(v < policy['fastSpreadSeconds'] for v in spans) >= 2 and max(spans) >= policy['outlierSpreadSeconds']:
                evidence.append({'classification': 'load-generation-anomaly', 'profile': profile, 'workloadModel': model,
                                 'field': field, 'raw_values_seconds': spans,
                                 'evidence': 'Two repeated bursts dispatch in <1s while another dispatches in >=6s.',
                                 'scope': 'Burst input differs; server-side cause is not inferred.'})
    return evidence


def table(lines, headers, rows):
    lines.extend(['| ' + ' | '.join(headers) + ' |', '|' + '|'.join(['---'] * len(headers)) + '|'])
    for row in rows:
        lines.append('| ' + ' | '.join(str(v).replace('|', '\\|') for v in row) + ' |')


def write_campaign_report(root, state):
    root = Path(root)
    lines = ['# Target VU 재현성 검증 및 새 Final Baseline', '',
             '기존 결과는 Exploratory Baseline, 8,000/10,000 VU는 Stress Observation으로 별도 보존합니다.', '',
             '1 VU = 1 User = 1 Journey = 1 Iteration. 서버의 business/system 결과 편차는 합격 기준에 포함하지 않습니다.', '',
             '상태: ' + state['status'], '', 'Harness SHA: ' + state.get('harness_sha', 'N/A'), '']
    for category, expected in [('Preflight', 15), ('New Final Baseline', 60)]:
        entries = [r for r in state['results'] if r['category'] == category]
        lines.extend(['## ' + category, '', f"유효 Run: {sum(r['valid'] for r in entries)} / {expected}. 시도: {len(entries)}. 자동 재시도 없음.", ''])
        table(lines, ['Profile', 'Model', 'Run', 'Status', 'Report'],
              [[r['profile'], r['workloadModel'], r['run_number'], 'valid' if r['valid'] else 'invalid',
                f"[report](../{Path(r['directory']).name}/report.md)" if r.get('directory') else r['attempt_directory']] for r in entries])
        groups = defaultdict(list)
        for r in entries:
            if r.get('directory') and all((Path(r['directory']) / name).exists() for name in
                    ('vu-baseline-observations.json', 'run-metadata.json', 'load-generator-observations.json', 'seat-inventory.json')):
                groups[(r['profile'], r['workloadModel'])].append(r)
        csv_rows = []
        for (profile, model), runs in groups.items():
            observed = {r['run_number']: run_values(r['directory']) for r in runs}
            lines.extend(['', f'### {profile} / {model}', '', '각 Run 값을 그대로 보존합니다. 시간 단위 ms, spread와 sell-out 단위 s.', ''])
            fields = list(dict.fromkeys(field for value in observed.values() for field in value))
            rows = []
            for field in fields:
                raw = [observed.get(number, {}).get(field) for number in (1, 2, 3)]
                numeric = [float(v) for v in raw if type(v) in (int, float) and math.isfinite(v)]
                rows.append([field, *[str(v).lower() if isinstance(v, bool) else measured(v) for v in raw],
                             *([measured(min(numeric)), measured(statistics.median(numeric)), measured(max(numeric))] if numeric else ['N/A'] * 3)])
            table(lines, ['Metric', 'Run 1', 'Run 2', 'Run 3', 'Min', 'Median', 'Max'], rows)
            if category == 'Preflight':
                lines.extend(['', 'Load-generation observation: Journey 및 first-request spread, elapsed-second dispatch, native blocked/connecting 분포를 별도 보존.',
                              'Server observation: Tomcat/Hikari/JVM 값은 위 표와 Run별 시계열에 기록.',
                              'Business observation: Booking/System Failure/DB confirmed-seat 값은 위 표의 Run별 관측값. 이 차이를 단독 FAIL 근거로 사용하지 않음.'])
            for number, values in observed.items():
                csv_rows.append({'Profile': profile, 'Model': model, 'Run': number, **values})
        if csv_rows:
            columns = list(dict.fromkeys(key for row in csv_rows for key in row))
            filename = 'preflight-comparison.csv' if category == 'Preflight' else 'new-final-baseline-comparison.csv'
            with (root / filename).open('w', encoding='utf-8-sig', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=columns)
                writer.writeheader(); writer.writerows(csv_rows)
    if state.get('preflight_evidence'):
        lines.extend(['', '## Preflight Hard FAIL evidence', '', *[str(e) for e in state['preflight_evidence']]])
    if state.get('failure'):
        failure = state['failure']
        lines.extend(['', '## 중단 기록', '',
                      '실패 조합: ' + str(failure.get('profile', '환경 준비')) + ' / ' + str(failure.get('workloadModel', 'N/A')),
                      '검증 사유: ' + str(failure.get('issues', failure.get('classification', 'unknown'))),
                      '상세 예외 및 준비 기록은 series-results.json과 attempt artifact에 보존합니다. 자동 재시도 없음.'])
    if state['status'] == 'completed':
        plan = read_json(root / 'series-plan.json')
        old = read_json(plan['exploratory_index'])['accepted_runs']
        lines.extend(['', '## Exploratory / New Final 별도 비교', '', '기존 및 새 결과는 별도 행입니다. 차이만 기록하고 원인을 판정하지 않습니다.', ''])
        targets = [('musical-high', 'single-attempt'), ('idol-high', 'reselection'), ('overseas-high', 'reselection')]
        rows = []
        for profile, model in targets:
            for category, candidates in [('Exploratory Baseline', old), ('New Final Baseline', state['results'])]:
                for r in candidates:
                    if r['profile'] != profile or r['workloadModel'] != model or (category == 'New Final Baseline' and r['category'] != category):
                        continue
                    values = run_values(r['directory'])
                    rows.append([profile, model, category, r['run_number'], *[measured(values.get(key)) for key in
                        ('Journey start spread (s)', 'First request spread (s)', 'HTTP RPS', 'booking_completed', 'system_failures', 'DB confirmed seats')]])
        table(lines, ['Profile', 'Model', 'Category', 'Run', 'Journey spread (s)', 'First-request spread (s)', 'HTTP RPS', 'Booking', 'System failure', 'DB confirmed seats'], rows)
    (root / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
