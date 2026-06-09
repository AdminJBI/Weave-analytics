# Weave → Google Sheets daily call report

Every morning at **6:05 AM Pacific**, a GitHub Actions job opens the
[Weave Phone Analytics page](https://app.getweave.com/analytics/phone/main)
in a headless browser, sets the Time Period to **Yesterday**, and appends
yesterday's numbers to the
[Weave Call Analytics spreadsheet](https://docs.google.com/spreadsheets/d/1hc-ELFp0W1F8W5IuUeOosNJpcWwydjC0_aJQ4UYwswI/edit):

- **Daily Summary** tab — one row: Date, Total Calls, Incoming, Outgoing,
  Answered, Missed, Abandoned, Answer Rate %, Avg Call Duration,
  Long Duration Calls (columns A–J).
- **By Line** tab — one row: Date in column A, then each line's
  *Calls Answered* under its matching header column. Lines are matched by
  extension number (e.g. Weave's "Phone 101" → the header containing 101),
  so Weave's random ordering doesn't matter. Lines with no calls get 0.

Built-in safety checks:

- **Sum check** — if the By Line counts don't add up to the Daily Summary
  "Answered" number, the rows are still written but the run fails so you get
  a GitHub notification email.
- **New line check** — if Weave reports a line that has no matching header,
  the By Line row is **not** written (no guessing). The run fails with a
  message naming the new line; add a header column to the By Line tab and
  re-run the workflow.
- **Idempotent** — if a row for yesterday already exists, it's skipped, so
  re-runs and duplicate schedule fires never create duplicates.

---

## One-time setup

### 1. Google service account (writes to the sheet)

1. In [Google Cloud Console](https://console.cloud.google.com/), create (or
   pick) a project → **APIs & Services → Library** → enable **Google Sheets API**.
2. **IAM & Admin → Service Accounts → Create service account** (any name,
   no roles needed) → open it → **Keys → Add key → JSON**. A `.json` file downloads.
3. Open the spreadsheet → **Share** → add the service account's email
   (`...@...iam.gserviceaccount.com`) as **Editor**.

### 2. GitHub Actions secrets

In this repo: **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Entire contents of the downloaded JSON key file |
| `WEAVE_EMAIL` | Weave login email |
| `WEAVE_PASSWORD` | Weave login password |
| `WEAVE_STORAGE_STATE_B64` | *(only needed if Weave asks for MFA — see below)* |

> ⚠️ Keep this repository **private**. The workflow caches the Weave browser
> session between runs, and secrets/cache must not live in a public repo.

### 3. If Weave login uses MFA / a verification code

Unattended runs can't answer a verification code. Capture a session once on
your own computer:

```bash
pip install playwright
playwright install chromium
python scripts/bootstrap_auth.py
```

Log in in the window that opens (complete the code prompt), press Enter in
the terminal, and paste the printed base64 line into the
`WEAVE_STORAGE_STATE_B64` secret. The workflow refreshes and re-caches the
session on every successful run, so this normally only needs doing again if
runs fail with a login error after a long gap.

### 4. First run

**Actions → Daily Weave call report → Run workflow** (check *dry run* the
first time to scrape-and-log without writing). When the dry run looks right,
run it again without dry run. After that, the 6 AM PT schedule takes over.

---

## How you're notified

- **Success** — the run's *Summary* page shows what was appended.
- **Sum mismatch or unknown line** — the run fails → GitHub emails you
  (Settings → Notifications → Actions must be enabled, which is the default
  for workflow failures on your own repos).
- **Scrape/login failure** — the run fails and uploads a `weave-debug-*`
  artifact (screenshot, page HTML, captured analytics JSON) to diagnose.

If a day's run fails, fix the cause and **Run workflow** again *the same
day* — Weave's "Yesterday" preset still points at the right day. Older gaps
need manual entry (the scraper doesn't drive Weave's custom date picker).

## Scheduling details

GitHub cron runs in UTC and ignores daylight saving, so the workflow is
scheduled at both 13:05 and 14:05 UTC; a guard step keeps whichever lands at
6–7 AM Pacific and skips the other. Idempotency makes an accidental double
run harmless. GitHub may delay scheduled runs by a few minutes.

## Local development

```bash
pip install -r requirements-dev.txt
playwright install chromium
python -m pytest                     # offline + mock-browser tests
python scripts/bootstrap_auth.py     # capture a Weave session locally
python -m weave_bot.main --dry-run --headed   # watch it scrape, write nothing
```

Configuration is environment-driven (see `weave_bot/config.py`):
`SPREADSHEET_ID`, `DAILY_SUMMARY_TAB`, `BY_LINE_TAB`, `REPORT_TIMEZONE`,
`STRICT_VALIDATION` (set `true` to block writes on a sum mismatch instead of
writing + notifying), `HEADLESS`, `DEBUG_DIR`.

## If Weave changes its page layout

The scraper anchors on visible text ("Total Calls", "Device Extension", …)
rather than CSS classes, so cosmetic redesigns usually don't break it. If a
run does fail with a "layout may have changed" error, download the debug
artifact and adjust `METRIC_LABELS` / the extraction JS in
`weave_bot/scraper.py`. Every run also captures the analytics JSON responses
the page itself loads (`debug/network/`) — if scraping ever becomes a
maintenance burden, those payloads are the map for switching to Weave's
internal API directly.
