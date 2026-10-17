"""SQLite storage for payments and sent reminders.

Each payment row is also its receipt. The row's ``receipt_number`` is assigned
by SQLite with AUTOINCREMENT, so numbers only ever go up and are never reused.
Each row also stores the rent, late fee and balance as they were when the
payment was recorded, so re-sending an old receipt always reproduces it
exactly, even after the rent in the config changes.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from rent_receipt.period import Period

_SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    receipt_number      INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_email        TEXT    NOT NULL,
    amount_cents        INTEGER NOT NULL CHECK (amount_cents > 0),
    period              TEXT    NOT NULL,  -- YYYY-MM
    received_on         TEXT    NOT NULL,  -- ISO date
    method              TEXT    NOT NULL,
    note                TEXT,
    waive_late_fee      INTEGER NOT NULL DEFAULT 0,
    rent_cents          INTEGER NOT NULL,
    late_fee_cents      INTEGER NOT NULL,
    paid_before_cents   INTEGER NOT NULL,
    balance_after_cents INTEGER NOT NULL,
    created_at          TEXT    NOT NULL,  -- ISO timestamp with offset
    emailed_at          TEXT               -- NULL until the email is sent
);
CREATE INDEX IF NOT EXISTS payments_by_period ON payments (tenant_email, period);

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
    period: Period
    received_on: date
    method: str
    note: str | None
    waive_late_fee: bool = False


@dataclass(frozen=True)
class Snapshot:
    """Amounts for the period at the moment a payment is recorded."""

    rent_cents: int
    late_fee_cents: int
    paid_before_cents: int
    balance_after_cents: int


@dataclass(frozen=True)
class Payment:
    receipt_number: int
    tenant_email: str
    amount_cents: int
    period: Period
    received_on: date
    method: str
    note: str | None
    waive_late_fee: bool
    snapshot: Snapshot
    created_at: datetime
    emailed_at: datetime | None

    @property
    def receipt_id(self) -> str:
        """Printed receipt number, e.g. ``R-000007``."""
        return f"R-{self.receipt_number:06d}"


SnapshotFn = Callable[[list[Payment]], Snapshot]
"""Given the period's earlier payments, compute the new payment's snapshot."""


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
            self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def payments_for(self, tenant_email: str, period: Period) -> list[Payment]:
        with self._lock:
            return self._payments_for(tenant_email, period)

    def add(self, payment: NewPayment, snapshot_fn: SnapshotFn, created_at: datetime) -> Payment:
        """Save a payment, computing its snapshot from the period's earlier payments.

        Reading the earlier payments and inserting happen in one transaction,
        so the stored balance always matches what was recorded before it.
        """
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                earlier = self._payments_for(payment.tenant_email, payment.period)
                snapshot = snapshot_fn(earlier)
                cursor = self._connection.execute(
                    "INSERT INTO payments (tenant_email, amount_cents, period, received_on,"
                    " method, note, waive_late_fee, rent_cents, late_fee_cents,"
                    " paid_before_cents, balance_after_cents, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        payment.tenant_email,
                        payment.amount_cents,
                        payment.period.key,
                        payment.received_on.isoformat(),
                        payment.method,
                        payment.note,
                        int(payment.waive_late_fee),
                        snapshot.rent_cents,
                        snapshot.late_fee_cents,
                        snapshot.paid_before_cents,
                        snapshot.balance_after_cents,
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

    def _payments_for(self, tenant_email: str, period: Period) -> list[Payment]:
        rows = self._connection.execute(
            "SELECT * FROM payments WHERE tenant_email = ? AND period = ? ORDER BY receipt_number",
            (tenant_email, period.key),
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
        period=Period.parse(row["period"]),
        received_on=date.fromisoformat(row["received_on"]),
        method=row["method"],
        note=row["note"],
        waive_late_fee=bool(row["waive_late_fee"]),
        snapshot=Snapshot(
            rent_cents=row["rent_cents"],
            late_fee_cents=row["late_fee_cents"],
            paid_before_cents=row["paid_before_cents"],
            balance_after_cents=row["balance_after_cents"],
        ),
        created_at=datetime.fromisoformat(row["created_at"]),
        emailed_at=None if emailed_at is None else datetime.fromisoformat(emailed_at),
    )
