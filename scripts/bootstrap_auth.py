"""One-time local helper: log in to Weave by hand (including any MFA prompt),
then save the browser session for the unattended cloud runs.

Usage (on your own computer, not in CI):
    pip install playwright
    playwright install chromium
    python scripts/bootstrap_auth.py

A browser window opens on the Weave sign-in page. Complete the login —
password, verification code, anything it asks. When you can see the Phone
Analytics dashboard, come back to the terminal and press Enter. The script
saves .auth/storage_state.json and prints a base64 string: store that string
as the WEAVE_STORAGE_STATE_B64 secret in the GitHub repository
(Settings → Secrets and variables → Actions).
"""

import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

ANALYTICS_URL = "https://app.getweave.com/analytics/phone/main"
STATE_FILE = Path(".auth/storage_state.json")


def main() -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1500, "height": 950})
        page = context.new_page()
        page.goto(ANALYTICS_URL)
        print()
        print("A browser window is open. Log in to Weave (complete MFA if asked)")
        print("until you can see the Phone Analytics page.")
        input("Then press Enter here to save the session... ")
        context.storage_state(path=str(STATE_FILE))
        browser.close()

    b64 = base64.b64encode(STATE_FILE.read_bytes()).decode("ascii")
    print()
    print(f"Session saved to {STATE_FILE}")
    print("Add/update this GitHub Actions secret (Settings → Secrets and variables → Actions):")
    print()
    print("  Name:  WEAVE_STORAGE_STATE_B64")
    print("  Value: (the single line below)")
    print()
    print(b64)


if __name__ == "__main__":
    main()
