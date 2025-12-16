"""
Database schema and operations for hgt-bookkeeper.

SQLite-based storage for canonical transaction data.
Source-agnostic design supports multiple importers and exporters.
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass

# EPOCH date helpers
def to_epoch(dt: datetime) -> int: return int(dt.timestamp())

def from_epoch(epoch: int) -> datetime.datetime: return datetime.fromtimestamp(epoch, tz=timezone.utc)

def now_epoch() -> int: return to_epoch(datetime.now(timezone.utc))

# Canonical transaction types (normalized across all sources)
TRANSACTION_TYPES = {
    "revenue",  # Money earned (charges, sales, payments)
    "platform_fee",  # Monthly/billing fees (separate from transaction fees)
    "payout",  # Money moved to bank
    "refund",  # Money returned to customer
    "adjustment",  # Catch-all for corrections, disputes, etc.
}

# Income categories (for revenue transactions)
INCOME_CATEGORIES = {
    "subscription",  # Recurring subscription payments
    "invoice",  # One-time invoice payments
    "other",  # Anything else
}

SCHEMA = """
-- Core transactions table (canonical, source-agnostic)
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,                    -- Internal UUID
    source TEXT NOT NULL,                   -- 'stripe', 'paypal', 'square', etc.
    source_id TEXT NOT NULL,                -- Original ID from source (txn_abc123)
    source_type TEXT,                       -- Raw type from source ('charge', 'stripe_fee', etc.)
    
    date INTEGER NOT NULL,                     -- Transaction date (ISO 8601)
    type TEXT NOT NULL,                     -- Canonical type: revenue, platform_fee, payout, refund, adjustment
    income_category TEXT,                   -- For revenue: subscription, invoice, other (NULL for non-revenue)
    description TEXT,                       -- Raw description from source
    
    gross REAL,                             -- Gross amount (revenue transactions)
    fees REAL,                              -- Processing fees deducted (revenue transactions)
    net REAL NOT NULL,                      -- Net amount (positive = money in, negative = money out)
    currency TEXT NOT NULL DEFAULT 'usd',   -- ISO currency code
    
    available_on INTEGER,                      -- When funds become available (for payout matching)
    payout_id TEXT,                         -- Links revenue to its payout (NULL until paid out)
    
    created_at INTEGER NOT NULL,               -- When we imported this record
    metadata TEXT,                          -- JSON blob for source-specific extras
    exported_gnucash INTEGER,                  -- Timestamp when exported to GnuCash (NULL = not exported)
    
    UNIQUE(source, source_id)               -- Prevent duplicate imports
);

-- Tax calculations for revenue transactions
CREATE TABLE IF NOT EXISTS tax_calculations (
    transaction_id TEXT PRIMARY KEY,
    fica_employee REAL NOT NULL,
    fica_employer REAL NOT NULL,
    federal REAL NOT NULL,
    state REAL NOT NULL,
    total REAL NOT NULL,
    calculated_at INTEGER NOT NULL,
    
    FOREIGN KEY (transaction_id) REFERENCES transactions(id)
);

-- Payout linkages (which transactions were included in which payout)
CREATE TABLE IF NOT EXISTS payout_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payout_id TEXT NOT NULL,                -- References transactions.id where type='payout'
    transaction_id TEXT NOT NULL,           -- References transactions.id (revenue being paid out)
    
    FOREIGN KEY (payout_id) REFERENCES transactions(id),
    FOREIGN KEY (transaction_id) REFERENCES transactions(id),
    UNIQUE(payout_id, transaction_id)
);

-- Processing state (tracks what's been exported)
CREATE TABLE IF NOT EXISTS export_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exporter TEXT NOT NULL,                 -- 'gnucash', 'beancount', etc.
    start_date INTEGER NOT NULL,
    end_date INTEGER NOT NULL,
    transaction_count INTEGER NOT NULL,
    exported_at INTEGER NOT NULL,
    output_file TEXT                        -- Path to generated file
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(type);
CREATE INDEX IF NOT EXISTS idx_transactions_source ON transactions(source);
CREATE INDEX IF NOT EXISTS idx_transactions_available_on ON transactions(available_on);
CREATE INDEX IF NOT EXISTS idx_transactions_payout_id ON transactions(payout_id);
"""


@dataclass
class Transaction:
    """Canonical transaction record."""

    id: str
    source: str
    source_id: str
    source_type: Optional[str]
    date: int
    type: str
    income_category: Optional[str]
    description: Optional[str]
    gross: Optional[float]
    fees: Optional[float]
    net: float
    currency: str
    available_on: Optional[int]
    payout_id: Optional[str]
    created_at: int
    metadata: Optional[str]
    exported_gnucash: Optional[int] = None


@dataclass
class TaxCalculation:
    """Tax calculation for a revenue transaction."""

    transaction_id: str
    fica_employee: float
    fica_employer: float
    federal: float
    state: float
    total: float
    calculated_at: int


class Database:
    """SQLite database operations for hgt-bookkeeper."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._ensure_directory()
        self._connection: Optional[sqlite3.Connection] = None

    def _ensure_directory(self):
        """Create parent directory if it doesn't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._connection is None:
            self._connection = sqlite3.connect(self.db_path)
            self._connection.row_factory = sqlite3.Row
        return self._connection

    def close(self):
        """Close database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def initialize(self):
        """Create schema if it doesn't exist."""
        conn = self.connect()
        conn.executescript(SCHEMA)
        conn.commit()

    # --- Transaction Operations ---

    def insert_transaction(self, txn: Transaction) -> bool:
        """
        Insert a transaction. Returns True if inserted, False if duplicate.
        """
        conn = self.connect()
        try:
            conn.execute(
                """
                INSERT INTO transactions (
                    id, source, source_id, source_type, date, type, income_category,
                    description, gross, fees, net, currency, available_on, payout_id,
                    created_at, metadata, exported_gnucash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    txn.id,
                    txn.source,
                    txn.source_id,
                    txn.source_type,
                    txn.date,
                    txn.type,
                    txn.income_category,
                    txn.description,
                    txn.gross,
                    txn.fees,
                    txn.net,
                    txn.currency,
                    txn.available_on,
                    txn.payout_id,
                    txn.created_at,
                    txn.metadata,
                    txn.exported_gnucash,
                ),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Duplicate source + source_id
            return False

    def transaction_exists(self, source: str, source_id: str) -> bool:
        """Check if a transaction already exists."""
        conn = self.connect()
        cursor = conn.execute(
            "SELECT 1 FROM transactions WHERE source = ? AND source_id = ?", (
                source, source_id)
        )
        return cursor.fetchone() is not None

    def get_transaction(self, txn_id: str) -> Optional[Transaction]:
        """Retrieve a transaction by ID."""
        conn = self.connect()
        cursor = conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (txn_id,))
        row = cursor.fetchone()
        if row:
            return Transaction(**dict(row))
        return None

    def get_transactions_by_date_range(
        self, start_date: str, end_date: str, type_filter: Optional[str] = None
    ) -> list[Transaction]:
        """Get transactions within a date range."""
        conn = self.connect()
        query = "SELECT * FROM transactions WHERE date >= ? AND date <= ?"
        params = [start_date, end_date]

        if type_filter:
            query += " AND type = ?"
            params.append(type_filter)

        query += " ORDER BY date ASC"

        cursor = conn.execute(query, params)
        return [Transaction(**dict(row)) for row in cursor.fetchall()]

    def get_pending_revenue(self, available_on: str) -> list[Transaction]:
        """Get revenue transactions that become available on a specific date."""
        conn = self.connect()
        cursor = conn.execute(
            """
            SELECT * FROM transactions 
            WHERE type = 'revenue' 
            AND available_on = ?
            AND payout_id IS NULL
            ORDER BY date ASC
        """,
            (available_on,),
        )
        return [Transaction(**dict(row)) for row in cursor.fetchall()]

    def get_unpaid_revenue(self) -> list[Transaction]:
        """Get all revenue transactions not yet linked to a payout."""
        conn = self.connect()
        cursor = conn.execute("""
            SELECT * FROM transactions 
            WHERE type = 'revenue' 
            AND payout_id IS NULL
            ORDER BY date ASC
        """)
        return [Transaction(**dict(row)) for row in cursor.fetchall()]

    def link_revenue_to_payout(self, transaction_id: str, payout_id: str):
        """Mark a revenue transaction as paid out."""
        conn = self.connect()
        conn.execute(
            "UPDATE transactions SET payout_id = ? WHERE id = ?", (
                payout_id, transaction_id)
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO payout_links (payout_id, transaction_id)
            VALUES (?, ?)
        """,
            (payout_id, transaction_id),
        )
        conn.commit()

    def get_unexported_transactions(
        self,
        exporter: str = "gnucash",
        type_filter: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[Transaction]:
        """Get transactions not yet exported to the specified exporter."""

        conn = self.connect()

        column = f"exported_{exporter}"
        query = f"SELECT * FROM transactions WHERE {column} IS NULL"
        params = []

        if type_filter:
            query += " AND type = ?"
            params.append(type_filter)
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date ASC"

        cursor = conn.execute(query, params)
        return [Transaction(**dict(row)) for row in cursor.fetchall()]

    def mark_exported(
        self,
        transaction_ids: list[str],
        exporter: str = "gnucash",
    ) -> int:
        """Mark transactions as exported. Returns count updated."""
        if not transaction_ids:
            return 0

        conn = self.connect()
        column = f"exported_{exporter}"
        timestamp = now_epoch()

        placeholders = ",".join("?" * len(transaction_ids))
        query = f"UPDATE transactions SET {
            column} = ? WHERE id IN ({placeholders})"

        cursor = conn.execute(query, [timestamp] + transaction_ids)
        conn.commit()
        return cursor.rowcount

    # --- Tax Calculation Operations ---

    def insert_tax_calculation(self, calc: TaxCalculation):
        """Insert or update tax calculation for a transaction."""
        conn = self.connect()
        conn.execute(
            """
            INSERT OR REPLACE INTO tax_calculations (
                transaction_id, fica_employee, fica_employer, federal, state, total, calculated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                calc.transaction_id,
                calc.fica_employee,
                calc.fica_employer,
                calc.federal,
                calc.state,
                calc.total,
                calc.calculated_at,
            ),
        )
        conn.commit()

    def get_tax_calculation(self, transaction_id: str) -> Optional[TaxCalculation]:
        """Get tax calculation for a transaction."""
        conn = self.connect()
        cursor = conn.execute(
            "SELECT * FROM tax_calculations WHERE transaction_id = ?", (
                transaction_id,)
        )
        row = cursor.fetchone()
        if row:
            return TaxCalculation(**dict(row))
        return None

    def get_taxes_for_payout(self, payout_id: str) -> dict[str, float]:
        """
        Get aggregated tax amounts for all revenue linked to a payout.
        Returns dict with fica_employee, fica_employer, federal, state, total.
        """
        conn = self.connect()
        cursor = conn.execute(
            """
            SELECT 
                SUM(tc.fica_employee) as fica_employee,
                SUM(tc.fica_employer) as fica_employer,
                SUM(tc.federal) as federal,
                SUM(tc.state) as state,
                SUM(tc.total) as total
            FROM tax_calculations tc
            JOIN payout_links pl ON tc.transaction_id = pl.transaction_id
            WHERE pl.payout_id = ?
        """,
            (payout_id,),
        )
        row = cursor.fetchone()
        if row and row["total"] is not None:
            return dict(row)
        return {
            "fica_employee": 0.0,
            "fica_employer": 0.0,
            "federal": 0.0,
            "state": 0.0,
            "total": 0.0,
        }

    # --- Export Log Operations ---

    def log_export(
        self,
        exporter: str,
        start_date: str,
        end_date: str,
        transaction_count: int,
        output_file: Optional[str] = None,
    ):
        """Record an export operation."""
        conn = self.connect()
        conn.execute(
            """
            INSERT INTO export_log (exporter, start_date, end_date, transaction_count, exported_at, output_file)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                exporter,
                start_date,
                end_date,
                transaction_count,
                now_epoch(),
                output_file,
            ),
        )
        conn.commit()

    # --- Summary Operations ---

    def get_summary(self) -> dict:
        """Get database summary statistics."""
        conn = self.connect()

        cursor = conn.execute("SELECT COUNT(*) FROM transactions")
        total_txns = cursor.fetchone()[0]

        cursor = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE type = 'revenue'")
        revenue_count = cursor.fetchone()[0]

        cursor = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE type = 'payout'")
        payout_count = cursor.fetchone()[0]

        cursor = conn.execute("SELECT MIN(date), MAX(date) FROM transactions")
        date_range = cursor.fetchone()

        cursor = conn.execute("""
            SELECT SUM(gross) as total_gross, SUM(fees) as total_fees, SUM(net) as total_net
            FROM transactions WHERE type = 'revenue'
        """)
        revenue_totals = cursor.fetchone()

        cursor = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE type = 'revenue' AND payout_id IS NULL"
        )
        pending_count = cursor.fetchone()[0]

        return {
            "total_transactions": total_txns,
            "revenue_transactions": revenue_count,
            "payout_transactions": payout_count,
            "pending_revenue": pending_count,
            "date_range": (date_range[0], date_range[1]) if date_range[0] else (None, None),
            "total_gross": revenue_totals[0] or 0.0,
            "total_fees": revenue_totals[1] or 0.0,
            "total_net": revenue_totals[2] or 0.0,
        }


def get_database(year: int, base_path: Optional[Path] = None) -> Database:
    """
    Get database instance for a specific year.

    Args:
        year: The year (e.g., 2025)
        base_path: Base directory for databases. Defaults to ./processed/

    Returns:
        Database instance
    """
    if base_path is None:
        base_path = Path("processed")

    db_path = base_path / "bookkeeper.db"
    db = Database(db_path)
    db.initialize()
    return db
