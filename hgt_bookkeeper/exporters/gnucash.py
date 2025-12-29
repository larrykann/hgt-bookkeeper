"""
GnuCash CSV exporter.

Generates multi-split journal entries for import into GnuCash.

Transaction types:
- Revenue: Debit Stripe Balance (net), debit fees, debit tax expenses, 
           credit tax liabilities, credit income
- Platform Fee: Credit Stripe Balance, debit billing expense
- Payout: Credit Stripe Balance, debit operating + withholding accounts
"""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from hgt_bookkeeper.config import Config
from hgt_bookkeeper.database import Database, Transaction, TaxCalculation, from_epoch


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
    
    def export_all(
        self,
        output_path: Path,
        start_date: Optional[int] = None,
        end_date: Optional[int] = None,
        mark_exported: bool = True,
    ) -> dict:
        """
        Export all transaction types to a single GnuCash CSV.
        
        Args:
            output_path: Path for output CSV
            start_date: Optional start date filter (epoch)
            end_date: Optional end date filter (epoch)
            mark_exported: Whether to mark transactions as exported
            
        Returns:
            dict with export statistics
        """
        entries = []
        exported_ids = []
        stats = {"revenue": 0, "platform_fee": 0, "payout": 0, "skipped": 0}
        
        # Get all unexported transactions
        transactions = self.db.get_unexported_transactions(
            start_date=start_date,
            end_date=end_date,
        )
        
        for txn in transactions:
            entry = None
            
            if txn.type == "revenue":
                tax_calc = self.db.get_tax_calculation(txn.id)
                entry = self._build_revenue_entry(txn, tax_calc)
                if entry and entry.is_balanced():
                    stats["revenue"] += 1
                    
            elif txn.type == "platform_fee":
                entry = self._build_fee_entry(txn)
                if entry and entry.is_balanced():
                    stats["platform_fee"] += 1
                    
            elif txn.type == "payout":
                entry = self._build_payout_entry(txn)
                if entry and entry.is_balanced():
                    stats["payout"] += 1
            
            if entry and entry.is_balanced():
                entries.append(entry)
                exported_ids.append(txn.id)
            else:
                stats["skipped"] += 1
        
        # Sort entries by date
        entries.sort(key=lambda e: e.date)
        
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write CSV
        self._write_csv(output_path, entries)
        
        # Mark as exported
        if mark_exported and exported_ids:
            self.db.mark_exported(exported_ids, "gnucash")
        
        return {
            "total": len(entries),
            "revenue": stats["revenue"],
            "platform_fee": stats["platform_fee"],
            "payout": stats["payout"],
            "skipped": stats["skipped"],
            "file": str(output_path),
        }
    
    def _build_revenue_entry(
        self, 
        txn: Transaction, 
        tax_calc: Optional[TaxCalculation]
    ) -> Optional[JournalEntry]:
        """
        Build a revenue journal entry.
        
        Debits: Stripe Balance (net), transaction fees, tax expenses
        Credits: Tax liabilities, income (gross)
        """
        if txn.gross is None or txn.fees is None:
            return None
        
        # Select income account based on category
        if txn.income_category == "invoice":
            income_account = self.accounts.invoice_income
        else:
            income_account = self.accounts.subscription_income
        
        splits = []
        
        # Debit Stripe Balance with net amount
        splits.append(Split(
            account=self.accounts.stripe_balance,
            amount=txn.net,
            memo="Net to Stripe",
        ))
        
        # Debit transaction fees
        splits.append(Split(
            account=self.accounts.transaction_fees,
            amount=txn.fees,
            memo="Stripe fee",
        ))
        
        # Tax entries (if we have tax calc)
        if tax_calc:
            # Debit tax expenses
            splits.append(Split(
                account=self.accounts.tax_expense_fica_employee,
                amount=tax_calc.fica_employee,
                memo="FICA-EE expense",
            ))
            splits.append(Split(
                account=self.accounts.tax_expense_fica_employer,
                amount=tax_calc.fica_employer,
                memo="FICA-ER expense",
            ))
            splits.append(Split(
                account=self.accounts.tax_expense_federal,
                amount=tax_calc.federal,
                memo="Federal expense",
            ))
            splits.append(Split(
                account=self.accounts.tax_expense_state,
                amount=tax_calc.state,
                memo="State expense",
            ))
            
            # Credit tax liabilities (negative)
            splits.append(Split(
                account=self.accounts.tax_liability_fica_employee,
                amount=-tax_calc.fica_employee,
                memo="FICA-EE liability",
            ))
            splits.append(Split(
                account=self.accounts.tax_liability_fica_employer,
                amount=-tax_calc.fica_employer,
                memo="FICA-ER liability",
            ))
            splits.append(Split(
                account=self.accounts.tax_liability_federal,
                amount=-tax_calc.federal,
                memo="Federal liability",
            ))
            splits.append(Split(
                account=self.accounts.tax_liability_state,
                amount=-tax_calc.state,
                memo="State liability",
            ))
        
        # Credit income (negative = credit)
        splits.append(Split(
            account=income_account,
            amount=-txn.gross,
            memo=txn.description or "",
        ))
        
        return JournalEntry(
            date=from_epoch(txn.date).strftime("%Y-%m-%d"),
            description=f"{txn.income_category.title() if txn.income_category else 'Revenue'}: {txn.description or 'Revenue'}",
            splits=splits,
        )
    
    def _build_fee_entry(self, txn: Transaction) -> JournalEntry:
        """
        Build a platform fee journal entry.
        
        Stripe billing fees (monthly fees, not per-transaction).
        Credit Stripe Balance, debit expense.
        """
        amount = abs(txn.net)
        
        return JournalEntry(
            date=from_epoch(txn.date).strftime("%Y-%m-%d"),
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
    
    def _build_payout_entry(self, txn: Transaction) -> JournalEntry:
        """
        Build a payout journal entry.
        
        Credit Stripe Balance (full payout amount).
        Debit operating account (net after withholding).
        Debit withholding accounts (tax amounts from linked revenue).
        """
        payout_amount = abs(txn.net)
        
        # Get aggregated taxes from all revenue linked to this payout
        taxes = self.db.get_taxes_for_payout(txn.id)
        
        # Calculate operating amount (payout minus withholding)
        total_withholding = taxes["total"]
        operating_amount = payout_amount - total_withholding
        
        splits = [
            # Credit Stripe Balance (money leaving)
            Split(
                account=self.accounts.stripe_balance,
                amount=-payout_amount,
                memo="Payout to bank",
            ),
            # Debit operating account
            Split(
                account=self.accounts.operating,
                amount=operating_amount,
                memo="Net after withholding",
            ),
        ]
        
        # Debit withholding accounts (only if there are taxes)
        if taxes["fica_employee"] > 0:
            splits.append(Split(
                account=self.accounts.withholding_fica_employee,
                amount=taxes["fica_employee"],
                memo="FICA-EE withholding",
            ))
        if taxes["fica_employer"] > 0:
            splits.append(Split(
                account=self.accounts.withholding_fica_employer,
                amount=taxes["fica_employer"],
                memo="FICA-ER withholding",
            ))
        if taxes["federal"] > 0:
            splits.append(Split(
                account=self.accounts.withholding_federal,
                amount=taxes["federal"],
                memo="Federal withholding",
            ))
        if taxes["state"] > 0:
            splits.append(Split(
                account=self.accounts.withholding_state,
                amount=taxes["state"],
                memo="State withholding",
            ))
        
        return JournalEntry(
            date=from_epoch(txn.date).strftime("%Y-%m-%d"),
            description="Stripe Payout",
            splits=splits,
        )
    
    def _write_csv(self, path: Path, entries: list[JournalEntry]):
        """
        Write journal entries to GnuCash multi-split CSV format.
        """
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            
            # Header
            writer.writerow([
                "Date",
                "Description",
                "Account",
                "Amount",
                "Notes",
            ])
            
            for entry in entries:
                first_split = True
                for split in entry.splits:
                    if first_split:
                        writer.writerow([
                            entry.date,
                            entry.description,
                            split.account,
                            f"{split.amount:.2f}",
                            split.memo,
                        ])
                        first_split = False
                    else:
                        writer.writerow([
                            "",
                            "",
                            split.account,
                            f"{split.amount:.2f}",
                            split.memo,
                        ])
