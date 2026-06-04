# Stag-Feed

Self-hosted, single-user tool to pull your own bank transactions and balances
via [SimpleFIN](https://www.simplefin.org/) and export them as a
budgeting-app-ready CSV. Python standard library only — no dependencies.

A single `/accounts` call returns every linked account's balances and
transactions, and the script writes two files to `./out/`:

- **`transactions.csv`** — `Date, Description, Amount, Account, Id` (the trailing
  `Id` is SimpleFIN's stable transaction id, used for de-duplication), importable
  into [Stag](https://github.com/OrientedDeer/Stag) or any CSV-import budgeting app.
- **`balances.csv`** — the current balance per account, overwritten each run.

Transactions use the charge date (`transacted_at`) where available.
Investment/retirement accounts (401k, IRA, brokerage) contribute their balances
but have their transactions dropped from the CSV — configurable via
`INVESTMENT_ACCOUNT_PATTERNS` in `stag_feed.py`.

## Setup

Requires a [SimpleFIN Bridge](https://bridge.simplefin.org) subscription
(~$15/yr). Link your accounts there, copy the setup token, then:

```bash
python3 stag_feed.py --claim "PASTE_SETUP_TOKEN"   # one-time; saves access URL locally
python3 stag_feed.py                                # fetch + write files to ./out/
```

Limit the window for incremental imports (until dedup lands, use this to control
overlap):

```bash
python3 stag_feed.py --since 2026-05-23 --until 2026-05-31
```

Point cron at the fetch command to run it on a schedule.

## What's next

- Scheduled syncs with de-duplication on SimpleFIN's stable transaction `id`, so
  re-runs never create duplicates.
