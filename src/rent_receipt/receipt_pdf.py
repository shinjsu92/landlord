"""Render a payment as a one-page PDF receipt.

Uses fpdf2, which is pure Python, so nothing has to be installed on the server
outside this app's own virtual environment. The built-in Helvetica font covers
English (Latin-1) text only; see the README if you need other alphabets.
"""

from __future__ import annotations

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from rent_receipt.config import Landlord, Tenant
from rent_receipt.ledger import balance_text
from rent_receipt.money import format_cents
from rent_receipt.storage import Payment

_FONT = "Helvetica"
_LABEL_WIDTH = 55


def render_receipt(payment: Payment, landlord: Landlord, tenant: Tenant) -> bytes:
    """Return the PDF bytes for ``payment``."""
    pdf = FPDF(format="Letter")
    pdf.set_title(f"Rent receipt {payment.receipt_id}")
    pdf.set_author(landlord.name)
    pdf.add_page()
    pdf.set_margins(left=20, top=20, right=20)
    pdf.set_y(20)

    pdf.set_font(_FONT, style="B", size=22)
    pdf.cell(0, 12, "Rent Receipt", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(_FONT, size=11)
    pdf.cell(0, 6, f"Receipt no. {payment.receipt_id}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(6)

    snapshot = payment.snapshot
    rows = [
        ("Date received", payment.received_on.strftime("%B %d, %Y")),
        ("Received from", tenant.name),
        ("Property", tenant.property_address),
        ("Rent period", payment.period.label),
        ("Payment method", payment.method),
        ("Rent", format_cents(snapshot.rent_cents)),
    ]
    if snapshot.late_fee_cents:
        rows.append(("Late fee", format_cents(snapshot.late_fee_cents)))
    if snapshot.paid_before_cents:
        rows.append(("Previously paid", format_cents(snapshot.paid_before_cents)))
    for label, value in rows:
        _row(pdf, label, value)

    pdf.ln(2)
    _row(pdf, "Amount paid", format_cents(payment.amount_cents), size=14, bold=True)
    _row(pdf, "Balance for period", balance_text(snapshot.balance_after_cents))

    if payment.note:
        pdf.ln(2)
        _row(pdf, "Note", payment.note)

    pdf.ln(12)
    pdf.set_font(_FONT, size=11)
    pdf.cell(0, 6, f"Received by: {landlord.name}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    contact = landlord.email if landlord.phone is None else f"{landlord.email}  |  {landlord.phone}"
    pdf.cell(0, 6, contact, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(10)
    pdf.set_font(_FONT, style="I", size=9)
    pdf.set_text_color(110, 110, 110)
    pdf.multi_cell(
        0,
        5,
        "Thank you for your payment. Please keep this receipt for your records.",
    )

    return bytes(pdf.output())


def _row(pdf: FPDF, label: str, value: str, *, size: int = 11, bold: bool = False) -> None:
    pdf.set_font(_FONT, style="B", size=size)
    pdf.cell(_LABEL_WIDTH, 8, label)
    pdf.set_font(_FONT, style="B" if bold else "", size=size)
    pdf.multi_cell(0, 8, value, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
