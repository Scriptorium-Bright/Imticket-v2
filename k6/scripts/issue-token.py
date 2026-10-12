"""Log in supplied existing accounts before measurement; never called by the runner."""
import argparse
import json
import os
import sys
from pathlib import Path
from common import http_url, positive_integer, read_json
from preparation import api_data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accounts", type=Path, help="JSON list of existing username/password pairs; no default count")
    parser.add_argument("--output", default=os.environ.get("TOKEN_OUTPUT_FILE"))
    args = parser.parse_args()
    base_url = http_url(os.environ.get("BASE_URL"), "BASE_URL")
    accounts = read_json(args.accounts) if args.accounts else [
        {"username": os.environ.get("TEST_USERNAME"), "password": os.environ.get("TEST_PASSWORD")}
    ]
    if not isinstance(accounts, list) or not accounts or not args.output:
        raise ValueError("Supply existing accounts and TOKEN_OUTPUT_FILE (or --output)")
    for account in accounts:
        if not isinstance(account, dict) or any(not isinstance(account.get(key), str) or not account[key].strip()
                                               or account[key].startswith("<") for key in ("username", "password")):
            raise ValueError("Each existing account requires a real username/password")
    if len({account["username"] for account in accounts}) != len(accounts):
        raise ValueError("Accounts input contains duplicate usernames")
    target = Path(args.output).expanduser().resolve()
    if target.exists():
        raise ValueError("TOKEN_OUTPUT_FILE already exists; select a new filename")
    if not target.parent.is_dir():
        raise ValueError("TOKEN_OUTPUT_FILE parent directory does not exist")
    tokens = []
    for account in accounts:
        data = api_data(base_url, "POST", "/api/auth/login", {"username": account["username"], "password": account["password"]})
        if not isinstance(data, dict) or not isinstance(data.get("accessToken"), str) or not data["accessToken"]:
            raise ValueError("Expected TokenResponse in data.accessToken")
        identity = positive_integer(data.get("memberId"), "TokenResponse memberId")
        tokens.append({"memberId": identity, "accessToken": data["accessToken"]})
    # Save only on full success, exclusively with 0600; no secrets on stdout.
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(tokens, output, indent=2)
        output.write("\n")
    print(f"Preissued token pool saved: {target}; entries: {len(tokens)}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"Token preparation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
