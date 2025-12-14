"""
Stripe CSV importer - reads balance_history.csv exports.

Thin wrapper that handles CSV I/O and delegates parsing to base.
"""

import csv
from pathlib import Path

from hgt_bookkeeper.database import Database
from hgt_bookkeeper.config import Config
from hgt_bookkeeper.importers.stripe.base import (
    import_transaction,
    parse_amount,
    parse_date,
)


def import_csv(db: Database, config: Config, csv_path: Path) -> dict:
    """
    Import transactions from Stripe balance_history.csv.
    
    Args:
        db: Database instance
        config: Config instance
        csv_path: Path to the CSV file
    
    Returns:
        dict with import statistics
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    
    imported = 0
    skipped = 0
    errors = 0
    
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            try:
                success = import_transaction(
                    db=db,
                    config=config,
                    source_id=row["id"].strip(),
                    source_type=row["Type"].strip(),
                    description=row.get("Description", "").strip(),
                    amount=parse_amount(row["Amount"]),
                    fee=parse_amount(row["Fee"]),
                    net=parse_amount(row["Net"]),
                    currency=row["Currency"].strip(),
                    created_date=parse_date(row["Created (UTC)"]),
                    available_date=parse_date(row.get("Available On (UTC)", "")),
                    payout_id=row.get("Transfer", "").strip() or None,
                )
                
                if success:
                    imported += 1
                else:
                    skipped += 1
                    
            except Exception as e:
                errors += 1
                continue
    
    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
    }
