"""Google Sheets access.

Two credential options, checked in this order:

1. GOOGLE_OAUTH_TOKEN_B64 — an OAuth "authorized user" token for a real
   Google account (produced by scripts/bootstrap_google_oauth.py). Use this
   when org policy blocks service account keys.
2. GOOGLE_SERVICE_ACCOUNT_JSON — a service account JSON key; the sheet must
   be shared with the service account's email as Editor.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import date

import gspread
from google.oauth2.credentials import Credentials as UserCredentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials

from .config import Config
from .parsing import parse_sheet_date

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _decode_json_env(raw: str) -> dict:
    raw = raw.strip()
    if not raw.startswith("{"):
        raw = base64.b64decode(raw).decode("utf-8")
    return json.loads(raw)


def _load_credentials() -> tuple[object, str]:
    """Returns (credentials, identity_hint_for_error_messages)."""
    oauth_raw = os.environ.get("GOOGLE_OAUTH_TOKEN_B64", "").strip()
    if oauth_raw:
        info = _decode_json_env(oauth_raw)
        creds = UserCredentials.from_authorized_user_info(info, scopes=SCOPES)
        return creds, info.get("account") or "the Google account you authorized"

    sa_raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if sa_raw:
        info = _decode_json_env(sa_raw)
        creds = ServiceAccountCredentials.from_service_account_info(info, scopes=SCOPES)
        return creds, info.get("client_email", "the service account")

    raise RuntimeError(
        "No Google credentials configured. Set GOOGLE_OAUTH_TOKEN_B64 (run "
        "scripts/bootstrap_google_oauth.py to generate it) or "
        "GOOGLE_SERVICE_ACCOUNT_JSON."
    )


def open_spreadsheet(cfg: Config) -> gspread.Spreadsheet:
    creds, identity = _load_credentials()
    client = gspread.authorize(creds)
    try:
        return client.open_by_key(cfg.spreadsheet_id)
    except gspread.exceptions.APIError as exc:
        raise RuntimeError(
            f"Could not open spreadsheet {cfg.spreadsheet_id}. Make sure {identity} "
            "has edit access to the sheet, and that the token/key is still valid. "
            f"Underlying error: {exc}"
        ) from exc


def find_date_row(ws: gspread.Worksheet, target: date) -> int | None:
    """Return the 1-based row index whose column A parses to `target`."""
    for idx, cell in enumerate(ws.col_values(1), start=1):
        if parse_sheet_date(cell) == target:
            return idx
    return None


def append_row(ws: gspread.Worksheet, values: list) -> None:
    ws.append_row(values, value_input_option="USER_ENTERED", table_range="A1")


def get_headers(ws: gspread.Worksheet) -> list[str]:
    return ws.row_values(1)
