"""The core workflow: preview a payment, then record it, make the PDF and email it.

This module knows nothing about Discord, so another input (a web form, email,
SMS) can reuse it unchanged later.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from rent_receipt.config import Settings
from rent_receipt.emailer import EmailSender, build_receipt_email
from rent_receipt.ledger import AccountStatus, account_status, balance_text
from rent_receipt.money import format_cents
from rent_receipt.period import Period
from rent_receipt.receipt_pdf import render_receipt
from rent_receipt.storage import NewPayment, Payment, PaymentStore, Snapshot, SnapshotItem

logger = logging.getLogger(__name__)


class ReceiptNotFoundError(LookupError):
    """Raised when a receipt number doesn't exist."""


class InvalidPaymentDateError(ValueError):
    """Raised when a received date is in the future."""


@dataclass(frozen=True)
class Draft:
    """A payment the landlord has entered but not yet confirmed."""

    payment: NewPayment
    preview: Snapshot


@dataclass(frozen=True)
class IssuedReceipt:
    payment: Payment
    pdf_path: Path
    pdf_bytes: bytes
    email_error: str | None

    @property
    def emailed(self) -> bool:
        return self.email_error is None


class ReceiptService:
    def __init__(
        self,
        settings: Settings,
        store: PaymentStore,
        sender: EmailSender,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._sender = sender
        self._clock = clock or (lambda: datetime.now(settings.timezone))
        self._receipts_dir = settings.data_dir / "receipts"

    def now(self) -> datetime:
        return self._clock()

    def today(self) -> date:
        return self._clock().date()

    def status(self, as_of: date | None = None) -> AccountStatus:
        """The tenant's account on ``as_of`` (default: today)."""
        tenant = self._settings.tenant
        return account_status(
            tenant,
            self._store.payments(tenant.email),
            self._store.waived_periods(tenant.email),
            as_of or self.today(),
        )

    def waive_late_fee(self, period: Period | None = None) -> AccountStatus:
        """Waive the late fee for ``period`` (default: this month) and return the new status."""
        tenant = self._settings.tenant
        self._store.waive_late_fee(
            tenant.email, period or Period.containing(self.today()), self._clock()
        )
        return self.status()

    def prepare(
        self,
        amount_cents: int,
        received_on: date | None = None,
        method: str | None = None,
        note: str | None = None,
    ) -> Draft:
        """Build a draft for the landlord to confirm. Nothing is saved yet."""
        tenant = self._settings.tenant
        today = self.today()
        received_on = received_on or today
        if received_on > today:
            raise InvalidPaymentDateError(
                f"{received_on:%B %d, %Y} is in the future. Use the date you received it."
            )
        new_payment = NewPayment(
            tenant_email=tenant.email,
            amount_cents=amount_cents,
            received_on=received_on,
            method=method or tenant.default_payment_method,
            note=note.strip() if note and note.strip() else None,
        )
        saved = self._store.payments(tenant.email)
        waived = self._store.waived_periods(tenant.email)
        preview = self._snapshot(new_payment, saved, waived)
        return Draft(payment=new_payment, preview=preview)

    def describe(self, draft: Draft) -> str:
        """Short preview shown before the landlord confirms."""
        payment, preview = draft.payment, draft.preview
        tenant = self._settings.tenant
        lines = [
            f"**Receipt for {tenant.name}** ({tenant.property_address})",
            f"Amount: **{format_cents(payment.amount_cents)}**, received "
            f"{payment.received_on:%B %d, %Y} by {payment.method}",
            "Owed before this: "
            + (
                format_cents(preview.balance_before_cents)
                if preview.balance_before_cents > 0
                else balance_text(preview.balance_before_cents)
            ),
        ]
        lines += [f"- {i.description}: {format_cents(i.amount_cents)}" for i in preview.owed_before]
        lines.append(f"Balance after this: **{balance_text(preview.balance_after_cents)}**")
        if payment.note:
            lines.append(f"Note: {payment.note}")
        lines.append(f"Will be emailed to: {tenant.email} (copy to you)")
        return "\n".join(lines)

    def issue(self, draft: Draft) -> IssuedReceipt:
        """Record the payment, save its PDF and email it to the tenant.

        The payment and PDF are saved before emailing, so an email failure
        never loses the record; the receipt can be re-sent later.
        """
        # Read outside store.add: its callback runs while the store's lock is held.
        waived = self._store.waived_periods(self._settings.tenant.email)
        payment = self._store.add(
            draft.payment,
            snapshot_fn=lambda saved: self._snapshot(draft.payment, saved, waived),
            created_at=self._clock(),
        )
        logger.info("Recorded payment %s", payment.receipt_id)
        return self._render_and_send(payment)

    def resend(self, receipt_number: int) -> IssuedReceipt:
        payment = self._store.get(receipt_number)
        if payment is None:
            raise ReceiptNotFoundError(f"There is no receipt number {receipt_number}.")
        return self._render_and_send(payment)

    def recent(self, limit: int = 5) -> list[Payment]:
        return self._store.recent(limit)

    def _snapshot(
        self, new_payment: NewPayment, saved: list[Payment], waived: set[Period]
    ) -> Snapshot:
        """The account on the payment's date, just before and after it."""
        before = account_status(self._settings.tenant, saved, waived, new_payment.received_on)
        return Snapshot(
            owed_before=tuple(
                SnapshotItem(i.description, i.remaining_cents) for i in before.open_items
            ),
            balance_before_cents=before.balance_cents,
            balance_after_cents=before.balance_cents - new_payment.amount_cents,
        )

    def _render_and_send(self, payment: Payment) -> IssuedReceipt:
        settings = self._settings
        pdf_bytes = render_receipt(payment, settings.landlord, settings.tenant)
        self._receipts_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = self._receipts_dir / f"{payment.receipt_id}.pdf"
        pdf_path.write_bytes(pdf_bytes)

        message = build_receipt_email(
            payment,
            pdf_bytes,
            settings.landlord,
            settings.tenant,
            sender_address=settings.smtp.username,
        )
        try:
            self._sender.send(message)
        except Exception as exc:  # Any delivery failure is reported, not raised.
            logger.exception("Emailing %s failed", payment.receipt_id)
            return IssuedReceipt(payment, pdf_path, pdf_bytes, email_error=str(exc) or repr(exc))

        self._store.mark_emailed(payment.receipt_number, self._clock())
        logger.info("Emailed %s to %s", payment.receipt_id, settings.tenant.email)
        refreshed = self._store.get(payment.receipt_number) or payment
        return IssuedReceipt(refreshed, pdf_path, pdf_bytes, email_error=None)
