from dataclasses import dataclass
from datetime import date

from rent_receipt.config import OpeningItem
from rent_receipt.ledger import AccountStatus, account_status, balance_text, rent_unpaid_cents
from rent_receipt.period import Period

from .conftest import make_tenant

OCT = Period(2026, 10)
NOV = Period(2026, 11)


@dataclass(frozen=True)
class Entry:
    amount_cents: int
    received_on: date


def owed(status: AccountStatus) -> list[tuple[str, int]]:
    return [(i.description, i.remaining_cents) for i in status.open_items]


def test_paid_on_time_has_no_late_fee() -> None:
    status = account_status(
        make_tenant(), [Entry(100_000, date(2026, 10, 2))], set(), date(2026, 10, 20)
    )
    assert status.late_fee_cents(OCT) == 0
    assert status.balance_cents == 0
    assert status.open_items == []


def test_unpaid_before_late_day_has_no_fee_yet() -> None:
    status = account_status(make_tenant(), [], set(), date(2026, 10, 5))
    assert owed(status) == [("Rent, October 2026", 100_000)]


def test_fee_applies_from_late_day_when_not_fully_paid() -> None:
    payments = [Entry(60_000, date(2026, 10, 1))]
    status = account_status(make_tenant(), payments, set(), date(2026, 10, 6))
    assert owed(status) == [("Rent, October 2026", 40_000), ("Late fee, October 2026", 5_000)]


def test_fee_stays_after_paying_late() -> None:
    payments = [Entry(100_000, date(2026, 10, 9))]
    status = account_status(make_tenant(), payments, set(), date(2026, 10, 9))
    assert owed(status) == [("Late fee, October 2026", 5_000)]


def test_waiver_removes_fee() -> None:
    payments = [Entry(100_000, date(2026, 10, 9))]
    status = account_status(make_tenant(), payments, {OCT}, date(2026, 10, 9))
    assert status.balance_cents == 0


def test_zero_late_fee_setting() -> None:
    status = account_status(make_tenant(late_fee_cents=0), [], set(), date(2026, 10, 20))
    assert status.balance_cents == 100_000


def test_payments_after_as_of_are_not_counted() -> None:
    payments = [Entry(100_000, date(2026, 10, 9))]
    status = account_status(make_tenant(), payments, set(), date(2026, 10, 8))
    assert status.balance_cents == 105_000


def test_unpaid_balance_carries_into_next_month() -> None:
    status = account_status(make_tenant(), [], set(), date(2026, 11, 2))
    assert owed(status) == [
        ("Rent, October 2026", 100_000),
        ("Late fee, October 2026", 5_000),
        ("Rent, November 2026", 100_000),
    ]


def test_payments_pay_oldest_charges_first() -> None:
    tenant = make_tenant(
        opening_balance=(OpeningItem("Rent, September 2026", 100_000),),
    )
    status = account_status(tenant, [Entry(130_000, date(2026, 10, 3))], set(), date(2026, 10, 20))
    # The payment clears September first and then 30,000 of October. It was
    # received before the 6th and covers a month's rent, so no late fee.
    assert owed(status) == [("Rent, October 2026", 70_000)]


def test_old_balance_alone_does_not_cause_a_late_fee() -> None:
    tenant = make_tenant(opening_balance=(OpeningItem("Late fees before October", 7_000),))
    status = account_status(tenant, [Entry(100_000, date(2026, 10, 1))], set(), date(2026, 10, 20))
    assert status.late_fee_cents(OCT) == 0
    assert owed(status) == [("Rent, October 2026", 7_000)]


def test_josephs_october_example() -> None:
    """Sept rent and $70 of fees owed going in; four payments recorded on Oct 16."""
    tenant = make_tenant(
        monthly_rent_cents=220_000,
        late_fee_cents=3_500,
        opening_balance=(
            OpeningItem("Rent, September 2026", 220_000),
            OpeningItem("Late fees before October 2026", 7_000),
        ),
    )
    received = date(2026, 10, 16)
    payments = [Entry(cents, received) for cents in (160_000, 100_000, 40_000, 20_000)]
    status = account_status(tenant, payments, set(), received)
    assert status.charged_cents == 450_500
    assert status.paid_cents == 320_000
    assert status.balance_cents == 130_500
    assert owed(status) == [("Rent, October 2026", 127_000), ("Late fee, October 2026", 3_500)]


def test_charges_start_at_billing_start() -> None:
    status = account_status(make_tenant(billing_start=NOV), [], set(), date(2026, 10, 20))
    assert status.charges == ()


def test_rent_unpaid_counts_rent_only() -> None:
    tenant = make_tenant(opening_balance=(OpeningItem("Old", 50_000),))
    assert rent_unpaid_cents(tenant, [Entry(30_000, date(2026, 10, 2))], OCT) == 70_000
    assert rent_unpaid_cents(tenant, [], NOV) == 200_000
    assert rent_unpaid_cents(tenant, [], Period(2026, 9)) == 0


def test_balance_text() -> None:
    assert balance_text(0) == "$0.00 (paid in full)"
    assert balance_text(20_000) == "$200.00 remaining"
    assert balance_text(-1_000) == "$10.00 credit"
