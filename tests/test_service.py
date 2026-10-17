from datetime import date, datetime
from pathlib import Path

import pytest
from pypdf import PdfReader

from rent_receipt.config import Settings
from rent_receipt.period import Period
from rent_receipt.service import InvalidPaymentDateError, ReceiptNotFoundError, ReceiptService
from rent_receipt.storage import PaymentStore

from .conftest import TZ, FakeClock, FakeSender


@pytest.fixture
def service(
    settings: Settings, store: PaymentStore, sender: FakeSender, clock: FakeClock
) -> ReceiptService:
    return ReceiptService(settings, store, sender, clock=clock)


def test_prepare_defaults_to_today_and_tenant_method(service: ReceiptService) -> None:
    draft = service.prepare(100_000)
    assert draft.payment.received_on == date(2026, 10, 3)
    assert draft.payment.method == "Zelle"
    assert draft.preview.balance_after_cents == 0
    assert "paid in full" in service.describe(draft)


def test_prepare_saves_nothing(service: ReceiptService, store: PaymentStore) -> None:
    service.prepare(100_000)
    assert store.recent() == []


def test_issue_records_saves_pdf_and_emails(service: ReceiptService, sender: FakeSender) -> None:
    issued = service.issue(service.prepare(100_000, method="Cash", note="Thanks"))

    assert issued.emailed
    assert issued.payment.receipt_id == "R-000001"
    assert issued.payment.emailed_at is not None
    assert issued.pdf_path.read_bytes() == issued.pdf_bytes
    assert issued.pdf_bytes.startswith(b"%PDF")

    (message,) = sender.sent
    assert message["To"] == "Jane Kim <jane@example.com>"
    assert message["Bcc"] == "joseph@example.com"
    attachment = next(message.iter_attachments())
    assert attachment.get_filename() == "rent-receipt-R-000001.pdf"


def test_receipt_pdf_shows_key_details(service: ReceiptService, tmp_path: Path) -> None:
    issued = service.issue(service.prepare(60_000))
    text = PdfReader(issued.pdf_path).pages[0].extract_text()
    expected = ["R-000001", "Jane Kim", "Rent, October 2026", "$600.00", "BALANCE DUE $400.00"]
    for snippet in expected:
        assert snippet in text


def test_partial_payments_carry_balance(service: ReceiptService) -> None:
    first = service.issue(service.prepare(60_000)).payment
    second = service.issue(service.prepare(40_000)).payment
    assert first.snapshot.balance_after_cents == 40_000
    assert second.snapshot.balance_before_cents == 40_000
    assert second.snapshot.balance_after_cents == 0
    assert second.receipt_number == first.receipt_number + 1


def test_late_payment_adds_fee_unless_waived(service: ReceiptService, clock: FakeClock) -> None:
    clock.now = datetime(2026, 10, 10, 12, 0, tzinfo=TZ)
    late = service.issue(service.prepare(100_000)).payment
    assert [i.description for i in late.snapshot.owed_before] == [
        "Rent, October 2026",
        "Late fee, October 2026",
    ]
    assert late.snapshot.balance_after_cents == 5_000

    assert service.waive_late_fee(Period(2026, 10)).balance_cents == 0
    # The receipt already issued keeps the amounts it was issued with.
    assert service.recent(1)[0].snapshot.balance_after_cents == 5_000


def test_backdated_payment_avoids_late_fee(service: ReceiptService, clock: FakeClock) -> None:
    clock.now = datetime(2026, 10, 10, 12, 0, tzinfo=TZ)
    service.issue(service.prepare(100_000, received_on=date(2026, 10, 4)))
    assert service.status().balance_cents == 0


def test_future_date_is_refused(service: ReceiptService) -> None:
    with pytest.raises(InvalidPaymentDateError):
        service.prepare(100_000, received_on=date(2026, 10, 4))


def test_overpayment_becomes_credit(service: ReceiptService) -> None:
    issued = service.issue(service.prepare(120_000))
    assert issued.payment.snapshot.balance_after_cents == -20_000
    text = PdfReader(issued.pdf_path).pages[0].extract_text()
    assert "Credit toward future rent" in text
    assert "CREDIT $200.00" in text


def test_email_failure_keeps_the_record(
    service: ReceiptService, sender: FakeSender, store: PaymentStore
) -> None:
    sender.fail_with = OSError("SMTP down")
    issued = service.issue(service.prepare(100_000))

    assert not issued.emailed
    assert issued.email_error == "SMTP down"
    assert store.get(issued.payment.receipt_number) is not None
    assert issued.pdf_path.exists()

    sender.fail_with = None
    resent = service.resend(issued.payment.receipt_number)
    assert resent.emailed
    assert resent.pdf_bytes.startswith(b"%PDF")


def test_resend_unknown_receipt(service: ReceiptService) -> None:
    with pytest.raises(ReceiptNotFoundError):
        service.resend(99)
