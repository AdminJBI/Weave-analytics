from datetime import date

import pytest

from weave_bot.parsing import (
    extract_extension,
    format_report_date,
    map_lines_to_headers,
    parse_int,
    parse_sheet_date,
    pick_duration,
    pick_int,
    pick_percent,
    tokenize_values,
)

# The actual header row of the user's "By Line" tab.
BY_LINE_HEADERS = [
    "Date", "Phone 101", "102", "103", "104", "105", "106", "107", "108",
    "112", "113", "120 Walter's Softphone",
]


def test_parse_int():
    assert parse_int("1,234") == 1234
    assert parse_int(" 42 ") == 42
    assert parse_int("87%") is None
    assert parse_int("") is None


def test_extract_extension():
    assert extract_extension("Phone 101") == "101"
    assert extract_extension("120 Walter's Softphone") == "120"
    assert extract_extension("102") == "102"
    assert extract_extension("Front Desk") is None


def test_tokenize_and_pickers_on_card_text():
    # Typical card text after the label is stripped: value + trend badge.
    tokens = tokenize_values(" 142 +12% vs previous ")
    assert pick_int(tokens) == 142
    assert pick_percent(tokens) == "+12%" or pick_percent(tokens) == "12%"

    tokens = tokenize_values(" 87.5% 132 of 152 ")
    assert pick_percent(tokens) == "87.5%"

    assert pick_duration(tokenize_values("2m 34s")) == "2m 34s"
    assert pick_duration(tokenize_values("Avg 2:34 today")) == "2:34"
    assert pick_duration(tokenize_values("1:02:03")) == "1:02:03"
    assert pick_duration(tokenize_values("1h 5m")) == "1h 5m"


def test_duration_not_confused_with_int():
    tokens = tokenize_values("2m 34s")
    # "2m 34s" must come out as one duration token, not loose ints.
    assert pick_int(tokens) is None


def test_format_report_date_no_leading_zeros():
    assert format_report_date(date(2026, 6, 8)) == "6/8/2026"
    assert format_report_date(date(2026, 11, 23)) == "11/23/2026"


@pytest.mark.parametrize(
    "cell",
    ["6/8/2026", "06/08/2026", "2026-06-08", "Jun 08, 2026", "June 08, 2026", "06/08/26"],
)
def test_parse_sheet_date_formats(cell):
    assert parse_sheet_date(cell) == date(2026, 6, 8)


def test_parse_sheet_date_garbage():
    assert parse_sheet_date("Date") is None
    assert parse_sheet_date("") is None


def test_map_lines_happy_path():
    counts = {
        "Phone 101": 12, "102": 5, "103": 0, "105": 7,
        "120 Walter's Softphone": 3,
    }
    values, unknown = map_lines_to_headers(BY_LINE_HEADERS, counts)
    assert unknown == []
    assert values == [12, 5, 0, 0, 7, 0, 0, 0, 0, 0, 3]
    assert len(values) == len(BY_LINE_HEADERS) - 1


def test_map_lines_weave_name_variants():
    # Weave may render names differently from the headers — match by extension.
    counts = {"Ext. 104": 9, "Line 112": 2, "Walter's Softphone (120)": 1}
    values, unknown = map_lines_to_headers(BY_LINE_HEADERS, counts)
    assert unknown == []
    assert values[3] == 9   # 104
    assert values[8] == 2   # 112
    assert values[10] == 1  # 120


def test_map_lines_unknown_line_flagged():
    counts = {"Phone 101": 4, "Phone 999": 6}
    values, unknown = map_lines_to_headers(BY_LINE_HEADERS, counts)
    assert unknown == ["Phone 999"]
    assert values[0] == 4


def test_map_lines_duplicate_extensions_summed():
    counts = {"Phone 101": 4, "101 Front Desk": 2}
    values, unknown = map_lines_to_headers(BY_LINE_HEADERS, counts)
    assert unknown == []
    assert values[0] == 6


def test_map_lines_fuzzy_name_fallback():
    headers = ["Date", "Front Desk", "Phone 101"]
    values, unknown = map_lines_to_headers(headers, {"front desk": 5})
    assert unknown == []
    assert values == [5, 0]


def test_map_lines_ambiguous_headers_raise():
    headers = ["Date", "Phone 101", "101 Backup"]
    with pytest.raises(ValueError):
        map_lines_to_headers(headers, {"Phone 101": 1})
