"""Render a payment as a one-page PDF receipt.

Uses fpdf2, which is pure Python, so nothing has to be installed on the server
outside this app's own virtual environment. The built-in Helvetica font covers
English (Latin-1) text only; see the README if you need other alphabets.

Layout, top to bottom: a header band with the landlord's contact details, a row
of key facts (receipt number, date, method), who paid and for which property, a
table of what was owed, what this payment paid off (oldest first) and what is
still owed, the amount paid with a paid-in-full or balance-due badge, an
optional note, and a footer.
"""

from __future__ import annotations

from fpdf import FPDF
from fpdf.enums import Align, XPos, YPos

from rent_receipt.config import Landlord, Tenant
from rent_receipt.money import format_cents
from rent_receipt.storage import Payment

_FONT = "Helvetica"
_MARGIN = 18.0
_HEADER_HEIGHT = 34.0

Color = tuple[int, int, int]
_NAVY: Color = (28, 43, 74)
_INK: Color = (33, 37, 41)
_MUTED: Color = (108, 117, 125)
_RULE: Color = (222, 226, 230)
_PANEL: Color = (245, 247, 250)
_WHITE: Color = (255, 255, 255)
_GREEN: Color = (25, 135, 84)
_AMBER: Color = (176, 106, 0)
_BLUE: Color = (13, 110, 253)


def render_receipt(payment: Payment, landlord: Landlord, tenant: Tenant) -> bytes:
    """Return the PDF bytes for ``payment``."""
    pdf = FPDF(format="Letter")
    pdf.set_title(f"Rent receipt {payment.receipt_id}")
    pdf.set_author(landlord.name)
    pdf.set_margins(left=_MARGIN, top=_MARGIN, right=_MARGIN)
    pdf.set_auto_page_break(auto=True, margin=_MARGIN)
    pdf.add_page()

    _header(pdf, landlord)
    _key_facts(pdf, payment)
    _parties(pdf, landlord, tenant)
    _line_items(pdf, payment)
    _amount_paid(pdf, payment)
    if payment.note:
        _note(pdf, payment.note)
    _footer(pdf, payment)

    return bytes(pdf.output())


def _header(pdf: FPDF, landlord: Landlord) -> None:
    pdf.set_fill_color(*_NAVY)
    pdf.rect(0, 0, pdf.w, _HEADER_HEIGHT, style="F")

    pdf.set_text_color(*_WHITE)
    pdf.set_xy(_MARGIN, 10)
    pdf.set_font(_FONT, style="B", size=22)
    pdf.cell(100, 10, "RENT RECEIPT")
    pdf.set_xy(_MARGIN, 21)
    pdf.set_font(_FONT, size=9)
    pdf.cell(100, 5, "Proof of payment")

    contact = [landlord.email] + ([landlord.phone] if landlord.phone else [])
    right_x = pdf.w - _MARGIN - 90
    pdf.set_xy(right_x, 10)
    pdf.set_font(_FONT, style="B", size=11)
    pdf.cell(90, 6, landlord.name, align=Align.R)
    pdf.set_font(_FONT, size=9)
    for i, line in enumerate(contact):
        pdf.set_xy(right_x, 17 + i * 5)
        pdf.cell(90, 5, line, align=Align.R)

    pdf.set_text_color(*_INK)
    pdf.set_y(_HEADER_HEIGHT + 10)


def _key_facts(pdf: FPDF, payment: Payment) -> None:
    facts = [
        ("RECEIPT NO.", payment.receipt_id),
        ("DATE RECEIVED", payment.received_on.strftime("%B %d, %Y").replace(" 0", " ")),
        ("PAYMENT METHOD", payment.method),
    ]
    width = (pdf.w - 2 * _MARGIN) / len(facts)
    top = pdf.get_y()
    pdf.set_fill_color(*_PANEL)
    pdf.rect(_MARGIN, top, pdf.w - 2 * _MARGIN, 18, style="F")
    for i, (label, value) in enumerate(facts):
        x = _MARGIN + i * width + 5
        pdf.set_xy(x, top + 3)
        _label(pdf, label, width - 10)
        pdf.set_xy(x, top + 8.5)
        pdf.set_font(_FONT, style="B", size=11)
        pdf.cell(width - 10, 6, value)
    pdf.set_y(top + 18 + 9)


def _parties(pdf: FPDF, landlord: Landlord, tenant: Tenant) -> None:
    half = (pdf.w - 2 * _MARGIN) / 2
    top = pdf.get_y()

    pdf.set_xy(_MARGIN, top)
    _label(pdf, "RECEIVED FROM", half)
    pdf.set_xy(_MARGIN, top + 5)
    pdf.set_font(_FONT, style="B", size=11)
    pdf.multi_cell(half - 6, 5.5, tenant.name, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(_FONT, size=10)
    pdf.multi_cell(half - 6, 5, tenant.property_address, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    left_bottom = pdf.get_y()

    pdf.set_xy(_MARGIN + half, top)
    _label(pdf, "RECEIVED BY", half)
    pdf.set_xy(_MARGIN + half, top + 5)
    pdf.set_font(_FONT, style="B", size=11)
    pdf.multi_cell(half, 5.5, landlord.name)
    pdf.set_x(_MARGIN + half)
    pdf.set_font(_FONT, size=10)
    pdf.multi_cell(half, 5, "Landlord")
    right_bottom = pdf.get_y()

    pdf.set_y(max(left_bottom, right_bottom) + 9)


def _line_items(pdf: FPDF, payment: Payment) -> None:
    snapshot = payment.snapshot
    width = pdf.w - 2 * _MARGIN
    amount_width = 32.0
    description_width = width - 3 * amount_width
    row_height = 8.0

    def row(cells: tuple[str, str, str, str], *, border: str = "", fill: bool = False) -> None:
        pdf.cell(description_width, row_height, f"  {cells[0]}", border=border, fill=fill)
        for text in cells[1:]:
            pdf.cell(amount_width, row_height, f"{text}  ", border=border, fill=fill, align=Align.R)
        pdf.ln(row_height)

    pdf.set_fill_color(*_NAVY)
    pdf.set_text_color(*_WHITE)
    pdf.set_font(_FONT, style="B", size=9)
    row(("OWED BEFORE THIS PAYMENT", "OWED", "PAID NOW", "STILL OWED"), fill=True)
    pdf.set_text_color(*_INK)

    pdf.set_font(_FONT, size=10)
    pdf.set_draw_color(*_RULE)
    unapplied = payment.amount_cents
    for item in snapshot.owed_before:
        applied = min(item.amount_cents, unapplied)
        unapplied -= applied
        row(
            (
                item.description,
                format_cents(item.amount_cents),
                format_cents(applied) if applied else "-",
                format_cents(item.amount_cents - applied),
            ),
            border="B",
        )
    if snapshot.balance_before_cents < 0:
        credit = format_cents(-snapshot.balance_before_cents)
        row(("Credit already on account", f"-{credit}", "-", f"-{credit}"), border="B")
    if unapplied > 0 and snapshot.balance_after_cents < 0:
        row(("Credit toward future rent", "-", format_cents(unapplied), "-"), border="B")
    if not snapshot.owed_before and snapshot.balance_before_cents >= 0:
        row(("Nothing was owed", "-", "-", "-"), border="B")

    pdf.set_font(_FONT, style="B", size=10)
    pdf.set_fill_color(*_PANEL)
    row(
        (
            "Total",
            _balance_amount(snapshot.balance_before_cents),
            format_cents(payment.amount_cents),
            _balance_amount(snapshot.balance_after_cents),
        ),
        fill=True,
    )
    pdf.ln(9)


def _amount_paid(pdf: FPDF, payment: Payment) -> None:
    top = pdf.get_y()
    width = pdf.w - 2 * _MARGIN
    height = 22.0
    pdf.set_draw_color(*_NAVY)
    pdf.set_line_width(0.6)
    pdf.rect(_MARGIN, top, width, height, style="D")
    pdf.set_line_width(0.2)

    pdf.set_xy(_MARGIN + 6, top + 4)
    _label(pdf, "AMOUNT PAID", 80)
    pdf.set_xy(_MARGIN + 6, top + 9)
    pdf.set_font(_FONT, style="B", size=20)
    pdf.set_text_color(*_NAVY)
    pdf.cell(80, 10, format_cents(payment.amount_cents))

    text, color = _status(payment.snapshot.balance_after_cents)
    pdf.set_font(_FONT, style="B", size=10)
    badge_width = pdf.get_string_width(text) + 12
    badge_x = _MARGIN + width - 6 - badge_width
    pdf.set_fill_color(*color)
    pdf.set_text_color(*_WHITE)
    pdf.set_xy(badge_x, top + (height - 9) / 2)
    pdf.cell(badge_width, 9, text, fill=True, align=Align.C)

    pdf.set_text_color(*_INK)
    pdf.set_y(top + height + 8)


def _note(pdf: FPDF, note: str) -> None:
    _label(pdf, "NOTE", 0)
    pdf.ln(5)
    pdf.set_font(_FONT, size=10)
    pdf.multi_cell(0, 5, note, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(6)


def _footer(pdf: FPDF, payment: Payment) -> None:
    pdf.set_y(-_MARGIN - 16)
    pdf.set_draw_color(*_RULE)
    pdf.line(_MARGIN, pdf.get_y(), pdf.w - _MARGIN, pdf.get_y())
    pdf.ln(4)
    pdf.set_font(_FONT, style="B", size=10)
    pdf.set_text_color(*_INK)
    pdf.cell(0, 5, "Thank you for your payment.", align=Align.C)
    pdf.ln(5)
    pdf.set_font(_FONT, size=8)
    pdf.set_text_color(*_MUTED)
    pdf.cell(
        0,
        5,
        f"Please keep this receipt for your records. Receipt {payment.receipt_id} "
        f"was issued on {payment.created_at.strftime('%B %d, %Y').replace(' 0', ' ')}.",
        align=Align.C,
    )


def _label(pdf: FPDF, text: str, width: float) -> None:
    pdf.set_font(_FONT, style="B", size=7.5)
    pdf.set_text_color(*_MUTED)
    pdf.cell(width, 4, text)
    pdf.set_text_color(*_INK)


def _balance_amount(balance_cents: int) -> str:
    if balance_cents < 0:
        return f"{format_cents(-balance_cents)} credit"
    return format_cents(balance_cents)


def _status(balance_cents: int) -> tuple[str, Color]:
    """Badge text and color for the balance left after this payment."""
    if balance_cents == 0:
        return "PAID IN FULL", _GREEN
    if balance_cents > 0:
        return f"BALANCE DUE {format_cents(balance_cents)}", _AMBER
    return f"CREDIT {format_cents(-balance_cents)}", _BLUE
