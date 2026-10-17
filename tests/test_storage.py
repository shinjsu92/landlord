import sqlite3
from pathlib import Path

from rent_receipt.storage import PaymentStore


def test_old_per_month_payments_are_moved_aside(tmp_path: Path) -> None:
    path = tmp_path / "payments.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE payments (receipt_number INTEGER PRIMARY KEY AUTOINCREMENT,"
        " period TEXT NOT NULL);"
        "CREATE INDEX payments_by_period ON payments (period);"
        "INSERT INTO payments (period) VALUES ('2026-10');"
    )
    old.close()

    store = PaymentStore(path)
    assert store.recent() == []
    store.close()

    check = sqlite3.connect(path)
    assert check.execute("SELECT COUNT(*) FROM payments_v1").fetchone() == (1,)
    assert check.execute("PRAGMA user_version").fetchone() == (2,)
    check.close()
    PaymentStore(path).close()  # Opening again leaves things as they are.
