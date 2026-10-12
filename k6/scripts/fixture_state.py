"""Validate and claim prepared fixtures; no reset SQL or new server endpoints."""
import os
from pathlib import Path

from common import file_sha256, http_url, positive_integer, read_json, result_now, write_json
from preparation import verify_initial_seats


def prepared_fixture(path, base_url, profile_name=None):
    if not path:
        raise ValueError("Prepare a fresh fixture and set FIXTURE_MANIFEST (or --fixture)")
    path = Path(path).expanduser().resolve(strict=True)
    manifest = read_json(path)
    if (manifest.get("schema_version") != 1 or manifest.get("status") != "ready"
            or manifest.get("initial_state") != "all-AVAILABLE"):
        raise ValueError("Fixture must be a successfully prepared all-AVAILABLE fixture")
    if http_url(manifest.get("base_url"), "fixture base_url") != base_url:
        raise ValueError("Fixture BASE_URL differs from the measurement target")
    if profile_name and manifest.get("profile") != profile_name:
        raise ValueError("Fixture profile differs from the selected workload profile")
    identity = positive_integer(manifest.get("performance_time_id"), "fixture performanceTimeId")
    supplied = os.environ.get("PERFORMANCE_TIME_ID")
    if supplied and positive_integer(supplied, "PERFORMANCE_TIME_ID") != identity:
        raise ValueError("PERFORMANCE_TIME_ID differs from FIXTURE_MANIFEST; source the current fixture.env.sh")
    if (path.parent / "measurement-claim.json").exists():
        raise ValueError("Fixture already claimed by a previous run; prepare a new fixture")
    snapshot = path.parent / "initial-seats.json"
    if file_sha256(snapshot) != manifest.get("initial_seats_sha256"):
        raise ValueError("Fixture snapshot fingerprint differs from its preparation manifest")
    return path, manifest


def claim_fixture(path, manifest, base_url, token, run_id):
    # Exclusive local claim prevents two runners from measuring the same prepared time.
    claim_path = path.parent / "measurement-claim.json"
    try:
        descriptor = os.open(claim_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError("Fixture was claimed by another run; prepare a new fixture") from None
    os.close(descriptor)
    claim = {"run_id": run_id, "claimed_at": result_now(), "status": "initial-state-check"}
    write_json(claim_path, claim)
    try:
        seats = verify_initial_seats(base_url, manifest["performance_time_id"], token, manifest["conditions"])
        if seats != read_json(path.parent / "initial-seats.json"):
            raise ValueError("Live seat IDs/layout differ from the recorded initial fixture")
        claim.update(status="claimed", initial_state_verified_at=result_now(), verified_seat_count=len(seats))
    except (ValueError, OSError):
        claim.update(status="initial-state-check-failed", checked_at=result_now())
        write_json(claim_path, claim)
        raise
    write_json(claim_path, claim)
    return claim
