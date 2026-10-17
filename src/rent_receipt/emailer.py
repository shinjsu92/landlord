"""Build and send emails to the tenant (receipts and late notices)."""

from __future__ import annotations

import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

from rent_receipt.config import Landlord, SmtpSettings, Tenant
from rent_receipt.ledger import PeriodStatus
from rent_receipt.money import format_cents
from rent_receipt.storage import Payment

_SMTP_TIMEOUT_SECONDS = 30


class EmailSender(Protocol):
    """Anything that can deliver an email. Tests use a fake one."""

    def send(self, message: EmailMessage) -> None: ...


class SmtpEmailSender:
    """Sends mail through an SMTP server using STARTTLS (e.g. Gmail on port 587)."""

    def __init__(self, settings: SmtpSettings) -> None:
        self._settings = settings

    def send(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        with smtplib.SMTP(
            self._settings.host, self._settings.port, timeout=_SMTP_TIMEOUT_SECONDS
        ) as smtp:
            smtp.starttls(context=context)
            smtp.login(self._settings.username, self._settings.password)
            smtp.send_message(message)


def build_receipt_email(
    payment: Payment,
    pdf_bytes: bytes,
    landlord: Landlord,
    tenant: Tenant,
    sender_address: str,
) -> EmailMessage:
    """Email to the tenant with the PDF attached and a blind copy to the landlord."""
    message = _new_message(
        f"Rent receipt {payment.receipt_id}: {payment.period.label}",
        landlord,
        tenant,
        sender_address,
    )
    message.set_content(
        f"Hi {tenant.name},\n\n"
        f"Thank you for your payment of {format_cents(payment.amount_cents)} "
        f"for {payment.period.label} rent, received on "
        f"{payment.received_on.strftime('%B %d, %Y')}.\n\n"
        f"Your receipt ({payment.receipt_id}) is attached.\n\n"
        f"Best regards,\n{landlord.name}\n"
    )
    message.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=f"rent-receipt-{payment.receipt_id}.pdf",
    )
    return message


def build_late_notice_email(
    status: PeriodStatus,
    due_on: date,
    landlord: Landlord,
    tenant: Tenant,
    sender_address: str,
) -> EmailMessage:
    """Polite late notice listing what is owed for the period."""
    message = _new_message(
        f"Rent reminder: {status.period.label} balance of {format_cents(status.balance_cents)}",
        landlord,
        tenant,
        sender_address,
    )
    lines = [
        f"Hi {tenant.name},",
        "",
        f"This is a friendly reminder that rent for {status.period.label} was due on "
        f"{due_on.strftime('%B %d, %Y')}, and our records show a remaining balance.",
        "",
        f"  Rent:          {format_cents(status.rent_cents)}",
    ]
    if status.late_fee_cents:
        lines.append(f"  Late fee:      {format_cents(status.late_fee_cents)}")
    lines += [
        f"  Paid so far:   {format_cents(status.paid_cents)}",
        f"  Balance due:   {format_cents(status.balance_cents)}",
        "",
        "If you have already sent this payment, thank you, and please disregard this "
        "message. Feel free to reply with any questions.",
        "",
        f"Best regards,\n{landlord.name}",
        "",
    ]
    message.set_content("\n".join(lines))
    return message


def _new_message(
    subject: str, landlord: Landlord, tenant: Tenant, sender_address: str
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((landlord.name, sender_address))
    message["To"] = formataddr((tenant.name, tenant.email))
    message["Reply-To"] = landlord.email
    # smtplib.send_message delivers to Bcc recipients and strips the header.
    message["Bcc"] = landlord.email
    return message
