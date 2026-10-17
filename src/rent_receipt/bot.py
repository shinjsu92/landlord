"""Discord front end: slash commands, confirm buttons and the reminder loop.

Only the configured owner can use the bot; everyone else is refused. All
blocking work (database, PDF, email) runs in a worker thread so the bot stays
responsive.
"""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import date

import discord
from discord import app_commands
from discord.ext import tasks

from rent_receipt.config import PAYMENT_METHODS, Settings
from rent_receipt.ledger import AccountStatus, balance_text
from rent_receipt.money import InvalidAmountError, format_cents, parse_amount
from rent_receipt.period import InvalidPeriodError, Period
from rent_receipt.reminders import ReminderService, owed_lines
from rent_receipt.service import (
    Draft,
    InvalidPaymentDateError,
    IssuedReceipt,
    ReceiptNotFoundError,
    ReceiptService,
)

logger = logging.getLogger(__name__)

_CONFIRM_TIMEOUT_SECONDS = 15 * 60
_REMINDER_CHECK_MINUTES = 10
_METHOD_CHOICES = [app_commands.Choice(name=m, value=m) for m in PAYMENT_METHODS]


class OwnerOnlyTree(app_commands.CommandTree[discord.Client]):
    """Command tree that refuses anyone except the owner."""

    def __init__(self, client: discord.Client, owner_user_id: int) -> None:
        super().__init__(client)
        self._owner_user_id = owner_user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self._owner_user_id:
            return True
        await interaction.response.send_message("Sorry, this bot is private.", ephemeral=True)
        return False


class ReceiptBot(discord.Client):
    def __init__(
        self, settings: Settings, receipts: ReceiptService, reminders: ReminderService
    ) -> None:
        # Slash commands and buttons need no privileged intents.
        super().__init__(intents=discord.Intents.none())
        self.settings = settings
        self.receipts = receipts
        self.reminders = reminders
        self.tree = OwnerOnlyTree(self, settings.discord.owner_user_id)
        _register_commands(self)

    async def setup_hook(self) -> None:
        # Registering commands to one server makes them appear instantly;
        # global registration can take up to an hour.
        guild = discord.Object(id=self.settings.discord.guild_id)
        self.tree.copy_global_to(guild=guild)
        try:
            await self.tree.sync(guild=guild)
        except discord.Forbidden:
            logger.error(
                "Discord refused to add the slash commands to server %s. Invite the bot "
                "to that server with both the 'bot' and 'applications.commands' scopes, "
                "and check that discord.guild_id in the config is that server's ID.",
                guild.id,
            )
            raise
        self.reminder_loop.start()

    async def close(self) -> None:
        self.reminder_loop.cancel()
        await super().close()

    async def on_ready(self) -> None:
        logger.info("Logged in as %s", self.user)

    async def notify_owner(self, text: str) -> None:
        owner = await self.fetch_user(self.settings.discord.owner_user_id)
        await owner.send(text)

    @tasks.loop(minutes=_REMINDER_CHECK_MINUTES)
    async def reminder_loop(self) -> None:
        try:
            messages = await asyncio.to_thread(self.reminders.run_due_reminders)
            for text in messages:
                await self.notify_owner(text)
        except Exception:  # Keep the loop alive; the next tick tries again.
            logger.exception("Reminder check failed")

    @reminder_loop.before_loop
    async def _wait_until_ready(self) -> None:
        await self.wait_until_ready()


class ConfirmView(discord.ui.View):
    """Send / Cancel buttons under a receipt preview."""

    def __init__(self, receipts: ReceiptService, draft: Draft) -> None:
        super().__init__(timeout=_CONFIRM_TIMEOUT_SECONDS)
        self._receipts = receipts
        self._draft = draft
        self._done = False

    @discord.ui.button(label="Send receipt", style=discord.ButtonStyle.success)
    async def send(
        self, interaction: discord.Interaction, _button: discord.ui.Button[ConfirmView]
    ) -> None:
        if self._done:  # Guards against a double tap issuing two receipts.
            await interaction.response.defer()
            return
        self._done = True
        self.stop()
        await interaction.response.edit_message(view=None)
        issued = await asyncio.to_thread(self._receipts.issue, self._draft)
        await interaction.followup.send(_issued_text(issued), file=_pdf_file(issued))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, _button: discord.ui.Button[ConfirmView]
    ) -> None:
        self._done = True
        self.stop()
        await interaction.response.edit_message(content="Cancelled. Nothing was saved.", view=None)


def _register_commands(bot: ReceiptBot) -> None:
    receipts = bot.receipts

    @bot.tree.command(name="received", description="Record a rent payment and email a receipt")
    @app_commands.describe(
        amount="Amount received, e.g. 1000 or 1000.50",
        method="How it was paid (defaults to the tenant's usual method)",
        received_on="Day you received it as YYYY-MM-DD (defaults to today)",
        note="Optional note for the receipt, e.g. a check number",
    )
    @app_commands.rename(received_on="date")
    @app_commands.choices(method=_METHOD_CHOICES)
    async def received(
        interaction: discord.Interaction,
        amount: str,
        method: app_commands.Choice[str] | None = None,
        received_on: str | None = None,
        note: str | None = None,
    ) -> None:
        try:
            amount_cents = parse_amount(amount)
            draft = await asyncio.to_thread(
                receipts.prepare,
                amount_cents,
                _parse_date(received_on) if received_on else None,
                method.value if method else None,
                note,
            )
        except (InvalidAmountError, InvalidPaymentDateError, ValueError) as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        await interaction.response.send_message(
            receipts.describe(draft), view=ConfirmView(receipts, draft)
        )

    @bot.tree.command(name="balance", description="Show what the tenant owes")
    async def balance(interaction: discord.Interaction) -> None:
        status = await asyncio.to_thread(receipts.status)
        await interaction.response.send_message(_balance_text(status))

    @bot.tree.command(name="waive_late_fee", description="Remove the late fee for a month")
    @app_commands.describe(month="Month as YYYY-MM (defaults to this month)")
    async def waive_late_fee(interaction: discord.Interaction, month: str | None = None) -> None:
        try:
            period = Period.parse(month) if month else None
        except InvalidPeriodError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        status = await asyncio.to_thread(receipts.waive_late_fee, period)
        label = (period or Period.containing(status.as_of)).label
        await interaction.response.send_message(
            f"👍 The late fee for {label} is waived.\n{_balance_text(status)}"
        )

    @bot.tree.command(name="history", description="Show the latest receipts")
    async def history(interaction: discord.Interaction) -> None:
        payments = await asyncio.to_thread(receipts.recent, 10)
        if not payments:
            await interaction.response.send_message("No payments recorded yet.")
            return
        lines = [
            f"`{p.receipt_id}` {p.received_on:%b %d, %Y}: {format_cents(p.amount_cents)} "
            f"({p.method}), balance after {balance_text(p.snapshot.balance_after_cents)}"
            + ("" if p.emailed_at else " ⚠️ not emailed")
            for p in payments
        ]
        await interaction.response.send_message("\n".join(lines))

    @bot.tree.command(name="resend", description="Email a receipt to the tenant again")
    @app_commands.describe(number="Receipt number, e.g. 7 for R-000007")
    async def resend(interaction: discord.Interaction, number: app_commands.Range[int, 1]) -> None:
        await interaction.response.defer(thinking=True)
        try:
            issued = await asyncio.to_thread(receipts.resend, number)
        except ReceiptNotFoundError as exc:
            await interaction.followup.send(f"⚠️ {exc}")
            return
        await interaction.followup.send(_issued_text(issued), file=_pdf_file(issued))

    @bot.tree.command(
        name="late_notice", description="Email the tenant a late notice for the balance now"
    )
    async def late_notice(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        text = await asyncio.to_thread(bot.reminders.send_late_notice)
        await interaction.followup.send(text)


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise ValueError(f"{text!r} is not a date. Use YYYY-MM-DD, e.g. 2026-10-03.") from None


def _balance_text(status: AccountStatus) -> str:
    if status.balance_cents <= 0:
        return f"**Balance: {balance_text(status.balance_cents)}**"
    return f"**Balance: {format_cents(status.balance_cents)}**\n{owed_lines(status)}"


def _issued_text(issued: IssuedReceipt) -> str:
    payment = issued.payment
    if issued.emailed:
        return (
            f"✅ Receipt {payment.receipt_id} emailed to the tenant (copy to you). "
            f"Balance: {balance_text(payment.snapshot.balance_after_cents)}."
        )
    return (
        f"⚠️ Receipt {payment.receipt_id} was saved, but the email failed: "
        f"{issued.email_error}\nFix the email settings, then use "
        f"`/resend number:{payment.receipt_number}`."
    )


def _pdf_file(issued: IssuedReceipt) -> discord.File:
    return discord.File(io.BytesIO(issued.pdf_bytes), filename=issued.pdf_path.name)
