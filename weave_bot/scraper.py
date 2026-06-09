"""Playwright scraper for the Weave phone analytics page.

Strategy: log in (or reuse a saved session), set Time Period to "Yesterday",
then read metrics with text-anchored DOM extraction — find the element whose
text equals the metric label, walk up to its card, and pull the value out of
the card's text. This survives styling/class-name changes far better than CSS
selectors. Every analytics-looking JSON response is also captured to the debug
directory so selector drift can be diagnosed (and the scraper eventually moved
onto Weave's internal API) without re-running interactively.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

from .config import Config
from .parsing import normalize, parse_int, pick_duration, pick_int, pick_percent, tokenize_values


class WeaveScrapeError(Exception):
    """Raised with a human-readable message; debug artifacts hold the details."""


@dataclass
class PhoneAnalytics:
    total_calls: int
    incoming: int
    outgoing: int
    answered: int
    missed: int
    abandoned: int
    answer_rate: str
    avg_call_duration: str
    long_duration_calls: int
    by_line: dict[str, int] = field(default_factory=dict)


# Label variants seen across Weave UI iterations; first match wins.
METRIC_LABELS: dict[str, list[str]] = {
    "total_calls": ["Total Calls", "Total Call Volume", "Calls"],
    "incoming": ["Incoming", "Incoming Calls", "Inbound", "Inbound Calls"],
    "outgoing": ["Outgoing", "Outgoing Calls", "Outbound", "Outbound Calls"],
    "answered": ["Answered", "Answered Calls", "Calls Answered"],
    "missed": ["Missed", "Missed Calls", "Calls Missed"],
    "abandoned": ["Abandoned", "Abandoned Calls"],
    "answer_rate": ["Answer Rate", "Answer Rate %", "Answered Rate"],
    "avg_call_duration": ["Avg Call Duration", "Average Call Duration", "Avg. Call Duration", "Avg Duration"],
    "long_duration_calls": ["Long Duration Calls", "Long Calls", "Long Duration"],
}

INT_METRICS = ("total_calls", "incoming", "outgoing", "answered", "missed", "abandoned", "long_duration_calls")

_ANALYTICS_URL_HINT = re.compile(r"analytic|phone|call|report|metric|aggregate", re.I)


def scrape_yesterday(cfg: Config) -> PhoneAnalytics:
    cfg.debug_dir.mkdir(parents=True, exist_ok=True)
    captured: list[tuple[str, bytes]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=cfg.headless)
        context_kwargs = {"viewport": {"width": 1600, "height": 1000}}
        if cfg.storage_state_file.exists():
            context_kwargs["storage_state"] = str(cfg.storage_state_file)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.set_default_timeout(30_000)

        def on_response(resp):
            try:
                ctype = resp.headers.get("content-type", "")
                if "json" in ctype and _ANALYTICS_URL_HINT.search(resp.url):
                    body = resp.body()
                    if body and len(body) < 5_000_000:
                        captured.append((resp.url, body))
            except PlaywrightError:
                pass

        page.on("response", on_response)

        try:
            page.goto(cfg.analytics_url, wait_until="domcontentloaded", timeout=60_000)
            _login_if_needed(page, cfg)
            _wait_for_analytics(page, cfg)
            _select_yesterday(page)
            _settle(page)
            data = _extract(page, cfg)

            # Refresh the saved session so the workflow cache keeps it alive.
            cfg.storage_state_file.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(cfg.storage_state_file))
            return data
        except Exception:
            _dump_debug(page, cfg, captured, suffix="failure")
            raise
        finally:
            _save_captured(cfg, captured)
            try:
                _dump_debug(page, cfg, [], suffix="final")
            except PlaywrightError:
                pass
            context.close()
            browser.close()


# --- login -----------------------------------------------------------------

_LOGIN_URL_HINT = re.compile(r"sign[-_]?in|log[-_]?in|auth|sso|session", re.I)
_MFA_HINT = re.compile(r"verification code|one[- ]time|two[- ]factor|2fa|authenticator|texted you|emailed you a code", re.I)


def _looks_like_login(page: Page) -> bool:
    if _LOGIN_URL_HINT.search(page.url):
        return True
    try:
        return page.locator("input[type='password'], input[type='email'], input[name='username']").count() > 0
    except PlaywrightError:
        return False


def _login_if_needed(page: Page, cfg: Config) -> None:
    _settle(page)
    if not _looks_like_login(page):
        return
    if not cfg.weave_email or not cfg.weave_password:
        raise WeaveScrapeError(
            "Weave requires login but WEAVE_EMAIL / WEAVE_PASSWORD are not set, "
            "and the saved session (WEAVE_STORAGE_STATE_B64 / cached storage state) "
            "is missing or expired. Re-run scripts/bootstrap_auth.py and update the secret."
        )

    # Weave's sign-in may be single-page or a two-step (email, then password) flow.
    email_box = page.locator("input[type='email'], input[name='username'], input[name='email'], input[autocomplete='username']").first
    email_box.wait_for(state="visible", timeout=20_000)
    email_box.fill(cfg.weave_email)

    pwd = page.locator("input[type='password']").first
    if not _is_visible(pwd):
        _click_first(page, ["Continue", "Next", "Sign in", "Log in", "Submit"])
        pwd = page.locator("input[type='password']").first
        pwd.wait_for(state="visible", timeout=20_000)
    pwd.fill(cfg.weave_password)
    _click_first(page, ["Sign in", "Log in", "Continue", "Submit", "Next"])

    try:
        page.wait_for_url(re.compile(r"app\.getweave\.com"), timeout=45_000)
    except PlaywrightTimeout:
        pass
    _settle(page)

    body_text = _body_text(page)
    if _MFA_HINT.search(body_text):
        raise WeaveScrapeError(
            "Weave is asking for a verification code (MFA), which can't be answered "
            "in an unattended run. Run scripts/bootstrap_auth.py locally to complete "
            "MFA once, then store the printed value as the WEAVE_STORAGE_STATE_B64 secret."
        )
    if _looks_like_login(page):
        raise WeaveScrapeError(
            "Login did not succeed (still on a sign-in page). Check WEAVE_EMAIL / "
            "WEAVE_PASSWORD, or refresh the saved session with scripts/bootstrap_auth.py. "
            "See the debug artifacts (screenshot + HTML) from this run."
        )


def _wait_for_analytics(page: Page, cfg: Config) -> None:
    if "analytics" not in page.url:
        page.goto(cfg.analytics_url, wait_until="domcontentloaded", timeout=60_000)
    _settle(page)
    try:
        page.get_by_text(re.compile(r"total calls", re.I)).first.wait_for(timeout=45_000)
    except PlaywrightTimeout as exc:
        raise WeaveScrapeError(
            "The phone analytics page loaded but 'Total Calls' never appeared. "
            "The page layout may have changed, or the account lacks analytics access. "
            "See the debug artifacts (screenshot + HTML + captured JSON)."
        ) from exc


# --- time period -----------------------------------------------------------

_PERIOD_VALUE_RE = re.compile(
    r"^(today|yesterday|last\s+\d+\s+days?|this\s+(week|month|year)|last\s+(week|month|year)|custom(\s+range)?)$",
    re.I,
)


def _select_yesterday(page: Page) -> None:
    """Open the Time Period control and choose 'Yesterday'. Several locator
    strategies are tried because the control has changed across Weave releases."""
    openers = [
        lambda: page.get_by_role("button", name=re.compile(r"time period", re.I)).first,
        lambda: page.get_by_label(re.compile(r"time period", re.I)).first,
        lambda: page.get_by_role("combobox").first,
        lambda: page.get_by_role("button", name=_PERIOD_VALUE_RE).first,
        lambda: page.get_by_text(_PERIOD_VALUE_RE).first,
    ]
    last_err: Exception | None = None
    for opener in openers:
        try:
            ctl = opener()
            ctl.wait_for(state="visible", timeout=4_000)
            # Already on Yesterday?
            if normalize(ctl.inner_text() or "") == "yesterday":
                return
            ctl.click(timeout=4_000)
        except (PlaywrightError, PlaywrightTimeout) as exc:
            last_err = exc
            continue

        option_locators = [
            lambda: page.get_by_role("option", name=re.compile(r"^yesterday$", re.I)).first,
            lambda: page.get_by_role("menuitem", name=re.compile(r"^yesterday$", re.I)).first,
            lambda: page.get_by_text(re.compile(r"^\s*yesterday\s*$", re.I)).first,
        ]
        for opt in option_locators:
            try:
                o = opt()
                o.wait_for(state="visible", timeout=4_000)
                o.click(timeout=4_000)
                _settle(page)
                return
            except (PlaywrightError, PlaywrightTimeout) as exc:
                last_err = exc
                continue
        # The click opened something that wasn't the period menu; close and retry.
        try:
            page.keyboard.press("Escape")
        except PlaywrightError:
            pass

    raise WeaveScrapeError(
        "Could not set the Time Period to 'Yesterday' — the dropdown was not found "
        "with any known locator. See the debug screenshot/HTML to identify the new "
        f"control. Last underlying error: {last_err}"
    )


# --- extraction ------------------------------------------------------------

_METRIC_JS = """
(labelMap) => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const leaves = [...document.querySelectorAll('body *')].filter(
    (el) => el.childElementCount === 0 && norm(el.textContent).length > 0
  );
  const out = {};
  for (const [key, variants] of Object.entries(labelMap)) {
    for (const v of variants) {
      const target = norm(v);
      const anchors = leaves.filter((el) => norm(el.textContent) === target);
      for (const anchor of anchors) {
        let cur = anchor;
        for (let depth = 0; depth < 7 && cur; depth++, cur = cur.parentElement) {
          const text = (cur.innerText || '').replace(/\\s+/g, ' ');
          // Strip the label itself so we don't match digits inside it.
          const rest = text.toLowerCase().split(target).join(' ');
          if (/\\d/.test(rest)) {
            out[key] = { card_text: text.slice(0, 400) };
            break;
          }
        }
        if (out[key]) break;
      }
      if (out[key]) break;
    }
  }
  return out;
}
"""

_BY_LINE_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const result = { rows: [], source: null };

  const parseTable = (table) => {
    const headRow = table.querySelector('thead tr') || table.querySelector('tr');
    if (!headRow) return null;
    const headers = [...headRow.querySelectorAll('th,td')].map((c) => norm(c.innerText));
    let valueIdx = headers.findIndex((h) => /answered/i.test(h));
    if (valueIdx < 0 && headers.length === 2) valueIdx = 1;
    if (valueIdx < 0) return null;
    const rows = [];
    const bodyRows = table.querySelectorAll('tbody tr');
    const trs = bodyRows.length ? bodyRows : table.querySelectorAll('tr');
    for (const tr of trs) {
      if (tr === headRow) continue;
      const cells = [...tr.querySelectorAll('th,td')].map((c) => norm(c.innerText));
      if (cells.length > valueIdx && cells[0]) {
        rows.push({ name: cells[0], value: cells[valueIdx] });
      }
    }
    return rows.length ? { headers, rows } : null;
  };

  // Preferred: a table inside the section headed "Device Extension".
  const anchors = [...document.querySelectorAll('body *')].filter(
    (el) => el.childElementCount === 0 && /device\\s*extension/i.test(el.textContent)
  );
  for (const anchor of anchors) {
    let cur = anchor;
    for (let depth = 0; depth < 10 && cur; depth++, cur = cur.parentElement) {
      for (const table of cur.querySelectorAll('table')) {
        const parsed = parseTable(table);
        if (parsed) return { rows: parsed.rows, source: 'device-extension-section' };
      }
    }
  }
  // Fallback: any table that has a "Calls Answered"-ish column.
  for (const table of document.querySelectorAll('table')) {
    const parsed = parseTable(table);
    if (parsed && parsed.headers.some((h) => /extension|line|device/i.test(h))) {
      return { rows: parsed.rows, source: 'fallback-table' };
    }
  }
  return result;
}
"""


def resolve_metric_values(raw: dict) -> tuple[dict, list[str]]:
    """Turn the card texts found by _METRIC_JS into typed values.
    Returns (values, missing_metric_labels). Pure — unit-testable."""
    values: dict[str, object] = {}
    missing: list[str] = []
    for key, variants in METRIC_LABELS.items():
        card = raw.get(key)
        if not card:
            missing.append(variants[0])
            continue
        text = card["card_text"]
        # Remove all known labels from the card text before tokenizing, so
        # digits inside other labels/subtitles in the same card don't leak in.
        cleaned = text
        for vs in METRIC_LABELS.values():
            for v in vs:
                cleaned = re.sub(re.escape(v), " ", cleaned, flags=re.I)
        tokens = tokenize_values(cleaned)
        if key == "answer_rate":
            values[key] = pick_percent(tokens) or pick_percent(tokenize_values(text))
        elif key == "avg_call_duration":
            values[key] = pick_duration(tokens) or pick_duration(tokenize_values(text))
        else:
            values[key] = pick_int(tokens)
        if values.get(key) is None:
            missing.append(variants[0])
    return values, missing


def _extract(page: Page, cfg: Config) -> PhoneAnalytics:
    raw = page.evaluate(_METRIC_JS, METRIC_LABELS)
    (cfg.debug_dir / "metric_cards.json").write_text(json.dumps(raw, indent=2))

    values, missing = resolve_metric_values(raw)
    if missing:
        raise WeaveScrapeError(
            "Could not read these metrics from the analytics page: "
            f"{', '.join(missing)}. The page layout may have changed — inspect "
            "debug/metric_cards.json, debug/page.html and the screenshot, then "
            "update METRIC_LABELS or the extraction logic in weave_bot/scraper.py."
        )

    by_line_raw = page.evaluate(_BY_LINE_JS)
    (cfg.debug_dir / "by_line.json").write_text(json.dumps(by_line_raw, indent=2))
    rows = by_line_raw.get("rows") or []
    if not rows:
        raise WeaveScrapeError(
            "Could not find the Device Extension → Calls Answered table. "
            "Inspect debug/by_line.json, debug/page.html and the screenshot, "
            "then update the table extraction in weave_bot/scraper.py."
        )
    by_line: dict[str, int] = {}
    for r in rows:
        n = parse_int(str(r["value"]))
        if n is not None:
            by_line[r["name"]] = by_line.get(r["name"], 0) + n

    return PhoneAnalytics(
        total_calls=values["total_calls"],
        incoming=values["incoming"],
        outgoing=values["outgoing"],
        answered=values["answered"],
        missed=values["missed"],
        abandoned=values["abandoned"],
        answer_rate=values["answer_rate"],
        avg_call_duration=values["avg_call_duration"],
        long_duration_calls=values["long_duration_calls"],
        by_line=by_line,
    )


# --- helpers ---------------------------------------------------------------

def _settle(page: Page, quiet_ms: int = 1500) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeout:
        pass
    time.sleep(quiet_ms / 1000)


def _is_visible(locator) -> bool:
    try:
        return locator.is_visible(timeout=2_000)
    except (PlaywrightError, PlaywrightTimeout):
        return False


def _click_first(page: Page, button_names: list[str]) -> None:
    for name in button_names:
        btn = page.get_by_role("button", name=re.compile(rf"^{re.escape(name)}$", re.I)).first
        if _is_visible(btn):
            btn.click()
            _settle(page, quiet_ms=800)
            return


def _body_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=5_000)
    except (PlaywrightError, PlaywrightTimeout):
        return ""


def _dump_debug(page: Page, cfg: Config, captured: list[tuple[str, bytes]], suffix: str) -> None:
    try:
        page.screenshot(path=str(cfg.debug_dir / f"screenshot_{suffix}.png"), full_page=True)
    except PlaywrightError:
        pass
    try:
        (cfg.debug_dir / f"page_{suffix}.html").write_text(page.content())
    except PlaywrightError:
        pass


def _save_captured(cfg: Config, captured: list[tuple[str, bytes]]) -> None:
    if not captured:
        return
    net_dir = cfg.debug_dir / "network"
    net_dir.mkdir(parents=True, exist_ok=True)
    index = []
    for i, (url, body) in enumerate(captured):
        fname = f"{i:03d}.json"
        (net_dir / fname).write_bytes(body)
        index.append({"file": fname, "url": url})
    (net_dir / "index.json").write_text(json.dumps(index, indent=2))
