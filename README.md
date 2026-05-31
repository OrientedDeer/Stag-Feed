# stag-feed

A tiny, self-hosted, single-user tool to pull **your own** bank transactions and
account balances and hand them to [Stag](https://github.com/OrientedDeer/Stag).

This is **not** a Plaid-style data aggregator. It runs on your own home server,
against your own accounts only — which is what keeps it out of the compliance
burden (no SOC 2, no money-transmitter license, no holding anyone else's data).

## What it is, mechanically

A **batch job**, not a 24/7 service. A scheduler (cron) wakes it once a day; it
fetches, writes two files, and exits. SimpleFIN only refreshes ~once/day, so
that's all the cadence we need.

```
cron (daily)  ->  stag_feed.py  ->  SimpleFIN /accounts  ->  writes:
                                                              out/transactions.csv   (Stag-importable)
                                                              out/balances_history.csv (appended each run)
```

## Why SimpleFIN (the method decision)

The hard part of this space is per-bank connector maintenance, US banks
especially. We deliberately don't take that on.

| Method | Verdict |
|---|---|
| **SimpleFIN Bridge** | **Chosen.** ~$15/yr. Read-only, clearly allowed, never sees our app's bank password. Outsources connector maintenance to the aggregator (MX) behind it. One `/accounts` call returns balances **and** transactions for all linked accounts. |
| File export (OFX/CSV) | Free & fully local, but manual — can't be automated. Rejected because the goal is hands-off. |
| OFX Direct Connect | Free & automated, but dead at most major US banks now. Survives at some credit unions / a few brokerages. Not reliable. |
| Official bank APIs | Not available to US individuals. |
| Screen-scraping | Brittle + likely a ToS breach. Avoided. |

On legality: accessing **your own** account with your own credentials is a
contract question (a bank's ToS), essentially never a CFAA/criminal one
(post-*Van Buren*, the test is bypassing a technical barrier — using your own
login is not that). SimpleFIN keeps us a *customer* of a sanctioned service, not
an aggregator, so there's no ToS gray area to worry about.

## Institution support

Check each of your institutions against the SimpleFIN supported-institutions
list before relying on it. Major banks and brokerages are generally well
covered; **retirement recordkeepers (401k providers) are the wildcard** — they
often return balances-only, or aren't covered at all. Confirm empirically.

## How it feeds Stag

Stag is a **retirement planner with budgeting strapped on** to drive weekly use.
Both data streams matter:

| Data | Stag side | Status |
|---|---|---|
| **Transactions** | Existing CSV importer (Budget tab) — fingerprints a bank's format and remembers the mapping. Stag's `Transaction` needs only `date, description, amount`. | Solved — just emit a CSV. |
| **Balances** | The retirement engine (net worth over time). Stag tracks balances via `MonthlySnapshot`, but **only via manual entry today** — importing them would be a new Stag feature. | **New Stag-side work** (separate ticket, separate repo). |

SimpleFIN's sign convention matches Stag's (money out = negative), so
transactions map ~1:1 with no transform.

> Note: Stag is a browser app (localStorage, no backend). So the *last inch*
> into Stag is either a manual CSV import click, or a future Stag-side feature.
> stag-feed's job ends at "correct files on the server."

### Account handling

Investment/retirement accounts (401k, IRA, brokerage) get their **balances**
captured but their **transactions dropped** from `transactions.csv` — those are
contributions/dividends/fund buys, not budget spending, and Stag's budgeting
can't model them. Controlled by `INVESTMENT_ACCOUNT_PATTERNS` in `stag_feed.py`.

**401(k) caveat:** a 401(k) typically comes back as a single total balance with
**no Roth/Traditional source split** — that breakdown isn't published through
the aggregation feed (it's not in the balance, holdings, or transactions). If
you need the split for tax-aware projections, seed the ratio once from the
provider's website, model it as two accounts in Stag, and use stag-feed's
automated total as the reconciliation check. Don't have stag-feed guess it.

## Roadmap (kept deliberately small)

- **v1 — works once, by hand.** This repo. One linked account → fetch
  `/accounts` → write `transactions.csv` (Stag-importable) + append
  `balances_history.csv`. Run it manually. **Ship here.**
- **v2 — all accounts, still by hand.** Link the rest in the Bridge. Same script
  (≈no code change — one access URL covers all accounts). Sort out how
  retirement/investment accounts should be handled.
- **v3 — hands-off.** Add a cron entry + de-duplication so re-runs don't
  double-import.
- **v4 — into Stag.** Decide the last inch (manual import vs. a Stag-side
  balance/transaction import feature). This is a separate project.

### Scope guardrails
v1 does **not**: touch Stag, run on a schedule, dedup, handle multiple banks, or
categorize. If any of those try to sneak into v1, that's the balloon — park it
on the roadmap above.

## Usage (v1)

```bash
# 1. One-time: sign up at https://bridge.simplefin.org, link ONE account,
#    copy the Setup Token it gives you.

# 2. Claim the token -> saves an access URL locally (do this once):
python3 stag_feed.py --claim "PASTE_SETUP_TOKEN_HERE"

# 3. Fetch and write files (this is what cron will run later):
python3 stag_feed.py

# outputs land in ./out/
```

No dependencies — Python 3 standard library only.
