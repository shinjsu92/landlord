"""The tenant's account: charges, payments and the running balance.

These are pure functions with no database or clock, so the rules can be tested
on their own and are applied the same way for receipts, previews and reminders.

Charges, oldest first:

- Opening balance items from the config (what was owed before the bot started
  tracking, e.g. last month's unpaid rent).
- Rent for every month from ``tenant.billing_start``, due on the rent due day.
- A late fee for a month, from its late fee date, unless waived.

Payments pay off the oldest charges first, so the balance is simply everything
charged minus everything paid, and the open items show what is still unpaid.

Late fee rule: a month's late fee applies once its late fee date arrives if,
counting every payment received before that date, the rent charged from
``billing_start`` through that month isn't covered. For this test payments
count toward rent before older balances and fees, so a tenant who pays the
month's rent on time is never charged a late fee just because an old balance or
an earlier late fee is still open.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Protocol

from rent_receipt.config import Tenant
from rent_receipt.money import format_cents
from rent_receipt.period import Period


class LedgerEntry(Protocol):
    """The parts of a payment the ledger needs."""

    @property
    def amount_cents(self) -> int: ...

    @property
    def received_on(self) -> date: ...


class ChargeKind(StrEnum):
    OPENING = "opening"
    RENT = "rent"
    LATE_FEE = "late_fee"


@dataclass(frozen=True)
class Charge:
    kind: ChargeKind
    description: str
    amount_cents: int
    due_on: date
    period: Period | None
    """The month a rent or late fee charge belongs to; None for opening items."""


@dataclass(frozen=True)
class OpenItem:
    """A charge that is not fully paid yet."""

    description: str
    remaining_cents: int


@dataclass(frozen=True)
class AccountStatus:
    as_of: date
    charges: tuple[Charge, ...]
    paid_cents: int

    @property
    def charged_cents(self) -> int:
        return sum(c.amount_cents for c in self.charges)

    @property
    def balance_cents(self) -> int:
        """Still owed; negative means the tenant has a credit."""
        return self.charged_cents - self.paid_cents

    @property
    def open_items(self) -> list[OpenItem]:
        """Unpaid charges, oldest first, after paying off the oldest first."""
        return open_items(self.charges, self.paid_cents)

    def late_fee_cents(self, period: Period) -> int:
        return sum(
            c.amount_cents
            for c in self.charges
            if c.kind is ChargeKind.LATE_FEE and c.period == period
        )


def due_date(tenant: Tenant, period: Period) -> date:
    return date(period.year, period.month, tenant.rent_due_day)


def late_fee_date(tenant: Tenant, period: Period) -> date:
    return date(period.year, period.month, tenant.late_fee_day)


def account_status(
    tenant: Tenant,
    payments: Iterable[LedgerEntry],
    waived: Collection[Period],
    as_of: date,
) -> AccountStatus:
    """The account on the day ``as_of``, counting payments received by then."""
    counted = [p for p in payments if p.received_on <= as_of]
    return AccountStatus(
        as_of=as_of,
        charges=tuple(charges(tenant, counted, waived, as_of)),
        paid_cents=sum(p.amount_cents for p in counted),
    )


def charges(
    tenant: Tenant,
    payments: Iterable[LedgerEntry],
    waived: Collection[Period],
    as_of: date,
) -> list[Charge]:
    """Every charge due on or before ``as_of``, oldest first."""
    payments = list(payments)
    start = tenant.billing_start
    opening_due = date(start.year, start.month, 1) - timedelta(days=1)
    result = [
        Charge(ChargeKind.OPENING, item.description, item.amount_cents, opening_due, None)
        for item in tenant.opening_balance
    ]

    rent_charged = 0
    for period in _months(start, Period.containing(as_of)):
        rent_due = due_date(tenant, period)
        if rent_due > as_of:
            break
        rent_charged += tenant.monthly_rent_cents
        result.append(
            Charge(
                ChargeKind.RENT,
                f"Rent, {period.label}",
                tenant.monthly_rent_cents,
                rent_due,
                period,
            )
        )

        fee_date = late_fee_date(tenant, period)
        if tenant.late_fee_cents == 0 or period in waived or fee_date > as_of:
            continue
        paid_in_time = sum(p.amount_cents for p in payments if p.received_on < fee_date)
        if paid_in_time < rent_charged:
            result.append(
                Charge(
                    ChargeKind.LATE_FEE,
                    f"Late fee, {period.label}",
                    tenant.late_fee_cents,
                    fee_date,
                    period,
                )
            )
    return result


def open_items(charges: Iterable[Charge], paid_cents: int) -> list[OpenItem]:
    """Apply ``paid_cents`` to ``charges`` oldest first and list what is left."""
    remaining_payment = paid_cents
    items: list[OpenItem] = []
    for charge in charges:
        applied = min(charge.amount_cents, max(remaining_payment, 0))
        remaining_payment -= applied
        if charge.amount_cents > applied:
            items.append(OpenItem(charge.description, charge.amount_cents - applied))
    return items


def rent_unpaid_cents(tenant: Tenant, payments: Iterable[LedgerEntry], period: Period) -> int:
    """Rent through ``period`` not yet covered by payments, as the late fee test counts it.

    Used to warn the landlord the day before a late fee would apply.
    """
    if period < tenant.billing_start:
        return 0
    months = len(_months(tenant.billing_start, period))
    paid = sum(p.amount_cents for p in payments)
    return max(months * tenant.monthly_rent_cents - paid, 0)


def balance_text(balance_cents: int) -> str:
    """Describe a balance for people, e.g. ``"$200.00 remaining"``."""
    if balance_cents == 0:
        return "$0.00 (paid in full)"
    if balance_cents > 0:
        return f"{format_cents(balance_cents)} remaining"
    return f"{format_cents(-balance_cents)} credit"


def _months(first: Period, last: Period) -> list[Period]:
    months: list[Period] = []
    period = first
    while period <= last:
        months.append(period)
        period = period.next()
    return months
