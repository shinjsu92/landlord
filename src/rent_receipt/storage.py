"""SQLite storage for payments, late fee waivers and sent reminders.

Each payment row is also its receipt. The row's ``receipt_number`` is assigned
by SQLite with AUTOINCREMENT, so numbers only ever go up and are never reused.
Each row also stores what was owed before the payment and the balance after it,
so re-sending an old receipt always reproduces it exactly, even after the rent
in the config changes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from rent_receipt.period import Period

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    receipt_number       INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_email         TEXT    NOT NULL,
    amount_cents         INTEGER NOT NULL CHECK (amount_cents > 0),
    received_on          TEXT    NOT NULL,  -- ISO date
    method               TEXT    NOT NULL,
    note                 TEXT,
    balance_before_cents INTEGER NOT NULL,
    balance_after_cents  INTEGER NOT NULL,
    open_items           TEXT    NOT NULL,  -- JSON [[description, cents], ...] before it
    created_at           TEXT    NOT NULL,  -- ISO timestamp with offset
    emailed_at           TEXT               -- NULL until the email is sent
);
CREATE INDEX IF NOT EXISTS payments_by_tenant ON payments (tenant_email, received_on);

CREATE TABLE IF NOT EXISTS late_fee_waivers (
    tenant_email TEXT NOT NULL,
    period       TEXT NOT NULL,  -- YYYY-MM
    waived_at    TEXT NOT NULL,
    PRIMARY KEY (tenant_email, period)
);

CREATE TABLE IF NOT EXISTS reminders_sent (
    kind    TEXT NOT NULL,
    period  TEXT NOT NULL,  -- YYYY-MM
    sent_at TEXT NOT NULL,
    PRIMARY KEY (kind, period)
);
"""


@dataclass(frozen=True)
class NewPayment:
    """A payment that has not been saved yet."""

    tenant_email: str
    amount_cents: int
    received_on: date
    method: str
    note: str | None


@dataclass(frozen=True)
class SnapshotItem:
    description: str
    amount_cents: int


@dataclass(frozen=True)
class Snapshot:
    """The account at the moment a payment is recorded."""

    owed_before: tuple[SnapshotItem, ...]
    """Unpaid charges before this payment, oldest first."""
    balance_before_cents: int
    """Can be lower than the sum of ``owed_before`` when the tenant had a credit."""
    balance_after_cents: int


@dataclass(frozen=True)
class Payment:
    receipt_number: int
    tenant_email: str
    amount_cents: int
    received_on: date
    method: str
    note: str | None
    snapshot: Snapshot
    created_at: datetime
    emailed_at: datetime | None

    @property
    def receipt_id(self) -> str:
        """Printed receipt number, e.g. ``R-000007``."""
        return f"R-{self.receipt_number:06d}"


SnapshotFn = Callable[[list[Payment]], Snapshot]
"""Given the tenant's saved payments, compute the new payment's snapshot."""


class PaymentStore:
    """Thread-safe access to the database.

    The Discord bot runs blocking work in worker threads, so one lock guards
    the shared connection.
    """

    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: we open transactions ourselves with BEGIN.
        self._connection = sqlite3.connect(
            database_path, check_same_thread=False, isolation_level=None
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._migrate()
            self._connection.executescript(_SCHEMA)
            self._connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def payments(self, tenant_email: str) -> list[Payment]:
        with self._lock:
            return self._payments(tenant_email)

    def add(self, payment: NewPayment, snapshot_fn: SnapshotFn, created_at: datetime) -> Payment:
        """Save a payment, computing its snapshot from the tenant's saved payments.

        Reading the saved payments and inserting happen in one transaction, so
        the stored balance always matches what was recorded before it.
        """
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                snapshot = snapshot_fn(self._payments(payment.tenant_email))
                cursor = self._connection.execute(
                    "INSERT INTO payments (tenant_email, amount_cents, received_on, method,"
                    " note, balance_before_cents, balance_after_cents, open_items, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        payment.tenant_email,
                        payment.amount_cents,
                        payment.received_on.isoformat(),
                        payment.method,
                        payment.note,
                        snapshot.balance_before_cents,
                        snapshot.balance_after_cents,
                        json.dumps([[i.description, i.amount_cents] for i in snapshot.owed_before]),
                        created_at.isoformat(),
                    ),
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            receipt_number = cursor.lastrowid
            assert receipt_number is not None
            saved = self._get(receipt_number)
        assert saved is not None
        return saved

    def mark_emailed(self, receipt_number: int, emailed_at: datetime) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE payments SET emailed_at = ? WHERE receipt_number = ?",
                (emailed_at.isoformat(), receipt_number),
            )

    def get(self, receipt_number: int) -> Payment | None:
        with self._lock:
            return self._get(receipt_number)

    def recent(self, limit: int = 5) -> list[Payment]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM payments ORDER BY receipt_number DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_to_payment(row) for row in rows]

    def waived_periods(self, tenant_email: str) -> set[Period]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT period FROM late_fee_waivers WHERE tenant_email = ?", (tenant_email,)
            ).fetchall()
        return {Period.parse(row["period"]) for row in rows}

    def waive_late_fee(self, tenant_email: str, period: Period, waived_at: datetime) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR IGNORE INTO late_fee_waivers (tenant_email, period, waived_at)"
                " VALUES (?, ?, ?)",
                (tenant_email, period.key, waived_at.isoformat()),
            )

    def reminder_sent(self, kind: str, period: Period) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM reminders_sent WHERE kind = ? AND period = ?",
                (kind, period.key),
            ).fetchone()
        return row is not None

    def record_reminder(self, kind: str, period: Period, sent_at: datetime) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR IGNORE INTO reminders_sent (kind, period, sent_at) VALUES (?, ?, ?)",
                (kind, period.key, sent_at.isoformat()),
            )

    # The helpers below expect the caller to hold ``self._lock``.

    def _migrate(self) -> None:
        """Move aside payments saved by the first, per-month version of the bot.

        Those rows have no account balance, so they can't be carried over. The
        old table is kept as ``payments_v1`` for reference, not deleted.
        """
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        has_payments = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'payments'"
        ).fetchone()
        if version < _SCHEMA_VERSION and has_payments:
            logger.warning(
                "Found payments from the old per-month format. They were moved to the "
                "payments_v1 table and are no longer counted."
            )
            self._connection.execute("DROP INDEX IF EXISTS payments_by_period")
            self._connection.execute("ALTER TABLE payments RENAME TO payments_v1")

    def _payments(self, tenant_email: str) -> list[Payment]:
        rows = self._connection.execute(
            "SELECT * FROM payments WHERE tenant_email = ? ORDER BY received_on, receipt_number",
            (tenant_email,),
        ).fetchall()
        return [_to_payment(row) for row in rows]

    def _get(self, receipt_number: int) -> Payment | None:
        row = self._connection.execute(
            "SELECT * FROM payments WHERE receipt_number = ?", (receipt_number,)
        ).fetchone()
        return None if row is None else _to_payment(row)


def _to_payment(row: sqlite3.Row) -> Payment:
    emailed_at = row["emailed_at"]
    return Payment(
        receipt_number=row["receipt_number"],
        tenant_email=row["tenant_email"],
        amount_cents=row["amount_cents"],
        received_on=date.fromisoformat(row["received_on"]),
        method=row["method"],
        note=row["note"],
        snapshot=Snapshot(
            owed_before=tuple(
                SnapshotItem(description, cents)
                for description, cents in json.loads(row["open_items"])
            ),
            balance_before_cents=row["balance_before_cents"],
            balance_after_cents=row["balance_after_cents"],
        ),
        created_at=datetime.fromisoformat(row["created_at"]),
        emailed_at=None if emailed_at is None else datetime.fromisoformat(emailed_at),
    )
