"""Money helpers.

Amounts are stored as integer cents everywhere so that no rounding error can
creep into a receipt. Conversion to and from text happens only at the edges.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

MAX_AMOUNT_CENTS = 100_000_000  # $1,000,000.00, a sanity limit against typos.


class InvalidAmountError(ValueError):
    """Raised when text cannot be read as a positive dollar amount."""


def parse_amount(text: str, *, allow_zero: bool = False) -> int:
    """Convert text such as ``"1000"``, ``"$1,000.50"`` or ``"1000.5"`` to cents.

    Zero is rejected unless ``allow_zero`` is set (e.g. for "no late fee").
    """
    cleaned = text.strip().replace(",", "").removeprefix("$").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise InvalidAmountError(f"{text!r} is not a dollar amount.") from exc

    if not value.is_finite() or value < 0 or (value == 0 and not allow_zero):
        raise InvalidAmountError("The amount must be greater than zero.")
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise InvalidAmountError("The amount can have at most two decimal places.")

    cents = int(value * 100)
    if cents > MAX_AMOUNT_CENTS:
        raise InvalidAmountError("That amount looks too large. Please check it.")
    return cents


def format_cents(cents: int) -> str:
    """Format cents as dollars, e.g. ``100050`` -> ``"$1,000.50"``."""
    sign = "-" if cents < 0 else ""
    dollars, remainder = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{remainder:02d}"
