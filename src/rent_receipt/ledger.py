"""What is owed for a rent period: rent, late fee, payments and balance.

These are pure functions with no database or clock, so the late fee rules can
be tested on their own and are applied the same way for receipts, previews
and reminders.

Late fee rule: the fee applies to a month once that month's late fee date has
arrived, if the rent was not fully paid *before* that date and no payment for
the month was marked "waive late fee".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
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

    @property
    def waive_late_fee(self) -> bool: ...


@dataclass(frozen=True)
class PeriodStatus:
    period: Period
    rent_cents: int
    late_fee_cents: int
    """The late fee charged for the period, or 0 if none applies."""
    paid_cents: int

    @property
    def owed_cents(self) -> int:
        return self.rent_cents + self.late_fee_cents

    @property
    def balance_cents(self) -> int:
        """Still owed; negative means the tenant overpaid."""
        return self.owed_cents - self.paid_cents


def due_date(tenant: Tenant, period: Period) -> date:
    return date(period.year, period.month, tenant.rent_due_day)


def late_fee_date(tenant: Tenant, period: Period) -> date:
    return date(period.year, period.month, tenant.late_fee_day)


def period_status(
    tenant: Tenant,
    period: Period,
    payments: Iterable[LedgerEntry],
    as_of: date,
) -> PeriodStatus:
    """Status of ``period`` on the day ``as_of``, given its payments."""
    payments = list(payments)
    fee_date = late_fee_date(tenant, period)
    paid_on_time = sum(p.amount_cents for p in payments if p.received_on < fee_date)
    waived = any(p.waive_late_fee for p in payments)

    fee_applies = (
        tenant.late_fee_cents > 0
        and not waived
        and as_of >= fee_date
        and paid_on_time < tenant.monthly_rent_cents
    )
    return PeriodStatus(
        period=period,
        rent_cents=tenant.monthly_rent_cents,
        late_fee_cents=tenant.late_fee_cents if fee_applies else 0,
        paid_cents=sum(p.amount_cents for p in payments),
    )


def balance_text(balance_cents: int) -> str:
    """Describe a balance for people, e.g. ``"$200.00 remaining"``."""
    if balance_cents == 0:
        return "$0.00 (paid in full)"
    if balance_cents > 0:
        return f"{format_cents(balance_cents)} remaining"
    return f"{format_cents(-balance_cents)} overpaid (credit)"
