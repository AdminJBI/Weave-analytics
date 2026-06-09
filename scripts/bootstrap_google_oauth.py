"""One-time local helper: authorize the bot to edit Google Sheets as YOUR
Google account (no service account key needed).

Prerequisite: an OAuth client ID of type "Desktop app" from Google Cloud
Console (APIs & Services → Credentials). See the README for the exact clicks.

Usage (on your own computer, not in CI):
    pip install google-auth-oauthlib
    python scripts/bootstrap_google_oauth.py

It asks for the client ID and client secret, opens a browser for you to sign
in and approve, then prints a base64 line: store it as the
GOOGLE_OAUTH_TOKEN_B64 secret in the GitHub repository
(Settings → Secrets and variables → Actions). The token includes a refresh
token, so the daily job can keep using it indefinitely.
"""

import base64
import json

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def main() -> None:
    print("Paste the values from your 'Desktop app' OAuth client")
    print("(Google Cloud Console → APIs & Services → Credentials):\n")
    client_id = input("  Client ID: ").strip()
    client_secret = input("  Client secret: ").strip()

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        SCOPES,
    )
    creds = flow.run_local_server(port=0, prompt="consent")

    info = json.loads(creds.to_json())
    if not info.get("refresh_token"):
        raise SystemExit(
            "No refresh token was returned. Re-run this script and make sure you "
            "approve the consent screen (it must show the permission prompt)."
        )

    b64 = base64.b64encode(json.dumps(info).encode("utf-8")).decode("ascii")
    print("\nAuthorization successful.")
    print("Add/update this GitHub Actions secret (Settings → Secrets and variables → Actions):")
    print()
    print("  Name:  GOOGLE_OAUTH_TOKEN_B64")
    print("  Value: (the single line below)")
    print()
    print(b64)


if __name__ == "__main__":
    main()
