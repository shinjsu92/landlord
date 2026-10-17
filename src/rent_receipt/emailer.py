"""Build and send emails to the tenant (receipts and late notices)."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

from rent_receipt.config import Landlord, SmtpSettings, Tenant
from rent_receipt.ledger import AccountStatus, balance_text
from rent_receipt.money import format_cents
from rent_receipt.storage import Payment

_SMTP_TIMEOUT_SECONDS = 30


class EmailLoginError(Exception):
    """The SMTP server refused the username or password."""


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
            try:
                smtp.login(self._settings.username, self._settings.password)
            # Gmail sometimes just hangs up on a bad password instead of answering.
            except (smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected) as exc:
                raise EmailLoginError(
                    f"{self._settings.host} refused the login for {self._settings.username} "
                    f"({exc}). Check SMTP_PASSWORD in secrets.env: for Gmail it must be "
                    "the 16-letter app password, not your normal password."
                ) from exc
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
        f"Rent receipt {payment.receipt_id}: {format_cents(payment.amount_cents)} received",
        landlord,
        tenant,
        sender_address,
    )
    message.set_content(
        f"Hi {tenant.name},\n\n"
        f"Thank you for your payment of {format_cents(payment.amount_cents)}, received on "
        f"{payment.received_on.strftime('%B %d, %Y')}.\n\n"
        f"Remaining balance: {balance_text(payment.snapshot.balance_after_cents)}.\n\n"
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
    status: AccountStatus,
    landlord: Landlord,
    tenant: Tenant,
    sender_address: str,
) -> EmailMessage:
    """Polite late notice listing every unpaid charge and the balance due."""
    message = _new_message(
        f"Rent reminder: balance of {format_cents(status.balance_cents)}",
        landlord,
        tenant,
        sender_address,
    )
    items = status.open_items
    width = max(len(i.description) for i in items) + 2 if items else 0
    lines = [
        f"Hi {tenant.name},",
        "",
        "This is a friendly reminder that our records show a balance on your rent account "
        f"as of {status.as_of.strftime('%B %d, %Y')}:",
        "",
    ]
    lines += [f"  {i.description + ':':<{width}} {format_cents(i.remaining_cents)}" for i in items]
    lines += [
        f"  {'Balance due:':<{width}} {format_cents(status.balance_cents)}",
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
