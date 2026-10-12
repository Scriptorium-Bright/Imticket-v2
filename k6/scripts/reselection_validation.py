"""Audit one controlled conflict from real k6 events, after execution."""
import json
from pathlib import Path

from common import result_time, write_json


def audit_validation(run_dir, observations):
    run_dir = Path(run_dir)
    trace = []
    path = run_dir / 'k6-points.json'
    if path.exists():
        for line in path.read_text().splitlines():
            point = json.loads(line)
            if point.get('type') == 'Point' and point.get('metric') == 'validation_events' and point['data']['value']:
                data = point['data']
                trace.append({'event': data['tags']['event'], 'at': result_time(data['time']),
                              **json.loads(data['tags']['details'])})
    counts = observations['count_totals']
    maps = [event for event in trace if event['event'] == 'seat-map']
    selections = [event for event in trace if event['event'] == 'seat-selection']
    held = [event for event in trace if event['event'] == 'holder-pre-reserved']
    conflicts = [event for event in trace if event['event'] == 'pre-reserve-response' and event.get('code') == 'SEAT_UNAVAILABLE']
    reserved = [event for event in trace if event['event'] == 'subject-pre-reserved']
    endings = [event for event in trace if event['event'] == 'journey-ended']
    expected_order = ['seat-map', 'seat-selection', 'holder-pre-reserved', 'pre-reserve-response',
                      'seat-map', 'seat-selection', 'subject-pre-reserved', 'journey-ended']
    expected_counts = {'booking_journey_started': 1, 'booking_journey_completed': 1, 'seat_conflicts': 1,
                       'reselections': 1, 'pre_reserve_attempts': 2, 'seat_selection_attempts': 2,
                       'journey_started': 1, 'journey_finished': 1, 'iterations': 1,
                       'booking_completed': 1, 'reservation_success': 1, 'payment_applied': 1, 'seats_confirmed': 1,
                       'http_attempted': 11, 'http_completed': 11, 'http_reqs': 11,
                       'system_failures': 0, 'business_failures': 0, 'dropped_iterations': 0}
    checks = {
        'counter_values': all(counts.get(name) == value for name, value in expected_counts.items()),
        'server_request_order': [event['event'] for event in trace] == expected_order,
        'actual_seat_unavailable': len(conflicts) == 1 and conflicts[0].get('outcome') == 'business-failure',
        'seat_map_refetched_after_conflict': len(maps) == 2 and maps[0].get('target_status') == 'AVAILABLE'
            and maps[1].get('target_seat_id') == maps[0].get('target_seat_id') and maps[1].get('target_status') == 'LOCKED',
        'different_available_seat': len(selections) == 2 and all(len(s.get('seat_ids', [])) == 1 for s in selections)
            and not set(selections[0]['seat_ids']) & set(selections[1]['seat_ids'])
            and selections[1].get('statuses') == ['AVAILABLE'],
        'holder_matches_first_selection': len(held) == 1 and len(selections) == 2
            and held[0]['seat_ids'] == selections[0]['seat_ids'],
        'final_booking_completed': len(reserved) == 1 and len(selections) == 2
            and reserved[0]['seat_ids'] == selections[1]['seat_ids'] and len(endings) == 1
            and endings[0].get('outcome') == 'BOOKING_COMPLETED' and endings[0].get('retries') == 1,
    }
    result = {'status': 'passed' if all(checks.values()) else 'failed',
              'purpose': 'controlled-reselection-code-path-validation', 'performance_measurement': False,
              'checks': checks, 'expected_counts': expected_counts,
              'observed_counts': {name: counts.get(name) for name in expected_counts}, 'trace': trace,
              'holder_note': 'Two extra holder API requests are included in HTTP metrics; holder is not a second Journey. Its pending reservation is scoped to the disposable fixture.'}
    write_json(run_dir / 'reselection-validation.json', result)
    return result
