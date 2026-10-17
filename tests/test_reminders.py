from datetime import datetime

import pytest

from rent_receipt.config import Settings
from rent_receipt.reminders import ReminderService
from rent_receipt.service import ReceiptService
from rent_receipt.storage import PaymentStore

from .conftest import TZ, FakeClock, FakeSender


@pytest.fixture
def receipts(
    settings: Settings, store: PaymentStore, sender: FakeSender, clock: FakeClock
) -> ReceiptService:
    return ReceiptService(settings, store, sender, clock=clock)


@pytest.fixture
def reminders(
    settings: Settings,
    store: PaymentStore,
    receipts: ReceiptService,
    sender: FakeSender,
    clock: FakeClock,
) -> ReminderService:
    return ReminderService(settings, store, receipts, sender, clock=clock)


def at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 10, day, hour, 0, tzinfo=TZ)


def test_nothing_before_send_time(reminders: ReminderService, clock: FakeClock) -> None:
    clock.now = at(1, hour=8)
    assert reminders.run_due_reminders() == []


def test_due_day_reminder_sent_once(reminders: ReminderService, clock: FakeClock) -> None:
    clock.now = at(1)
    (message,) = reminders.run_due_reminders()
    assert "due today" in message
    assert "$1,000.00 left to pay" in message
    clock.now = at(1, hour=15)
    assert reminders.run_due_reminders() == []


def test_heads_up_then_late_notice_emails_tenant(
    reminders: ReminderService, clock: FakeClock, sender: FakeSender
) -> None:
    clock.now = at(5)
    (heads_up,) = reminders.run_due_reminders()
    assert "Heads-up" in heads_up
    assert sender.sent == []

    clock.now = at(6)
    (notice,) = reminders.run_due_reminders()
    assert "Late notice emailed" in notice
    (email,) = sender.sent
    assert "$1,050.00" in email["Subject"]
    assert "Late fee:      $50.00" in email.get_content()


def test_no_late_notice_when_paid(
    reminders: ReminderService, receipts: ReceiptService, clock: FakeClock, sender: FakeSender
) -> None:
    clock.now = at(2)
    receipts.issue(receipts.prepare(100_000))
    sender.sent.clear()

    clock.now = at(6)
    assert reminders.run_due_reminders() == []
    assert sender.sent == []


def test_missed_day_is_not_sent_late(
    reminders: ReminderService, clock: FakeClock, sender: FakeSender
) -> None:
    clock.now = at(17)  # e.g. the bot was first started mid-month
    assert reminders.run_due_reminders() == []
    assert sender.sent == []


def test_failed_late_notice_is_reported_not_repeated(
    reminders: ReminderService, clock: FakeClock, sender: FakeSender
) -> None:
    sender.fail_with = OSError("SMTP down")
    clock.now = at(6)
    (message,) = reminders.run_due_reminders()
    assert "couldn't email" in message
    clock.now = at(6, hour=10)
    assert reminders.run_due_reminders() == []
