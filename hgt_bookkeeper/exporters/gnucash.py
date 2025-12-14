"""
GnuCash CSV exporter.

Generates multi-split journal entries for import into GnuCash.
Each revenue transaction becomes a full entry with:
- Income credit
- Stripe fee debit  
- Tax withholding transfers
- Operating account remainder
"""

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from hgt_bookkeeper.config import Config
from hgt_bookkeeper.database import Database, Transaction, TaxCalculation


@dataclass
class Split:
    """A single split in a GnuCash transaction."""
    account: str
    amount: float  # Positive = debit, negative = credit
    memo: str = ""


@dataclass 
class JournalEntry:
    """A complete GnuCash transaction with multiple splits."""
    date: str
    description: str
    splits: list[Split]
    
    def is_balanced(self) -> bool:
        """Check if debits equal credits."""
        total = sum(s.amount for s in self.splits)
        return abs(total) < 0.01  # Float tolerance


class GnuCashExporter:
    """Export transactions to GnuCash CSV format."""
    
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config
        self.accounts = config.accounts
    
    def export_revenue(
        self,
        output_path: Path,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        mark_exported: bool = True,
    ) -> dict:
        """
        Export revenue transactions to GnuCash CSV.
        
        Args:
            output_path: Path for output CSV
            start_date: Optional start date filter (YYYY-MM-DD)
            end_date: Optional end date filter (YYYY-MM-DD)
            mark_exported: Whether to mark transactions as exported
            
        Returns:
            dict with export statistics
        """
        # Get unexported revenue transactions
        transactions = self.db.get_unexported_transactions(
            type_filter="revenue",
            start_date=start_date,
            end_date=end_date,
        )
        
        entries = []
        exported_ids = []
        
        for txn in transactions:
            tax_calc = self.db.get_tax_calculation(txn.id)
            entry = self._build_revenue_entry(txn, tax_calc)
            
            if entry and entry.is_balanced():
                entries.append(entry)
                exported_ids.append(txn.id)
            else:
                # Log unbalanced entry?
                pass
        
        # Write CSV
        self._write_csv(output_path, entries)
        
        # Mark as exported
        if mark_exported and exported_ids:
            self.db.mark_exported(exported_ids, "gnucash")
        
        return {
            "exported": len(entries),
            "file": str(output_path),
        }
    
    def export_payouts(
        self,
        output_path: Path,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        mark_exported: bool = True,
    ) -> dict:
        """
        Export payout transactions to GnuCash CSV.
        
        Payouts are simple transfers: Stripe Balance -> Checking
        
        Args:
            output_path: Path for output CSV
            start_date: Optional start date filter
            end_date: Optional end date filter
            mark_exported: Whether to mark as exported
            
        Returns:
            dict with export statistics
        """
        transactions = self.db.get_unexported_transactions(
            type_filter="payout",
            start_date=start_date,
            end_date=end_date,
        )
        
        entries = []
        exported_ids = []
        
        for txn in transactions:
            entry = self._build_payout_entry(txn)
            if entry and entry.is_balanced():
                entries.append(entry)
                exported_ids.append(txn.id)
        
        self._write_csv(output_path, entries)
        
        if mark_exported and exported_ids:
            self.db.mark_exported(exported_ids, "gnucash")
        
        return {
            "exported": len(entries),
            "file": str(output_path),
        }
    
    def export_fees(
        self,
        output_path: Path,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        mark_exported: bool = True,
    ) -> dict:
        """
        Export platform fee transactions (Stripe billing fees).
        
        These are the monthly billing fees, not per-transaction fees.
        """
        transactions = self.db.get_unexported_transactions(
            type_filter="platform_fee",
            start_date=start_date,
            end_date=end_date,
        )
        
        entries = []
        exported_ids = []
        
        for txn in transactions:
            entry = self._build_fee_entry(txn)
            if entry and entry.is_balanced():
                entries.append(entry)
                exported_ids.append(txn.id)
        
        self._write_csv(output_path, entries)
        
        if mark_exported and exported_ids:
            self.db.mark_exported(exported_ids, "gnucash")
        
        return {
            "exported": len(entries),
            "file": str(output_path),
        }
    
    def _build_revenue_entry(
        self, 
        txn: Transaction, 
        tax_calc: Optional[TaxCalculation]
    ) -> Optional[JournalEntry]:
        """
        Build a revenue journal entry with tax withholding.
        
        For a $19 subscription with $2.75 fees:
        - Credit Income:Subscriptions     $19.00
        - Debit Expenses:Stripe Fees       $2.75
        - Debit Assets:Withholding:FICA-EE $1.45
        - Debit Assets:Withholding:FICA-ER $1.45
        - Debit Assets:Withholding:Federal $1.73
        - Debit Assets:Withholding:State   $0.83
        - Debit Assets:Stripe Balance     $10.79 (remainder)
        """
        if txn.gross is None or txn.fees is None:
            return None
        
        # Select income account based on category
        if txn.income_category == "invoice":
            income_account = self.accounts.invoice_income
        else:
            income_account = self.accounts.subscription_income
        
        splits = []
        
        # Credit income (negative = credit)
        splits.append(Split(
            account=income_account,
            amount=-txn.gross,
            memo=txn.description or "",
        ))
        
        # Debit Stripe transaction fees
        splits.append(Split(
            account=self.accounts.transaction_fees,
            amount=txn.fees,
            memo="Stripe fee",
        ))
        
        # Tax withholding splits (if we have tax calc)
        if tax_calc:
            splits.append(Split(
                account=self.accounts.withholding_fica_employee,
                amount=tax_calc.fica_employee,
                memo="FICA-EE withholding",
            ))
            splits.append(Split(
                account=self.accounts.withholding_fica_employer,
                amount=tax_calc.fica_employer,
                memo="FICA-ER withholding",
            ))
            splits.append(Split(
                account=self.accounts.withholding_federal,
                amount=tax_calc.federal,
                memo="Federal withholding",
            ))
            splits.append(Split(
                account=self.accounts.withholding_state,
                amount=tax_calc.state,
                memo="State withholding",
            ))
            
            # Remainder to Stripe balance
            withheld = tax_calc.total
        else:
            withheld = 0
        
        # Stripe balance gets net minus withholding
        stripe_balance_amount = txn.net - withheld
        splits.append(Split(
            account=self.accounts.stripe_balance,
            amount=stripe_balance_amount,
            memo="Net to Stripe",
        ))
        
        return JournalEntry(
            date=txn.date,
            description=f"{txn.income_category.title()}: {txn.description or 'Revenue'}",
            splits=splits,
        )
    
    def _build_payout_entry(self, txn: Transaction) -> JournalEntry:
        """
        Build a payout journal entry.
        
        Simple transfer from Stripe Balance to Checking.
        Net is negative (money leaving Stripe).
        """
        amount = abs(txn.net)
        
        return JournalEntry(
            date=txn.date,
            description="Stripe Payout",
            splits=[
                Split(
                    account=self.accounts.stripe_balance,
                    amount=-amount,  # Credit (leaving Stripe)
                    memo="Payout to bank",
                ),
                Split(
                    account=self.accounts.checking,
                    amount=amount,  # Debit (entering checking)
                    memo="Stripe payout",
                ),
            ],
        )
    
    def _build_fee_entry(self, txn: Transaction) -> JournalEntry:
        """
        Build a platform fee journal entry.
        
        Stripe billing fees (not per-transaction fees).
        """
        amount = abs(txn.net)
        
        return JournalEntry(
            date=txn.date,
            description=txn.description or "Stripe Billing Fee",
            splits=[
                Split(
                    account=self.accounts.stripe_balance,
                    amount=-amount,  # Credit (leaving Stripe)
                    memo="Billing fee",
                ),
                Split(
                    account=self.accounts.billing_fees,
                    amount=amount,  # Debit expense
                    memo=txn.description or "",
                ),
            ],
        )
    
    def _write_csv(self, path: Path, entries: list[JournalEntry]):
        """
        Write journal entries to GnuCash CSV format.
        
        GnuCash CSV import format:
        Date, Description, Account, Deposit, Withdrawal, Balance
        
        For multi-split, each split is a row with same Date/Description.
        """
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            
            # Header
            writer.writerow([
                "Date",
                "Description",
                "Account",
                "Deposit",
                "Withdrawal",
                "Memo",
            ])
            
            for entry in entries:
                for split in entry.splits:
                    if split.amount >= 0:
                        deposit = ""
                        withdrawal = f"{split.amount:.2f}"
                    else:
                        deposit = f"{abs(split.amount):.2f}"
                        withdrawal = ""
                    
                    writer.writerow([
                        entry.date,
                        entry.description,
                        split.account,
                        deposit,
                        withdrawal,
                        split.memo,
                    ])
