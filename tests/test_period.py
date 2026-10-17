from datetime import date

import pytest

from rent_receipt.period import InvalidPeriodError, Period


def test_parse_and_format() -> None:
    period = Period.parse("2026-1")
    assert period == Period(2026, 1)
    assert period.key == "2026-01"
    assert period.label == "January 2026"


def test_containing() -> None:
    assert Period.containing(date(2026, 10, 17)) == Period(2026, 10)


@pytest.mark.parametrize("text", ["2026", "2026-13", "10-2026", "October"])
def test_parse_rejects_bad_input(text: str) -> None:
    with pytest.raises(InvalidPeriodError):
        Period.parse(text)
