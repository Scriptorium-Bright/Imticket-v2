"""Generate/reuse random test accounts and refresh their tokens through auth APIs."""
import argparse
import os
import shlex
import sys
from pathlib import Path

from common import K6_ROOT
from test_accounts import prepare_accounts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", default=os.environ.get("TEST_USER_COUNT", "10"))
    parser.add_argument("--workers", default=os.environ.get("TEST_ACCOUNT_WORKERS", "4"))
    parser.add_argument("--accounts", type=Path)
    args = parser.parse_args()
    pool, tokens = prepare_accounts(os.environ.get("BASE_URL"), args.users, args.workers, args.accounts)
    pointer = K6_ROOT / "fixtures" / "accounts.env.sh"
    pointer.write_text("unset TEST_ACCESS_TOKEN\n"
                       f"export TEST_TOKENS_FILE={shlex.quote(str(tokens))}\n"
                       f"export TEST_ACCOUNTS_FILE={shlex.quote(str(pool))}\n", encoding="utf-8")
    print(f"Next: source {shlex.quote(str(pointer))}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"Cannot prepare accounts: {error}", file=sys.stderr)
        raise SystemExit(2)
