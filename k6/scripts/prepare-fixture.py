"""Create a fresh hall -> performance -> time via existing APIs, before k6 starts."""
import argparse
import os
import shlex
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common import K6_ROOT, file_sha256, http_url, positive_integer, read_json, utc_now, write_json
from preparation import api_data, token_pool_from_env, verify_initial_seats


def fixture_conditions(profile, template):
    seat_count = positive_integer(profile["seatCount"], "profile seatCount")
    seats_per_row = positive_integer(template["seatsPerRow"], "seatsPerRow", 1000)
    if seat_count % seats_per_row:
        raise ValueError("seatCount must be divisible by seatsPerRow; the hall API only supports rectangular layouts")
    rows = positive_integer(seat_count // seats_per_row, "rows", 1000)
    section = template.get("section")
    if not isinstance(section, str) or not section.strip() or len(section) > 20:
        raise ValueError("Fixture section must contain 1–20 characters")
    grade = template.get("grade")
    if grade not in {"VIP", "R", "S", "A"}:
        raise ValueError("Fixture grade must be VIP/R/S/A")
    price = positive_integer(template.get("price"), "fixture price", (2**31 - 1) // 3)
    days = positive_integer(template.get("startsAfterDays"), "startsAfterDays", 3650)
    return {"seatCount": seat_count, "section": section, "rows": rows,
            "seatsPerRow": seats_per_row, "grade": grade, "price": price, "startsAfterDays": days}


def prepare(profile_name, template_path, timeout, exports_file=None):
    base_url = http_url(os.environ.get("BASE_URL"), "BASE_URL")
    pool = token_pool_from_env()
    profiles = read_json(K6_ROOT / "config" / "profiles.json")
    if profile_name not in profiles:
        raise ValueError("Select a profile from k6/config/profiles.json")
    conditions = fixture_conditions(profiles[profile_name], read_json(template_path))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    fixture_id = f"{timestamp}_{profile_name}_{uuid.uuid4().hex[:8]}"
    directory = K6_ROOT / "fixtures" / fixture_id
    directory.mkdir(mode=0o700)
    manifest_path = directory / "fixture-manifest.json"
    starts_at = (datetime.now(timezone.utc) + timedelta(days=conditions["startsAfterDays"])).replace(tzinfo=None).isoformat(timespec="seconds")
    manifest = {
        "schema_version": 1, "fixture_id": fixture_id, "status": "preparing",
        "profile": profile_name, "base_url": base_url, "conditions": conditions,
        "prepared_started_at": utc_now(), "prepared_ended_at": None,
        "initial_state": None, "venue_hall_id": None, "performance_id": None,
        "performance_time_id": None, "requests": [], "starts_at": starts_at,
        "test_member_ids": sorted({u["memberId"] for u in pool if "memberId" in u}),
        "starts_at_basis": "UTC wall clock represented as API LocalDateTime; not a measurement stage",
    }
    write_json(manifest_path, manifest)
    print(f"Fixture manifest: {manifest_path}", flush=True)
    name = "k6-" + fixture_id
    requests = [
        ("venue_hall_id", "/api/catalog/halls", {"name": name, "section": conditions["section"],
          "rows": conditions["rows"], "seatsPerRow": conditions["seatsPerRow"], "grade": conditions["grade"]}),
        ("performance_id", "/api/catalog/performances", {"title": name, "prices": {conditions["grade"]: conditions["price"]}}),
    ]
    token = pool[0]["accessToken"]
    try:
        for field, path, payload in requests:
            record = {"method": "POST", "path": path, "body": payload, "status": "in-flight"}
            manifest["requests"].append(record)
            write_json(manifest_path, manifest)
            manifest[field] = positive_integer(api_data(base_url, "POST", path, payload, token, timeout), field)
            record.update(status="succeeded", returned_id=manifest[field])
            write_json(manifest_path, manifest)
        path = f"/api/catalog/performances/{manifest['performance_id']}/times"
        payload = {"venueHallId": manifest["venue_hall_id"], "startsAt": starts_at}
        record = {"method": "POST", "path": path, "body": payload, "status": "in-flight"}
        manifest["requests"].append(record)
        write_json(manifest_path, manifest)
        manifest["performance_time_id"] = positive_integer(api_data(base_url, "POST", path, payload, token, timeout), "performanceTimeId")
        record.update(status="succeeded", returned_id=manifest["performance_time_id"])
        write_json(manifest_path, manifest)
        seats = verify_initial_seats(base_url, manifest["performance_time_id"], token, conditions, timeout)
        snapshot = directory / "initial-seats.json"
        write_json(snapshot, seats)
        manifest.update(status="ready", initial_state="all-AVAILABLE", initial_seats_sha256=file_sha256(snapshot),
                        prepared_ended_at=utc_now(), verified_seat_count=len(seats))
        write_json(manifest_path, manifest)
        exports = (f"export FIXTURE_MANIFEST={shlex.quote(str(manifest_path))}\n"
                   f"export PERFORMANCE_TIME_ID={manifest['performance_time_id']}\n"
                   f"export WORKLOAD_PROFILE={shlex.quote(profile_name)}\n")
        (directory / "fixture.env.sh").write_text(exports, encoding="utf-8")
        # Convenience pointer contains IDs/paths only. Every fixture's original file is retained.
        pointer = exports_file or (K6_ROOT / "fixtures" / "current.env.sh")
        pointer.write_text(exports, encoding="utf-8")
    except (ValueError, OSError) as error:
        manifest.update(status="failed", prepared_ended_at=utc_now())
        write_json(manifest_path, manifest)
        print(f"Fixture preparation failed; saved partial manifest: {manifest_path}", file=sys.stderr)
        error.fixture_manifest_path = manifest_path
        raise
    print(f"performanceTimeId: {manifest['performance_time_id']}; seats: {conditions['seatCount']} (all AVAILABLE)")
    print(f"Next: source {shlex.quote(str(directory / 'fixture.env.sh'))}")
    return manifest_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("WORKLOAD_PROFILE"))
    parser.add_argument("--template", default=str(K6_ROOT / "config" / "fixture-template.json"))
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--exports-file", type=Path,
                        help="Optional environment pointer file; default k6/fixtures/current.env.sh")
    args = parser.parse_args()
    try:
        timeout = positive_integer(args.timeout_seconds, "preparation timeout")
        prepare(args.profile, args.template, timeout, args.exports_file)
    except (ValueError, OSError) as error:
        print(f"Cannot prepare fixture: {error}", file=sys.stderr)
        raise SystemExit(2)
