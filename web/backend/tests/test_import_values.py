from datetime import datetime

import pytest

from app.import_values import parse_csv_list, parse_scraped_at


@pytest.mark.parametrize("value", [
    "2026-09-10T09:30:00+03:00", "2026-09-10T06:30:00Z", "2026-09-10T06:30:00",
])
def test_csv_timestamps_are_utc_without_timezone(value):
    assert parse_scraped_at(value) == datetime(2026, 9, 10, 6, 30)


@pytest.mark.parametrize("value", ["shop; coffee", "shop, coffee", '["shop", "coffee"]'])
def test_parser_and_legacy_list_formats(value):
    assert parse_csv_list(value) == ["shop", "coffee"]


def test_invalid_list_members_are_not_silently_imported():
    with pytest.raises(ValueError):
        parse_csv_list('[null, "coffee"]')
