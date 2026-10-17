"""Monthly reminders.

Three reminders per month, each sent at most once (tracked in the database):

1. ``due``: on the rent due day, tell the landlord what is owed.
2. ``late_heads_up``: the day before the late fee date, if a balance remains,
   warn the landlord so a payment received outside the bot can be recorded
   before the tenant is emailed.
3. ``late_notice``: on the late fee date, if a balance remains, email the
   tenant a late notice and tell the landlord.

A reminder only goes out on its own day. If the bot was offline that whole
day, it is skipped rather than sent late, so starting the bot mid-month never
fires off a surprise late notice for a month whose payments weren't recorded.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from enum import StrEnum

from rent_receipt.config import Settings
from rent_receipt.emailer import EmailSender, build_late_notice_email
from rent_receipt.ledger import due_date, late_fee_date
from rent_receipt.money import format_cents
from rent_receipt.period import Period
from rent_receipt.service import ReceiptService
from rent_receipt.storage import PaymentStore

logger = logging.getLogger(__name__)


class ReminderKind(StrEnum):
    DUE = "due"
    LATE_HEADS_UP = "late_heads_up"
    LATE_NOTICE = "late_notice"


class ReminderService:
    def __init__(
        self,
        settings: Settings,
        store: PaymentStore,
        receipts: ReceiptService,
        sender: EmailSender,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._receipts = receipts
        self._sender = sender
        self._clock = clock or (lambda: datetime.now(settings.timezone))

    def run_due_reminders(self) -> list[str]:
        """Send whatever reminders are due right now.

        Returns messages for the landlord. Safe to call as often as you like.
        """
        settings = self._settings
        now = self._clock()
        if not settings.reminders.enabled or now.time() < settings.reminders.send_time:
            return []

        today = now.date()
        period = Period.containing(today)
        messages: list[str] = []
        for kind, day in self._schedule(period).items():
            if day != today or self._store.reminder_sent(kind, period):
                continue
            message = self._send(kind, period, today)
            # Recorded even when the email failed, so a broken mail setup
            # can't repeat the attempt all day; the landlord is told and can
            # retry with /late_notice.
            self._store.record_reminder(kind, period, now)
            if message:
                messages.append(message)
        return messages

    def send_late_notice(self, period: Period | None = None) -> str:
        """Email the tenant a late notice now, if a balance remains."""
        today = self._clock().date()
        status = self._receipts.status(period, as_of=today)
        tenant = self._settings.tenant
        if status.balance_cents <= 0:
            return f"{status.period.label} is paid in full, so no late notice was sent."

        message = build_late_notice_email(
            status,
            due_on=due_date(tenant, status.period),
            landlord=self._settings.landlord,
            tenant=tenant,
            sender_address=self._settings.smtp.username,
        )
        try:
            self._sender.send(message)
        except Exception as exc:  # Reported to the landlord, not raised.
            logger.exception("Sending the late notice failed")
            return (
                f"⚠️ I couldn't email the late notice for {status.period.label}: {exc}. "
                "Fix the email settings, then use /late_notice to try again."
            )
        logger.info("Late notice for %s sent to %s", status.period.key, tenant.email)
        return (
            f"📨 Late notice emailed to {tenant.name} for {status.period.label}: "
            f"balance due {format_cents(status.balance_cents)}."
        )

    def _schedule(self, period: Period) -> dict[ReminderKind, date]:
        tenant = self._settings.tenant
        due_on = due_date(tenant, period)
        fee_on = late_fee_date(tenant, period)
        schedule = {ReminderKind.DUE: due_on, ReminderKind.LATE_NOTICE: fee_on}
        heads_up_on = fee_on - timedelta(days=1)
        if heads_up_on > due_on:  # Otherwise the due reminder already covers it.
            schedule[ReminderKind.LATE_HEADS_UP] = heads_up_on
        return schedule

    def _send(self, kind: ReminderKind, period: Period, today: date) -> str | None:
        tenant = self._settings.tenant
        status = self._receipts.status(period, as_of=today)

        if kind is ReminderKind.DUE:
            if status.balance_cents <= 0:
                return f"✅ Rent for {period.label} is due today and is already paid in full."
            return (
                f"🔔 Rent for {period.label} is due today. {tenant.name} owes "
                f"{format_cents(status.owed_cents)}, has paid {format_cents(status.paid_cents)}, "
                f"and has {format_cents(status.balance_cents)} left to pay."
            )

        if kind is ReminderKind.LATE_HEADS_UP:
            if status.balance_cents <= 0:
                return None
            fee = tenant.late_fee_cents
            fee_text = f" plus the {format_cents(fee)} late fee" if fee else ""
            notice_text = (
                f" and {tenant.name} will be emailed a late notice"
                if self._settings.reminders.email_tenant_late_notice
                else ""
            )
            return (
                f"⏰ Heads-up: {format_cents(status.balance_cents)} is still unpaid for "
                f"{period.label}. Tomorrow it becomes late{fee_text}{notice_text}. "
                "If they already paid you, record it now with /received."
            )

        # ReminderKind.LATE_NOTICE
        if status.balance_cents <= 0:
            return None
        if not self._settings.reminders.email_tenant_late_notice:
            return (
                f"⚠️ Rent for {period.label} is now late. {tenant.name} has "
                f"{format_cents(status.balance_cents)} left to pay (late notices to the "
                "tenant are turned off; use /late_notice to send one)."
            )
        return self.send_late_notice(period)
