from dataclasses import dataclass
from datetime import date

from rent_receipt.ledger import balance_text, period_status
from rent_receipt.period import Period

from .conftest import make_tenant

OCT = Period(2026, 10)


@dataclass(frozen=True)
class Entry:
    amount_cents: int
    received_on: date
    waive_late_fee: bool = False


def test_paid_on_time_has_no_late_fee() -> None:
    status = period_status(
        make_tenant(), OCT, [Entry(100_000, date(2026, 10, 2))], date(2026, 10, 20)
    )
    assert status.late_fee_cents == 0
    assert status.balance_cents == 0


def test_unpaid_before_late_day_has_no_fee_yet() -> None:
    status = period_status(make_tenant(), OCT, [], date(2026, 10, 5))
    assert status.late_fee_cents == 0
    assert status.balance_cents == 100_000


def test_fee_applies_from_late_day_when_not_fully_paid() -> None:
    payments = [Entry(60_000, date(2026, 10, 1))]
    status = period_status(make_tenant(), OCT, payments, date(2026, 10, 6))
    assert status.late_fee_cents == 5_000
    assert status.balance_cents == 100_000 + 5_000 - 60_000


def test_fee_stays_after_paying_late() -> None:
    payments = [Entry(100_000, date(2026, 10, 9))]
    status = period_status(make_tenant(), OCT, payments, date(2026, 10, 9))
    assert status.late_fee_cents == 5_000
    assert status.balance_cents == 5_000


def test_waiver_removes_fee() -> None:
    payments = [Entry(100_000, date(2026, 10, 9), waive_late_fee=True)]
    status = period_status(make_tenant(), OCT, payments, date(2026, 10, 9))
    assert status.late_fee_cents == 0
    assert status.balance_cents == 0


def test_zero_late_fee_setting() -> None:
    status = period_status(make_tenant(late_fee_cents=0), OCT, [], date(2026, 10, 20))
    assert status.late_fee_cents == 0


def test_balance_text() -> None:
    assert balance_text(0) == "$0.00 (paid in full)"
    assert balance_text(20_000) == "$200.00 remaining"
    assert balance_text(-1_000) == "$10.00 overpaid (credit)"
