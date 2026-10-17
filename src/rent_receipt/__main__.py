"""Start the bot: ``python -m rent_receipt --config /path/to/config.toml``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rent_receipt.bot import ReceiptBot
from rent_receipt.config import ConfigError, load_settings
from rent_receipt.emailer import SmtpEmailSender
from rent_receipt.reminders import ReminderService
from rent_receipt.service import ReceiptService
from rent_receipt.storage import PaymentStore

DEFAULT_CONFIG_PATH = Path("/etc/rent-receipt-bot/config.toml")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rent receipt Discord bot")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        sys.exit(f"Configuration problem: {exc}")

    store = PaymentStore(settings.data_dir / "payments.sqlite3")
    sender = SmtpEmailSender(settings.smtp)
    receipts = ReceiptService(settings, store, sender)
    reminders = ReminderService(settings, store, receipts, sender)
    bot = ReceiptBot(settings, receipts, reminders)
    try:
        # log_handler=None keeps the logging setup above instead of discord.py's own.
        bot.run(settings.discord.token, log_handler=None)
    finally:
        store.close()


if __name__ == "__main__":
    main()
