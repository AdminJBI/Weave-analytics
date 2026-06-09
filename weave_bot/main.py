"""Orchestrator: scrape Weave for yesterday and append to the Google Sheet.

Exit codes:
  0  success (or nothing to do — rows already present)
  1  hard failure (login, scraping, or Sheets access)
  2  data written, but the By Line sum didn't match Answered (needs review)
  3  Daily Summary written, By Line skipped because Weave reported a line
     that has no matching header column (add the header, then re-run)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .parsing import format_report_date, map_lines_to_headers
from .scraper import PhoneAnalytics, WeaveScrapeError, scrape_yesterday
from . import sheets


def yesterday_in(tz_name: str) -> date:
    now = datetime.now(ZoneInfo(tz_name))
    return (now - timedelta(days=1)).date()


def log(msg: str) -> None:
    print(msg, flush=True)


def step_summary(lines: list[str]) -> None:
    """Append to the GitHub Actions job summary when running in CI."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def build_summary_row(report_date: date, d: PhoneAnalytics) -> list:
    return [
        format_report_date(report_date),
        d.total_calls,
        d.incoming,
        d.outgoing,
        d.answered,
        d.missed,
        d.abandoned,
        d.answer_rate,
        d.avg_call_duration,
        d.long_duration_calls,
    ]


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Scrape and print, but don't write to the sheet")
    ap.add_argument("--date", help="Report date YYYY-MM-DD (default: yesterday in PT). "
                                   "Only yesterday can actually be scraped; this exists for testing.")
    ap.add_argument("--headed", action="store_true", help="Run the browser with a visible window")
    args = ap.parse_args(argv)

    cfg = Config.from_env()
    if args.headed:
        cfg.headless = False

    expected = yesterday_in(cfg.timezone)
    report_date = date.fromisoformat(args.date) if args.date else expected
    if report_date != expected:
        log(f"WARNING: requested date {report_date} is not yesterday ({expected}). "
            "The scraper uses Weave's 'Yesterday' preset, so the scraped numbers "
            "will be for yesterday regardless. Proceeding for testing purposes only.")

    log(f"Report date: {report_date} ({cfg.timezone})")

    # Open the spreadsheet first: fail fast on credential problems, and check
    # idempotency before paying for a browser session.
    summary_ws = byline_ws = None
    summary_exists = byline_exists = False
    if not args.dry_run:
        ss = sheets.open_spreadsheet(cfg)
        summary_ws = ss.worksheet(cfg.daily_summary_tab)
        byline_ws = ss.worksheet(cfg.by_line_tab)
        summary_exists = sheets.find_date_row(summary_ws, report_date) is not None
        byline_exists = sheets.find_date_row(byline_ws, report_date) is not None
        if summary_exists and byline_exists:
            log(f"Rows for {report_date} already exist in both tabs — nothing to do.")
            step_summary([f"✅ Weave report for **{report_date}** was already recorded. No changes made."])
            return 0

    log("Scraping Weave phone analytics (Time Period = Yesterday)...")
    data = scrape_yesterday(cfg)
    log(f"Scraped: total={data.total_calls} in={data.incoming} out={data.outgoing} "
        f"answered={data.answered} missed={data.missed} abandoned={data.abandoned} "
        f"rate={data.answer_rate} avg={data.avg_call_duration} long={data.long_duration_calls}")
    log(f"By line: {data.by_line}")

    # Cross-checks.
    warnings: list[str] = []
    if data.incoming + data.outgoing != data.total_calls:
        warnings.append(
            f"Incoming ({data.incoming}) + Outgoing ({data.outgoing}) != Total Calls "
            f"({data.total_calls}) — double-check the scraped values."
        )
    by_line_sum = sum(data.by_line.values())
    mismatch = by_line_sum != data.answered
    if mismatch:
        warnings.append(
            f"By Line answered-call counts sum to {by_line_sum}, but the Daily Summary "
            f"'Answered' metric is {data.answered}. Verify against the Weave page."
        )

    if args.dry_run:
        log("Dry run — nothing written.")
        for w in warnings:
            log(f"WARNING: {w}")
        return 0

    if mismatch and cfg.strict_validation:
        log("STRICT_VALIDATION is on — not writing anything.")
        for w in warnings:
            log(f"ERROR: {w}")
        step_summary([f"❌ Weave report for **{report_date}** NOT written (strict validation):", ""]
                     + [f"- {w}" for w in warnings])
        return 2

    md = [f"## Weave call report — {report_date}", ""]

    if summary_exists:
        log("Daily Summary row already exists — skipping that tab.")
        md.append("- Daily Summary: already present, skipped")
    else:
        sheets.append_row(summary_ws, build_summary_row(report_date, data))
        log("Appended Daily Summary row.")
        md.append(f"- Daily Summary: appended (Total {data.total_calls}, Answered {data.answered}, "
                  f"Missed {data.missed}, Rate {data.answer_rate})")

    unknown: list[str] = []
    if byline_exists:
        log("By Line row already exists — skipping that tab.")
        md.append("- By Line: already present, skipped")
    else:
        headers = sheets.get_headers(byline_ws)
        line_values, unknown = map_lines_to_headers(headers, data.by_line)
        if unknown:
            md.append(f"- By Line: **NOT written** — unrecognized line(s) from Weave: {', '.join(unknown)}")
        else:
            sheets.append_row(byline_ws, [format_report_date(report_date)] + line_values)
            log("Appended By Line row.")
            md.append(f"- By Line: appended ({by_line_sum} answered calls across "
                      f"{sum(1 for v in line_values if v)} lines)")

    if warnings:
        md += ["", "### ⚠️ Warnings", ""] + [f"- {w}" for w in warnings]
    step_summary(md)

    if unknown:
        log(
            "ERROR: Weave reported line(s) with no matching column in the By Line tab: "
            f"{unknown}. Add a header column for each (e.g. '121 New Phone') in row 1 "
            "of the By Line tab, then re-run this workflow — the Daily Summary row is "
            "already saved and will not be duplicated."
        )
        return 3
    if mismatch:
        log("ERROR: " + warnings[-1] + " (Rows were written; this exit code is to notify you.)")
        return 2
    log("Done — all checks passed.")
    return 0


def main() -> None:
    try:
        sys.exit(run())
    except WeaveScrapeError as exc:
        log(f"SCRAPE ERROR: {exc}")
        step_summary(["❌ **Weave scrape failed**", "", f"> {exc}", "",
                      "Debug artifacts (screenshot, page HTML, captured JSON) are attached to this run."])
        sys.exit(1)


if __name__ == "__main__":
    main()
