"""Regression test for the DOM-extraction JavaScript, run against a mock page
that mimics the Weave phone analytics layout. Requires Playwright + Chromium
(skipped automatically when they aren't installed):

    pip install -r requirements-dev.txt && playwright install chromium
"""

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from weave_bot.scraper import _BY_LINE_JS, _METRIC_JS, METRIC_LABELS, resolve_metric_values

MOCK_PAGE = """
<html><body>
<header><h1>Phone Analytics</h1><button>Yesterday</button></header>
<main>
  <div class="cards">
    <div class="card"><span class="label">Total Calls</span><span class="v">142</span><span class="trend">+12%</span></div>
    <div class="card"><span class="label">Incoming</span><div class="v">96</div></div>
    <div class="card"><span class="label">Outgoing</span><div class="v">46</div></div>
    <div class="card"><span class="label">Answered</span><div class="v">78</div></div>
    <div class="card"><span class="label">Missed</span><div class="v">14</div></div>
    <div class="card"><span class="label">Abandoned</span><div class="v">4</div></div>
    <div class="card"><span class="label">Answer Rate</span><div class="v">81%</div></div>
    <div class="card"><span class="label">Avg Call Duration</span><div class="v">2m 34s</div></div>
    <div class="card"><span class="label">Long Duration Calls</span><div class="v">6</div></div>
  </div>
  <section>
    <h3>Device Extension</h3>
    <table>
      <thead><tr><th>Device Extension</th><th>Calls Answered</th><th>Calls Missed</th></tr></thead>
      <tbody>
        <tr><td>102</td><td>11</td><td>2</td></tr>
        <tr><td>Phone 101</td><td>23</td><td>1</td></tr>
        <tr><td>120 Walter's Softphone</td><td>3</td><td>0</td></tr>
        <tr><td>105</td><td>41</td><td>4</td></tr>
      </tbody>
    </table>
  </section>
</main>
</body></html>
"""


@pytest.fixture(scope="module")
def page():
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:  # browser not installed
            pytest.skip(f"Chromium unavailable: {exc}")
        pg = browser.new_page()
        pg.set_content(MOCK_PAGE)
        yield pg
        browser.close()


def test_metric_extraction(page):
    raw = page.evaluate(_METRIC_JS, METRIC_LABELS)
    values, missing = resolve_metric_values(raw)
    assert missing == []
    assert values == {
        "total_calls": 142,
        "incoming": 96,
        "outgoing": 46,
        "answered": 78,
        "missed": 14,
        "abandoned": 4,
        "answer_rate": "81%",
        "avg_call_duration": "2m 34s",
        "long_duration_calls": 6,
    }


def test_by_line_extraction(page):
    result = page.evaluate(_BY_LINE_JS)
    assert result["source"] == "device-extension-section"
    got = {r["name"]: r["value"] for r in result["rows"]}
    assert got == {
        "102": "11",
        "Phone 101": "23",
        "120 Walter's Softphone": "3",
        "105": "41",
    }
