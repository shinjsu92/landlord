from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, time
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from rent_receipt.config import (
    DiscordSettings,
    Landlord,
    ReminderSettings,
    Settings,
    SmtpSettings,
    Tenant,
)
from rent_receipt.period import Period
from rent_receipt.storage import PaymentStore

TZ = ZoneInfo("America/Los_Angeles")


class FakeSender:
    """Collects emails instead of sending them; can be told to fail."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self.fail_with: Exception | None = None

    def send(self, message: EmailMessage) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(message)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_tenant(**overrides: object) -> Tenant:
    values: dict[str, object] = {
        "name": "Jane Kim",
        "email": "jane@example.com",
        "property_address": "123 Main St, Unit 2",
        "monthly_rent_cents": 100_000,
        "rent_due_day": 1,
        "late_fee_day": 6,
        "late_fee_cents": 5_000,
        "default_payment_method": "Zelle",
        "billing_start": Period(2026, 10),
    }
    values.update(overrides)
    return Tenant(**values)  # type: ignore[arg-type]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        landlord=Landlord(name="Joseph", email="joseph@example.com"),
        tenant=make_tenant(),
        smtp=SmtpSettings(
            host="smtp.example.com", port=587, username="joseph@example.com", password="x"
        ),
        discord=DiscordSettings(token="t", owner_user_id=1, guild_id=2),
        reminders=ReminderSettings(
            enabled=True, send_time=time(9, 0), email_tenant_late_notice=True
        ),
        data_dir=tmp_path,
        timezone=TZ,
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[PaymentStore]:
    payment_store = PaymentStore(tmp_path / "payments.sqlite3")
    yield payment_store
    payment_store.close()


@pytest.fixture
def sender() -> FakeSender:
    return FakeSender()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 10, 3, 12, 0, tzinfo=TZ))
