"""
Stripe importer base - shared parsing and normalization logic.

Handles:
- Field parsing (amounts, dates, descriptions)
- Type mapping (charge → revenue, etc.)
- Income category detection
- Transaction creation

Used by both CSV and API importers.
"""

import uuid
from datetime import datetime
from typing import Optional

from hgt_bookkeeper.database import Database, Transaction, TaxCalculation, now_epoch, to_epoch
from hgt_bookkeeper.config import Config


# Map Stripe transaction types to canonical types
TYPE_MAPPING = {
    "charge": "revenue",
    "payment": "revenue",
    "stripe_fee": "platform_fee",
    "payout": "payout",
    "refund": "refund",
    "adjustment": "adjustment",
    "transfer": "payout",
}

# Keywords for income category detection
SUBSCRIPTION_KEYWORDS = [
    "subscription update",
    "subscription creation",
    "subscription",
]

INVOICE_KEYWORDS = [
    "payment for invoice",
]


def parse_income_category(description: str, source_type: str) -> Optional[str]:
    """
    Determine income category from transaction description.
    
    Based on actual Stripe balance_history.csv patterns:
    - Type "charge" + "Subscription update/creation" → subscription
    - Type "payment" + "Payment for Invoice" → invoice
    """
    if source_type.lower() not in ("charge", "payment"):
        return None
    
    desc_lower = (description or "").lower()
    
    for keyword in INVOICE_KEYWORDS:
        if keyword in desc_lower:
            return "invoice"
    
    for keyword in SUBSCRIPTION_KEYWORDS:
        if keyword in desc_lower:
            return "subscription"
    
    # Fallback based on type
    if source_type.lower() == "payment":
        return "invoice"
    if source_type.lower() == "charge":
        return "subscription"
    
    return None


def map_transaction_type(source_type: str) -> str:
    """Map Stripe transaction type to canonical type."""
    return TYPE_MAPPING.get(source_type.lower(), "adjustment")


def parse_amount(value) -> float:
    """Parse amount to float. Handles strings, floats, None."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().strip('"').strip("'")
    if cleaned == "":
        return 0.0
    return float(cleaned)


def parse_date(value) -> Optional[str]:
    """Parse date to YYYY-MM-DD format."""
    if not value:
        return None
    cleaned = str(value).strip().strip('"')

    # Handle "YYYY-MM-DD HH:MM:SS" format from Stripe
    if " " in cleaned:
        date_part = cleaned.split(" ")[0]
    else:
        date_part = cleaned

    # Parse YYYY-MM-DD to datetime, then to epoch
    dt = datetime.strptime(date_part, "%Y-%m-%d")
    return to_epoch(dt)

def create_transaction(
    source_id: str,
    source_type: str,
    description: str,
    amount: float,
    fee: float,
    net: float,
    currency: str,
    created_date: str,
    available_date: Optional[str] = None,
    payout_id: Optional[str] = None,
) -> Transaction:
    """
    Create a normalized Transaction from Stripe data.
    
    Args:
        source_id: Stripe transaction ID (txn_xxx)
        source_type: Stripe type (charge, payout, etc.)
        description: Transaction description
        amount: Gross amount
        fee: Fee amount
        net: Net amount
        currency: Currency code
        created_date: Transaction date (YYYY-MM-DD)
        available_date: When funds available (YYYY-MM-DD)
        payout_id: Transfer/payout ID this belongs to (po_xxx)
    """
    canonical_type = map_transaction_type(source_type)
    income_category = parse_income_category(description, source_type)
    
    if canonical_type == "revenue":
        gross = abs(amount)
        fees = abs(fee)
    else:
        gross = None
        fees = None
    
    return Transaction(
        id=str(uuid.uuid4()),
        source="stripe",
        source_id=source_id,
        source_type=source_type,
        date=created_date,
        type=canonical_type,
        income_category=income_category,
        description=description,
        gross=gross,
        fees=fees,
        net=net,
        currency=currency.lower(),
        available_on=available_date,
        payout_id=payout_id,
        created_at=now_epoch(),
        metadata=None,
    )


def calculate_taxes(txn: Transaction, config: Config) -> Optional[TaxCalculation]:
    """Calculate taxes for a revenue transaction."""
    if txn.type != "revenue" or txn.gross is None or txn.fees is None:
        return None
    
    taxes = config.calculate_taxes(txn.gross, txn.fees)
    
    return TaxCalculation(
        transaction_id=txn.id,
        fica_employee=taxes["fica_employee"],
        fica_employer=taxes["fica_employer"],
        federal=taxes["federal"],
        state=taxes["state"],
        total=sum(taxes.values()),
        calculated_at=now_epoch(),
    )


def import_transaction(
    db: Database,
    config: Config,
    source_id: str,
    source_type: str,
    description: str,
    amount: float,
    fee: float,
    net: float,
    currency: str,
    created_date: str,
    available_date: Optional[str] = None,
    payout_id: Optional[str] = None,
) -> bool:
    """
    Import a single transaction into the database.
    
    Returns True if imported, False if skipped (duplicate).
    """
    if db.transaction_exists("stripe", source_id):
        return False
    
    txn = create_transaction(
        source_id=source_id,
        source_type=source_type,
        description=description,
        amount=amount,
        fee=fee,
        net=net,
        currency=currency,
        created_date=created_date,
        available_date=available_date,
        payout_id=payout_id,
    )
    
    if db.insert_transaction(txn):
        if txn.type == "revenue":
            tax_calc = calculate_taxes(txn, config)
            if tax_calc:
                db.insert_tax_calculation(tax_calc)
        return True
    
    return False
