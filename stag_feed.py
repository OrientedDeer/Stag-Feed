#!/usr/bin/env python3
"""stag-feed v1 — pull my own transactions + balances from SimpleFIN.

A short-lived batch job: claim a SimpleFIN setup token once, then fetch
/accounts and write two files Stag (and I) can use. No 24/7 process; a cron
entry will run the fetch step daily in v3.

Standard library only — no pip install needed.

Usage:
    python3 stag_feed.py --claim "<setup-token>"   # one-time, saves access URL
    python3 stag_feed.py                            # fetch + write files
"""

import argparse
import base64
import csv
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ACCESS_URL_FILE = os.path.join(HERE, "access_url.txt")  # secret — gitignored
OUT_DIR = os.path.join(HERE, "out")                     # personal data — gitignored

# How many days of history to request. SimpleFIN caps at 90; ask for 89 so an
# exact-90 request doesn't trip the "exceeds limit" notice.
LOOKBACK_DAYS = 89

# Cloudflare in front of the Bridge rejects the default Python-urllib agent
# (error 1010, "banned by browser signature"), so present a normal browser UA.
DEFAULT_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "*/*",
}

# Accounts whose "transactions" are investment/retirement movements (401k
# contributions, dividends, fund buys) rather than budget spending. Stag's
# budgeting can't model these, so we DROP their transactions from the import —
# but we still capture their BALANCES (the retirement engine needs those).
# Matched case-insensitively as substrings of the account name; edit to taste.
INVESTMENT_ACCOUNT_PATTERNS = ("401(K)", "401K", "IRA", "BROKERAGE")


def claim_setup_token(setup_token: str) -> str:
    """Exchange a one-time SimpleFIN setup token for a durable access URL.

    The setup token is base64 of a claim URL. POSTing (empty body) to that URL
    returns the access URL, which embeds Basic Auth credentials. We store it and
    never need the setup token again.
    """
    setup_token = setup_token.strip().strip('"').strip("'")  # tolerate paste cruft
    try:
        claim_url = base64.b64decode(setup_token).decode("utf-8").strip()
    except Exception:
        sys.exit("That doesn't look like a SimpleFIN setup token (it should be "
                 "a long base64 string that decodes to an https URL).")
    if not claim_url.startswith("https://"):
        sys.exit(f"Decoded token is not an https URL (got: {claim_url[:40]!r}). "
                 "Make sure you pasted the SETUP TOKEN, not the access URL.")

    # Show the host (not the path/creds) so the user can sanity-check what we hit.
    host = claim_url.split("/")[2] if "/" in claim_url[8:] else claim_url
    print(f"Claiming against {host} ...")

    req = urllib.request.Request(claim_url, data=b"", method="POST", headers=DEFAULT_HEADERS)
    try:
        with urllib.request.urlopen(req) as resp:
            access_url = resp.read().decode("utf-8").strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace").strip()
        if e.code == 403:
            sys.exit("SimpleFIN returned 403 Forbidden. A setup token is "
                     "single-use — this one was most likely already claimed or "
                     "is invalid. Generate a FRESH setup token in the Bridge and "
                     f"try again.\nServer said: {body or '(no message)'}")
        sys.exit(f"SimpleFIN returned HTTP {e.code}: {body or e.reason}")

    if not access_url.startswith("https://"):
        sys.exit(f"Claim succeeded but the response wasn't a URL: {access_url[:80]!r}")
    with open(ACCESS_URL_FILE, "w") as f:
        f.write(access_url)
    os.chmod(ACCESS_URL_FILE, 0o600)  # creds — keep it to the owner
    return access_url


def load_access_url() -> str:
    if not os.path.exists(ACCESS_URL_FILE):
        sys.exit(
            "No access URL found. Run once with:\n"
            '    python3 stag_feed.py --claim "<setup-token>"'
        )
    with open(ACCESS_URL_FILE) as f:
        return f.read().strip()


def _split_credentials(access_url: str) -> tuple:
    """Pull inline Basic-Auth creds out of the access URL.

    SimpleFIN hands us https://USER:PASS@host/path, but urllib can't fetch that
    form. Return (clean_url_without_creds, headers_with_Authorization).
    """
    parts = urllib.parse.urlsplit(access_url)
    if not parts.username:
        return access_url, dict(DEFAULT_HEADERS)
    creds = f"{parts.username}:{parts.password or ''}"
    token = base64.b64encode(creds.encode()).decode()
    netloc = parts.hostname + (f":{parts.port}" if parts.port else "")
    clean = urllib.parse.urlunsplit(
        (parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    headers = dict(DEFAULT_HEADERS, Authorization="Basic " + token)
    return clean, headers


def fetch_accounts(access_url: str, start_epoch: int) -> dict:
    """GET {access_url}/accounts — returns balances + transactions together."""
    base, headers = _split_credentials(access_url)
    url = f"{base}/accounts?start-date={start_epoch}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def is_investment_account(account: dict) -> bool:
    """True if this account's transactions are investment moves, not spending."""
    name = account.get("name", "").upper()
    return any(p in name for p in INVESTMENT_ACCOUNT_PATTERNS)


def _transaction_date(txn: dict) -> str:
    """Date Stag should show: the charge/transaction date (`transacted_at`) when
    available, falling back to the posted date. SimpleFIN gives both as unix
    timestamps; for cards they differ by 1-3 days (swipe vs. settle), and for
    budgeting we want the day the money was actually spent."""
    ts = txn.get("transacted_at") or txn.get("posted") or 0
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def write_transactions(data: dict, since: str = None, until: str = None) -> str:
    """Write a Stag-importable CSV: Date, Description, Amount.

    SimpleFIN's sign convention (money out = negative) already matches Stag's,
    so amounts pass straight through. `since`/`until` are inclusive YYYY-MM-DD
    bounds on the transaction date (ISO strings compare correctly as text).
    """
    path = os.path.join(OUT_DIR, "transactions.csv")
    rows = 0
    excluded = {}  # account name -> count of dropped investment transactions
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Description", "Amount", "Account"])
        for acct in data.get("accounts", []):
            txns = acct.get("transactions", [])
            if is_investment_account(acct):
                if txns:
                    excluded[acct.get("name", "?")] = len(txns)
                continue  # balances still captured in append_balances()
            acct_name = acct.get("name", acct.get("id", "unknown"))
            for txn in txns:
                date = _transaction_date(txn)
                if since and date < since:
                    continue
                if until and date > until:
                    continue
                desc = txn.get("description") or txn.get("payee") or txn.get("memo") or ""
                w.writerow([date, desc, txn.get("amount", ""), acct_name])
                rows += 1
    span = (f" from {since}" if since else "") + (f" through {until}" if until else "")
    print(f"  transactions.csv  ({rows} rows{span})")
    for name, n in excluded.items():
        print(f"    dropped {n} investment txns: {name}")
    return path


def append_balances(data: dict) -> str:
    """Append a balance snapshot per account, building history over time.

    This is the retirement-side data. Stag can't import it yet (manual entry
    only today) — we just land it durably so a future Stag feature can use it.
    """
    path = os.path.join(OUT_DIR, "balances_history.csv")
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    new_file = not os.path.exists(path)
    rows = 0
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["FetchedAt", "Org", "Account", "Balance",
                        "AvailableBalance", "BalanceDate", "Currency"])
        for acct in data.get("accounts", []):
            org = (acct.get("org") or {}).get("name", "")
            bal_date = ""
            if acct.get("balance-date"):
                bal_date = datetime.fromtimestamp(
                    int(acct["balance-date"]), tz=timezone.utc).strftime("%Y-%m-%d")
            w.writerow([fetched_at, org, acct.get("name", ""), acct.get("balance", ""),
                        acct.get("available-balance", ""), bal_date,
                        acct.get("currency", "")])
            rows += 1
    print(f"  balances_history.csv  (+{rows} rows)")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull my transactions + balances from SimpleFIN.")
    parser.add_argument("--claim", metavar="SETUP_TOKEN",
                        help="One-time: exchange a setup token for an access URL, then exit.")
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="Only include transactions on/after this date "
                             "(also narrows the fetch). Default: last %(default)s days."
                             % {"default": LOOKBACK_DAYS})
    parser.add_argument("--until", metavar="YYYY-MM-DD",
                        help="Only include transactions on/before this date.")
    args = parser.parse_args()

    if args.claim:
        claim_setup_token(args.claim)
        print(f"Claimed. Access URL saved to {ACCESS_URL_FILE}")
        return

    # Resolve the fetch window. --since narrows the request (good — SimpleFIN
    # recommends <=45 day windows); otherwise fall back to LOOKBACK_DAYS.
    for label, value in (("--since", args.since), ("--until", args.until)):
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                sys.exit(f"{label} must be YYYY-MM-DD (got {value!r}).")
    if args.since:
        start_epoch = int(datetime.strptime(args.since, "%Y-%m-%d")
                          .replace(tzinfo=timezone.utc).timestamp())
    else:
        start_epoch = int(datetime.now(timezone.utc).timestamp()) - LOOKBACK_DAYS * 86400

    access_url = load_access_url()
    data = fetch_accounts(access_url, start_epoch)

    errors = data.get("errors") or data.get("errlist") or []
    if errors:
        print("SimpleFIN reported issues:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)

    os.makedirs(OUT_DIR, exist_ok=True)
    print("Wrote:")
    write_transactions(data, since=args.since, until=args.until)
    append_balances(data)


if __name__ == "__main__":
    main()
