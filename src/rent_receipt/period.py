"""The rent period (a calendar month) that a payment covers."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

_PERIOD_PATTERN = re.compile(r"^(\d{4})-(\d{1,2})$")


class InvalidPeriodError(ValueError):
    """Raised when text cannot be read as a ``YYYY-MM`` month."""


@dataclass(frozen=True, order=True)
class Period:
    """A calendar month, e.g. October 2026."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise InvalidPeriodError(f"Month must be 1-12, got {self.month}.")

    @classmethod
    def parse(cls, text: str) -> Period:
        """Read ``"2026-10"`` (or ``"2026-1"``) as a period."""
        match = _PERIOD_PATTERN.match(text.strip())
        if match is None:
            raise InvalidPeriodError(f"{text!r} is not a month. Use YYYY-MM, e.g. 2026-10.")
        return cls(year=int(match.group(1)), month=int(match.group(2)))

    @classmethod
    def containing(cls, day: date) -> Period:
        """The period that a given day falls in."""
        return cls(year=day.year, month=day.month)

    def next(self) -> Period:
        """The following month."""
        if self.month == 12:
            return Period(self.year + 1, 1)
        return Period(self.year, self.month + 1)

    @property
    def key(self) -> str:
        """Stable storage form, e.g. ``"2026-10"``."""
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def label(self) -> str:
        """Human-readable form, e.g. ``"October 2026"``."""
        return f"{calendar.month_name[self.month]} {self.year}"
