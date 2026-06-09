"""Google Sheets access via a service account (share the spreadsheet with the
service account's email as Editor)."""

from __future__ import annotations

import base64
import json
import os
from datetime import date

import gspread
from google.oauth2.service_account import Credentials

from .config import Config
from .parsing import parse_sheet_date

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def open_spreadsheet(cfg: Config) -> gspread.Spreadsheet:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not set. Paste the service account's "
            "JSON key (raw or base64) into that secret/env var."
        )
    if not raw.startswith("{"):
        raw = base64.b64decode(raw).decode("utf-8")
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    client = gspread.authorize(creds)
    try:
        return client.open_by_key(cfg.spreadsheet_id)
    except gspread.exceptions.APIError as exc:
        raise RuntimeError(
            f"Could not open spreadsheet {cfg.spreadsheet_id}. Make sure the sheet "
            f"is shared (Editor) with {info.get('client_email', 'the service account')}."
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
