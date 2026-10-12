"""Reset a recorded fixture's reservations/payments/seats, or delete its catalog too."""
import argparse
import os
import subprocess
import sys

from fixture_cleanup import clean_fixture


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default=os.environ.get("FIXTURE_MANIFEST"))
    parser.add_argument("--mode", choices=["reset", "delete"], default="reset")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if not args.fixture:
            raise ValueError("Supply --fixture or FIXTURE_MANIFEST")
        clean_fixture(args.fixture, args.mode, args.dry_run)
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"Cannot clean fixture: {error}", file=sys.stderr)
        raise SystemExit(2)
