"""Scoped MySQL cleanup of recorded k6 fixtures, outside the measurement window."""
import fcntl
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from common import K6_ROOT, http_url, positive_integer, read_json, result_now, write_json


def database_command():
    container = os.environ.get("K6_DB_CONTAINER", "imticket-v2-mysql")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", container):
        raise ValueError("Invalid K6_DB_CONTAINER")
    command = ["docker", "exec", "-i", container, "sh", "-c",
               'export MYSQL_PWD="$MYSQL_PASSWORD"; '
               'exec mysql --batch --skip-column-names --user="$MYSQL_USER" "$MYSQL_DATABASE"']
    target = os.environ.get("K6_SSH_TARGET")
    if target:
        if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.@-]*", target):
            raise ValueError("Invalid K6_SSH_TARGET")
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target, shlex.join(command)]
    return command


def execute_sql(sql, marker, timeout=120):
    process = subprocess.run(database_command(), input=sql, capture_output=True, text=True, timeout=timeout)
    if process.returncode or marker not in process.stdout.splitlines():
        # SQL never includes tokens/passwords; preserve the DB error for diagnosis.
        raise ValueError(f"Fixture database command failed: {process.stderr.strip() or 'missing completion marker'}")
    return process.stdout


def probe_database():
    execute_sql("SELECT 'K6_DB_READY';\n", "K6_DB_READY", timeout=20)


def resolve_partial_fixture(manifest):
    """Resolve an uncertain catalog POST only by this fixture's exact random name."""
    fixture_id = manifest.get("fixture_id", "")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,117}", fixture_id):
        raise ValueError("Invalid generated fixture ID")
    name = "k6-" + fixture_id
    sql = f"""SELECT JSON_OBJECT(
'venue_hall_id', (SELECT id FROM venue_hall WHERE name = '{name}'),
'performance_id', (SELECT id FROM performance WHERE title = '{name}'),
'performance_time_id', (SELECT pt.id FROM performance_time pt
 JOIN performance p ON p.id = pt.performance_id JOIN venue_hall h ON h.id = pt.venue_hall_id
 WHERE p.title = '{name}' AND h.name = '{name}'));
SELECT 'K6_PARTIAL_IDS';
"""
    resolved = json.loads(execute_sql(sql, "K6_PARTIAL_IDS").splitlines()[0])
    for key, value in resolved.items():
        if manifest.get(key) is not None and manifest[key] != value:
            raise ValueError("Partial fixture IDs differ from the recorded catalog identity")
        manifest[key] = value
    return manifest


def partial_cleanup_sql(manifest):
    hall = positive_integer(manifest["venue_hall_id"], "partial hall ID") if manifest.get("venue_hall_id") else 0
    performance = positive_integer(manifest["performance_id"], "partial performance ID") if manifest.get("performance_id") else 0
    name = "k6-" + manifest["fixture_id"]  # Validated by resolve_partial_fixture.
    return f"""START TRANSACTION;
CREATE TEMPORARY TABLE k6_guard (id INT PRIMARY KEY);
INSERT INTO k6_guard VALUES (1);
INSERT INTO k6_guard SELECT 1 WHERE
  ({hall} <> 0 AND NOT EXISTS (SELECT 1 FROM venue_hall WHERE id = {hall} AND name = '{name}'))
  OR ({performance} <> 0 AND NOT EXISTS (SELECT 1 FROM performance WHERE id = {performance} AND title = '{name}'))
  OR EXISTS (SELECT 1 FROM performance_time WHERE venue_hall_id = {hall} OR performance_id = {performance});
DELETE FROM seat_price WHERE performance_id = {performance};
DELETE FROM performance WHERE id = {performance};
DELETE FROM venue_hall_seat_template WHERE venue_hall_id = {hall};
DELETE FROM venue_hall WHERE id = {hall};
COMMIT;
SELECT 'K6_CLEANUP_OK';
"""


def cleanup_sql(manifest, run_id=None, mode="delete"):
    if mode not in {"delete", "reset"}:
        raise ValueError("Cleanup mode must be delete/reset")
    fixture_id = manifest.get("fixture_id", "")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,117}", fixture_id):
        raise ValueError("Invalid generated fixture ID")
    hall = positive_integer(manifest.get("venue_hall_id"), "fixture hall ID")
    performance = positive_integer(manifest.get("performance_id"), "fixture performance ID")
    time_id = positive_integer(manifest.get("performance_time_id"), "fixture time ID")
    members = manifest.get("test_member_ids")
    if not isinstance(members, list) or not members:
        raise ValueError("Cleanup requires recorded test member IDs; prepare a new fixture")
    member_ids = ",".join(str(positive_integer(value, "test member ID")) for value in members)
    key_match = "FALSE"
    if run_id:
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,110}", run_id):
            raise ValueError("Invalid fixture claim run ID")
        prefix = "reserve-" + run_id + "-"
        key_match = f"(ri.member_id IN ({member_ids}) AND LEFT(ri.idempotency_key, {len(prefix)}) = '{prefix}')"
    # A duplicate insert into the guard aborts the mysql batch. The connection
    # closes without COMMIT and rolls back ALL mutations on any later SQL error.
    sql = f"""START TRANSACTION;
CREATE TEMPORARY TABLE k6_guard (id INT PRIMARY KEY);
INSERT INTO k6_guard VALUES (1);
INSERT INTO k6_guard SELECT 1 WHERE NOT (
  EXISTS (SELECT 1 FROM venue_hall WHERE id = {hall} AND name = 'k6-{fixture_id}')
  AND EXISTS (SELECT 1 FROM performance WHERE id = {performance} AND title = 'k6-{fixture_id}')
  AND EXISTS (SELECT 1 FROM performance_time WHERE id = {time_id}
              AND performance_id = {performance} AND venue_hall_id = {hall})
);
CREATE TEMPORARY TABLE k6_reservations (id BIGINT PRIMARY KEY);
INSERT INTO k6_reservations SELECT DISTINCT rs.reservation_id FROM reserved_seat rs
JOIN seat s ON s.id = rs.seat_id WHERE s.performance_time_id = {time_id};
INSERT INTO k6_guard SELECT 1 WHERE EXISTS (
  SELECT 1 FROM reserved_seat rs JOIN k6_reservations kr ON kr.id = rs.reservation_id
  JOIN seat s ON s.id = rs.seat_id WHERE s.performance_time_id <> {time_id}
);
INSERT INTO k6_guard SELECT 1 WHERE EXISTS (
  SELECT 1 FROM reservation r JOIN k6_reservations kr ON kr.id = r.id
  WHERE r.member_id NOT IN ({member_ids})
);
INSERT INTO k6_guard SELECT 1 WHERE EXISTS (
  SELECT 1 FROM payment_attempt pa JOIN payment_order po ON po.id = pa.payment_order_id
  JOIN k6_reservations kr ON kr.id = po.reservation_id WHERE pa.provider <> 'FAKE'
);
SELECT r.id FROM reservation r JOIN k6_reservations kr ON kr.id = r.id FOR UPDATE;
SELECT id FROM seat WHERE performance_time_id = {time_id} FOR UPDATE;
DELETE pa FROM payment_attempt pa JOIN payment_order po ON po.id = pa.payment_order_id
JOIN k6_reservations kr ON kr.id = po.reservation_id;
DELETE po FROM payment_order po JOIN k6_reservations kr ON kr.id = po.reservation_id;
DELETE ri FROM reservation_idempotency ri LEFT JOIN k6_reservations kr ON kr.id = ri.reservation_id
WHERE kr.id IS NOT NULL OR {key_match};
DELETE rs FROM reserved_seat rs JOIN k6_reservations kr ON kr.id = rs.reservation_id;
DELETE r FROM reservation r JOIN k6_reservations kr ON kr.id = r.id;
"""
    if mode == "reset":
        sql += f"UPDATE seat SET seat_status = 'AVAILABLE', version = COALESCE(version, 0) + 1 WHERE performance_time_id = {time_id};\n"
    else:
        # Refuse shared catalog parents before changing them. Never disable FKs.
        sql += f"""INSERT INTO k6_guard SELECT 1 WHERE EXISTS (
  SELECT 1 FROM performance_time WHERE id <> {time_id}
  AND (performance_id = {performance} OR venue_hall_id = {hall})
);
DELETE FROM seat WHERE performance_time_id = {time_id};
DELETE FROM performance_time WHERE id = {time_id};
DELETE FROM seat_price WHERE performance_id = {performance};
DELETE FROM performance WHERE id = {performance};
DELETE FROM venue_hall_seat_template WHERE venue_hall_id = {hall};
DELETE FROM venue_hall WHERE id = {hall};
"""
    return sql + "COMMIT;\nSELECT 'K6_CLEANUP_OK';\n"


def fixture_claim(path):
    claim_path = path.parent / "measurement-claim.json"
    if not claim_path.exists():
        return None
    claim = read_json(claim_path)
    run_id = claim.get("run_id", "")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", run_id):
        raise ValueError("Invalid fixture claim")
    metadata_path = K6_ROOT / "results" / run_id / "run-metadata.json"
    if not metadata_path.exists():
        raise ValueError("Cannot confirm claimed run has stopped: run metadata missing")
    metadata = read_json(metadata_path)
    if metadata.get("status") in {"preflight", "running", "postprocessing"}:
        raise ValueError("Cannot clean a fixture while its run is active or interrupted")
    return run_id


def clean_fixture(path, mode="delete", dry_run=False):
    path = Path(path).expanduser().resolve(strict=True)
    with (path.parent / "cleanup.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another cleanup is using this fixture") from None
        manifest = read_json(path)
        base_url = http_url(os.environ.get("BASE_URL"), "BASE_URL")
        if http_url(manifest.get("base_url"), "fixture BASE_URL") != base_url:
            raise ValueError("Cleanup fixture differs from BASE_URL")
        if manifest.get("status") == "cleaned":
            print("Fixture already cleaned", flush=True)
            return
        run_id = fixture_claim(path)
        if manifest.get("status") == "failed" and mode == "delete":
            manifest = resolve_partial_fixture(manifest)
            sql = cleanup_sql(manifest, run_id, mode) if manifest.get("performance_time_id") else partial_cleanup_sql(manifest)
        else:
            sql = cleanup_sql(manifest, run_id, mode)
        if dry_run:
            print(sql)
            return
        execute_sql(sql, "K6_CLEANUP_OK")
        event = {"mode": mode, "completed_at": result_now(), "run_id": run_id}
        manifest.setdefault("cleanup_history", []).append(event)
        manifest["status"] = "cleaned" if mode == "delete" else "ready"
        write_json(path, manifest)
        if mode == "reset":
            claim = path.parent / "measurement-claim.json"
            if claim.exists():
                claim.rename(path.parent / f"measurement-claim.{run_id}.json")
        if run_id:
            write_json(K6_ROOT / "results" / run_id / "fixture-cleanup.json", event)
        print(f"Fixture {mode} completed: time {manifest['performance_time_id']}", flush=True)
