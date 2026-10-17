import pytest

from rent_receipt.money import InvalidAmountError, format_cents, parse_amount


@pytest.mark.parametrize(
    ("text", "cents"),
    [("1000", 100_000), ("$1,000.50", 100_050), (" 1000.5 ", 100_050), ("0.01", 1)],
)
def test_parse_amount(text: str, cents: int) -> None:
    assert parse_amount(text) == cents


@pytest.mark.parametrize("text", ["", "abc", "-5", "0", "10.001", "nan", "inf", "2000000"])
def test_parse_amount_rejects_bad_input(text: str) -> None:
    with pytest.raises(InvalidAmountError):
        parse_amount(text)


def test_parse_amount_allows_zero_when_asked() -> None:
    assert parse_amount("0", allow_zero=True) == 0


@pytest.mark.parametrize(
    ("cents", "text"), [(0, "$0.00"), (100_050, "$1,000.50"), (-2_500, "-$25.00")]
)
def test_format_cents(cents: int, text: str) -> None:
    assert format_cents(cents) == text
