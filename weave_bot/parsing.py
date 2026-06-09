"""Pure parsing/mapping logic. No network or browser dependencies, so all of
this is covered by offline unit tests in tests/."""

from __future__ import annotations

import re
from datetime import date, datetime

INT_RE = re.compile(r"^-?\d[\d,]*$")
PERCENT_RE = re.compile(r"^-?\d+(?:\.\d+)?\s*%$")
# Durations as Weave may render them: "2m 34s", "1h 2m", "45s", "2:34", "1:02:03"
DURATION_RE = re.compile(
    r"^(?:\d+\s*h(?:rs?)?\s*)?\d+\s*m(?:in)?(?:\s*\d+\s*s(?:ec)?)?$"
    r"|^\d+\s*s(?:ec)?$"
    r"|^\d{1,3}:\d{2}(?::\d{2})?$",
    re.IGNORECASE,
)
# Tokenizer used against a metric card's text: durations first (so "2m 34s"
# isn't split), then percents, then plain numbers.
VALUE_TOKEN_RE = re.compile(
    r"(?:\d+\s*h(?:rs?)?\s*)?\d+\s*m(?:in)?\s*\d+\s*s(?:ec)?"
    r"|\d+\s*h(?:rs?)?\s*\d+\s*m(?:in)?"
    r"|\d{1,3}:\d{2}(?::\d{2})?"
    r"|\d+\s*s(?:ec)?\b"
    r"|-?\d+(?:\.\d+)?\s*%"
    r"|-?\d[\d,]*",
    re.IGNORECASE,
)

_EXTENSION_RE = re.compile(r"(?<!\d)(\d{2,5})(?!\d)")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def parse_int(token: str) -> int | None:
    token = (token or "").strip().replace(",", "")
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    return None


def tokenize_values(text: str) -> list[str]:
    return [t.strip() for t in VALUE_TOKEN_RE.findall(text or "")]


def pick_int(tokens: list[str]) -> int | None:
    for t in tokens:
        if INT_RE.fullmatch(t.strip()):
            return parse_int(t)
    return None


def pick_percent(tokens: list[str]) -> str | None:
    for t in tokens:
        if PERCENT_RE.fullmatch(t.strip()):
            return re.sub(r"\s+", "", t.strip())
    return None


def pick_duration(tokens: list[str]) -> str | None:
    for t in tokens:
        if DURATION_RE.fullmatch(t.strip()):
            return re.sub(r"\s+", " ", t.strip())
    return None


def extract_extension(name: str) -> str | None:
    """Pull the line/extension number out of a label like 'Phone 101' or
    "120 Walter's Softphone". Returns None when no standalone number exists."""
    m = _EXTENSION_RE.search(name or "")
    return m.group(1) if m else None


def format_report_date(d: date) -> str:
    """M/D/YYYY without leading zeros (e.g. 6/8/2026), matching how Google
    Sheets displays dates by default in the US locale."""
    return f"{d.month}/{d.day}/{d.year}"


_SHEET_DATE_FORMATS = (
    "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%b-%Y", "%b %d, %Y", "%B %d, %Y",
    "%a, %b %d, %Y", "%m-%d-%Y",
)


def parse_sheet_date(cell: str) -> date | None:
    """Best-effort parse of a date cell as returned by the Sheets API."""
    cell = (cell or "").strip()
    if not cell:
        return None
    for fmt in _SHEET_DATE_FORMATS:
        try:
            return datetime.strptime(cell, fmt).date()
        except ValueError:
            continue
    return None


def map_lines_to_headers(
    headers: list[str], line_counts: dict[str, int]
) -> tuple[list[int], list[str]]:
    """Map Weave line names -> By Line header columns.

    headers: the full header row of the By Line tab (headers[0] is the Date
    column). Matching is by extension number first ('Phone 101' matches a
    header containing 101), falling back to a unique case-insensitive name
    containment match for lines without a number.

    Returns (values, unknown_lines) where values has one entry per header
    after the date column (0 when a line had no answered calls), and
    unknown_lines are Weave lines that matched no header — the caller must
    flag these instead of guessing.
    """
    data_headers = headers[1:]
    ext_to_col: dict[str, int] = {}
    for idx, h in enumerate(data_headers):
        ext = extract_extension(h)
        if ext is not None:
            if ext in ext_to_col:
                raise ValueError(
                    f"By Line headers are ambiguous: extension {ext} appears in "
                    f"both '{data_headers[ext_to_col[ext]]}' and '{h}'"
                )
            ext_to_col[ext] = idx

    values = [0] * len(data_headers)
    unknown: list[str] = []

    for name, count in line_counts.items():
        col = None
        ext = extract_extension(name)
        if ext is not None and ext in ext_to_col:
            col = ext_to_col[ext]
        else:
            norm_name = normalize(name)
            matches = [
                i for i, h in enumerate(data_headers)
                if norm_name and (norm_name in normalize(h) or normalize(h) in norm_name)
            ]
            if len(matches) == 1:
                col = matches[0]
        if col is None:
            unknown.append(name)
        else:
            values[col] += count

    return values, unknown
