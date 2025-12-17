"""
Stripe API importer - fetches from balance_transactions endpoint.

Thin wrapper that handles API calls and delegates parsing to base.
TODO: Implement after CSV workflow is validated.
"""

from hgt_bookkeeper.database import Database
from hgt_bookkeeper.config import Config
from hgt_bookkeeper.importers.stripe.base import (
    import_transaction,
    parse_amount,
    parse_date,
)


def import_api(db: Database, config: Config, api_key: str, limit: int = 100) -> dict:
    """
    Import transactions from Stripe API.
    
    Args:
        db: Database instance
        config: Config instance  
        api_key: Stripe secret key
        limit: Max transactions to fetch per request
    
    Returns:
        dict with import statistics
    """
    # TODO: Implement
    # 1. GET /v1/balance_transactions with pagination
    # 2. For each transaction, call import_transaction() with:
    #    - source_id = txn["id"]
    #    - source_type = txn["type"]
    #    - description = txn["description"]
    #    - amount = txn["amount"] / 100  (API uses cents)
    #    - fee = txn["fee"] / 100
    #    - net = txn["net"] / 100
    #    - currency = txn["currency"]
    #    - created_date = datetime.fromtimestamp(txn["created"]).strftime("%Y-%m-%d")
    #    - available_date = datetime.fromtimestamp(txn["available_on"]).strftime("%Y-%m-%d")
    #    - payout_id = txn.get("source") if type is payout else None
    
    raise NotImplementedError("API import not yet implemented. Use CSV export.") # api specific import
