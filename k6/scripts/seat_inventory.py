"""Post-workload seat observations and a read-only, fixture-scoped confirmed-seat audit."""
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from common import positive_integer, read_json, result_now, write_json
from fixture_cleanup import execute_sql
from preparation import api_data


def snapshot_sql(manifest):
    identity = manifest.get('fixture_id', '')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,117}', identity):
        raise ValueError('Invalid inventory fixture ID')
    hall = positive_integer(manifest.get('venue_hall_id'), 'inventory hall ID')
    performance = positive_integer(manifest.get('performance_id'), 'inventory performance ID')
    time_id = positive_integer(manifest.get('performance_time_id'), 'inventory time ID')
    return f"""SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;
START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY;
SELECT JSON_OBJECT('catalog_matches', EXISTS (
 SELECT 1 FROM performance_time pt JOIN performance p ON p.id=pt.performance_id
 JOIN venue_hall h ON h.id=pt.venue_hall_id WHERE pt.id={time_id}
 AND p.id={performance} AND h.id={hall} AND p.title='k6-{identity}' AND h.name='k6-{identity}'),
 'seats', COALESCE((SELECT JSON_ARRAYAGG(JSON_OBJECT('id', s.id, 'status', s.seat_status))
 FROM seat s WHERE s.performance_time_id={time_id}), JSON_ARRAY()),
 'confirmed_assignments', COALESCE((SELECT JSON_ARRAYAGG(JSON_OBJECT(
 'seat_id', rs.seat_id, 'reservation_id', rs.reservation_id))
 FROM reserved_seat rs JOIN seat s ON s.id=rs.seat_id
 JOIN reservation r ON r.id=rs.reservation_id
 WHERE s.performance_time_id={time_id} AND r.reservation_status='CONFIRMED'), JSON_ARRAY()));
COMMIT;
SELECT 'K6_INVENTORY_OK';
"""


def seat_counts(seats):
    if not isinstance(seats, list):
        raise ValueError('Inventory seats must be a list')
    ids, counts = [], Counter()
    for seat in seats:
        ids.append(positive_integer(seat.get('id'), 'inventory seat ID'))
        if seat.get('status') not in {'AVAILABLE', 'LOCKED', 'RESERVED'}:
            raise ValueError('Unexpected seat status in inventory')
        counts[seat['status']] += 1
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate seat IDs in inventory snapshot')
    return {'seat_count': len(ids), **{status.lower() + '_seat_count': counts[status] for status in ('AVAILABLE', 'LOCKED', 'RESERVED')}}


def audit_snapshot(initial_seats, snapshot):
    if snapshot.get('catalog_matches') != 1:
        raise ValueError('Inventory DB catalog differs from the recorded fixture')
    initial_ids = {positive_integer(s['id'], 'initial seat ID') for s in initial_seats}
    seats = snapshot['seats']
    counts = seat_counts(seats)
    final_ids = {s['id'] for s in seats}
    assignments, reservation_ids, mapping_counts = defaultdict(set), set(), Counter()
    for row in snapshot['confirmed_assignments']:
        seat_id = positive_integer(row['seat_id'], 'confirmed seat ID')
        reservation_id = positive_integer(row['reservation_id'], 'confirmed reservation ID')
        assignments[seat_id].add(reservation_id)
        reservation_ids.add(reservation_id)
        mapping_counts[(seat_id, reservation_id)] += 1
    repeated_mappings = [{'seat_id': s, 'reservation_id': r, 'count': count}
                         for (s, r), count in sorted(mapping_counts.items()) if count > 1]
    duplicates = {str(s): sorted(rs) for s, rs in assignments.items() if len(rs) > 1}
    unique = len(assignments)
    missing, unexpected = sorted(initial_ids - final_ids), sorted(final_ids - initial_ids)
    outside = sorted(set(assignments) - initial_ids)
    inconsistent = sorted(s['id'] for s in seats if s['id'] in assignments and s['status'] != 'RESERVED')
    invariant = not duplicates and not outside and unique <= len(initial_ids)
    exact_inventory = not missing and not unexpected and len(initial_ids) == len(initial_seats)
    return {'status': 'verified' if invariant and exact_inventory and not inconsistent and not repeated_mappings else 'failed',
            'source': 'MySQL consistent read-only snapshot', 'initial_seat_count': len(initial_ids),
            **counts, 'confirmed_seat_assignments': len(snapshot['confirmed_assignments']),
            'unique_sold_seat_count': unique, 'confirmed_booking_count': len(reservation_ids),
            'duplicate_oversold_seat_count': len(duplicates), 'duplicate_seat_reservations': duplicates,
            'duplicate_reservation_mapping_count': len(repeated_mappings), 'duplicate_reservation_mappings': repeated_mappings,
            'excess_confirmed_assignments': sum(max(0, len(rs) - 1) for rs in assignments.values()),
            'missing_initial_seat_ids': missing, 'unexpected_seat_ids': unexpected,
            'confirmed_seats_outside_initial_fixture': outside, 'confirmed_seats_not_reserved': inconsistent,
            'oversell_invariant': 'passed' if invariant else 'failed',
            'inventory_identity_invariant': 'passed' if exact_inventory else 'failed',
            'seat_sell_through_rate': unique / len(initial_ids) if initial_ids else None,
            'sell_through_definition': 'unique confirmed Seat IDs / initial Seat IDs',
            'server_average_seats_per_confirmed_booking': len(snapshot['confirmed_assignments']) / len(reservation_ids) if reservation_ids else None}


def collect_inventory(run_dir, manifest, base_url, token, mode):
    run_dir = Path(run_dir)
    initial = read_json(run_dir / 'fixture-initial-seats.json')
    observation = {'status': 'not-verified', 'mode': mode, 'inspection_started_at': result_now(),
                   'initial_seat_count': len(initial), 'api': None, 'db': None, 'errors': [],
                   'oversell_invariant': 'not-verified'}
    try:
        seats = api_data(base_url, 'GET', f"/api/seats/{manifest['performance_time_id']}", token=token)
        counts = seat_counts(seats)
        write_json(run_dir / 'fixture-final-seats.json', seats)
        observation['api'] = {**counts, 'observed_at': result_now(), 'source': 'existing seat-map API'}
        observation['status'] = 'api-observed'
    except (ValueError, OSError) as error:
        observation['errors'].append({'source': 'api', 'error': str(error)})
    if mode == 'db':
        try:
            sql = snapshot_sql(manifest)
            (run_dir / 'seat-inventory-query.sql').write_text(sql, encoding='utf-8')
            raw = execute_sql(sql, 'K6_INVENTORY_OK', timeout=30)
            snapshot = json.loads(raw.splitlines()[0])
            write_json(run_dir / 'seat-inventory-db-snapshot.json', snapshot)
            observation['db'] = audit_snapshot(initial, snapshot)
            observation['status'] = observation['db']['status']
            observation['oversell_invariant'] = observation['db']['oversell_invariant']
        except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
            observation['status'] = 'not-verified'
            observation['errors'].append({'source': 'db', 'error': str(error)})
    observation['inspection_ended_at'] = result_now()
    observation['note'] = 'After k6 exit, before fixture cleanup. API and DB observations have separate instants; API status counts alone cannot verify oversell.'
    write_json(run_dir / 'seat-inventory.json', observation)
    return observation
