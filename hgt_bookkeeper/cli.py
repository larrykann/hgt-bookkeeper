"""
Command-line interface for hgt-bookkeeper.
"""

import argparse
import sys
from pathlib import Path

from rich.console import Console

console = Console()


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="book",
        description="HGT Bookkeeper - Transaction formatting middleware for self-employed accounting",     
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed error messages and stack traces"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # ===== IMPORT COMMAND =====
    import_parser = subparsers.add_parser(
        "import",
        help="Import transactions from payment processors"
    )
    import_parser.add_argument(
        "source",
        choices=["stripe"],
        help="Payment processor source"
    )
    import_parser.add_argument(
        "file",
        help="CSV file to import (filename only or full path)"
    )
    
    # ===== EXPORT COMMAND =====
    export_parser = subparsers.add_parser(
        "export",
        help="Export transactions to accounting software"
    )
    export_parser.add_argument(
        "format",
        choices=["gnucash"],
        help="Export format"
    )
    export_parser.add_argument(
        "--start",
        help="Start date (YYYY-MM-DD). If omitted, exports new transactions only."
    )
    export_parser.add_argument(
        "--end",
        help="End date (YYYY-MM-DD). Only used with --start."
    )
    export_parser.add_argument(
        "--output", "-o",
        help="Output file path (optional, auto-generated if not provided)"
    )
    
    # ===== STATUS COMMAND =====
    status_parser = subparsers.add_parser(
        "status",
        help="Show database summary and statistics"
    )
    
    # Parse arguments
    args = parser.parse_args()
    
    # Show help if no command provided
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Dispatch to command handlers
    try:
        if args.command == "import":
            cmd_import(args)
        elif args.command == "export":
            cmd_export(args)
        elif args.command == "status":
            cmd_status(args)
    except Exception as e:
        if args.verbose:
            raise
        else:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)


def cmd_import(args):
    """Handle import command."""
    from hgt_bookkeeper.config import load_config, ConfigError
    from hgt_bookkeeper.database import get_database, from_epoch
    from hgt_bookkeeper.importers.stripe import import_csv
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
    
    # Resolve file path
    file_path = Path(args.file)
    if not file_path.is_absolute():
        # Look in raw/{source}/
        file_path = Path("raw") / args.source / args.file
    
    if not file_path.exists():
        console.print(f"[red]Error:[/red] File not found: {file_path}")
        console.print(f"[yellow]Tip:[/yellow] Place CSV files in raw/{args.source}/ or provide full path")
        sys.exit(1)
    
    # Load config and database
    try:
        config = load_config()
    except ConfigError as e:
        console.print(f"[red]Configuration Error:[/red] {e}")
        sys.exit(1)
    
    db = get_database()
    
    # Import with progress
    console.print(f"[blue]Importing from:[/blue] {file_path}")
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task("Importing transactions...", total=None)
        
        if args.source == "stripe":
            result = import_csv(db, config, file_path)
        
        progress.update(task, completed=True)
    
    # Print summary
    console.print()
    console.print("[green]Import Complete[/green]")
    console.print(f"  Imported: [cyan]{result['imported']}[/cyan]")
    console.print(f"  Skipped (duplicates): [yellow]{result['skipped']}[/yellow]")
    console.print(f"  Errors: [red]{result['errors']}[/red]")
    console.print(f"  Linked to payouts: [cyan]{result['linked']}[/cyan]")
    
    # Show date range if we imported anything
    if result['imported'] > 0:
        summary = db.get_summary()
        if summary['date_range'][0]:
            start_date = from_epoch(summary['date_range'][0]).strftime("%Y-%m-%d")
            end_date = from_epoch(summary['date_range'][1]).strftime("%Y-%m-%d")
            console.print(f"  Date range: [cyan]{start_date}[/cyan] to [cyan]{end_date}[/cyan]")
    
    # Warn about orphaned revenue
    if result['orphans']:
        console.print()
        console.print(f"[yellow]Warning:[/yellow] {len(result['orphans'])} revenue transactions have no matching payout")
        console.print("[yellow]These may need a future import to link:[/yellow]")
        console.print()
        console.print(f"  {'Date':<12} {'Amount':>10} {'Available On':<12}")
        console.print(f"  {'-'*12} {'-'*10} {'-'*12}")
        for orphan in result['orphans'][:10]:  # Show first 10
            date = from_epoch(orphan['date']).strftime("%Y-%m-%d")
            available = from_epoch(orphan['available_on']).strftime("%Y-%m-%d") if orphan['available_on'] else "N/A"
            console.print(f"  {date:<12} ${orphan['gross']:>9.2f} {available:<12}")
        if len(result['orphans']) > 10:
            console.print(f"  ... and {len(result['orphans']) - 10} more")
    db.close()

def cmd_export(args):
    """Handle export command."""
    from hgt_bookkeeper.config import load_config, ConfigError
    from hgt_bookkeeper.database import get_database, from_epoch, to_epoch
    from hgt_bookkeeper.exporters.gnucash import GnuCashExporter
    from datetime import datetime
    from pathlib import Path
    
    # Load config and database
    try:
        config = load_config()
    except ConfigError as e:
        console.print(f"[red]Configuration Error:[/red] {e}")
        sys.exit(1)
    
    db = get_database()
    
    # Parse date arguments
    start_date = None
    end_date = None
    
    if args.start:
        try:
            dt = datetime.strptime(args.start, "%Y-%m-%d")
            start_date = to_epoch(dt)
        except ValueError:
            console.print(f"[red]Error:[/red] Invalid start date format. Use YYYY-MM-DD")
            sys.exit(1)
    
    if args.end:
        if not args.start:
            console.print(f"[red]Error:[/red] --end requires --start")
            sys.exit(1)
        try:
            dt = datetime.strptime(args.end, "%Y-%m-%d")
            end_date = to_epoch(dt)
        except ValueError:
            console.print(f"[red]Error:[/red] Invalid end date format. Use YYYY-MM-DD")
            sys.exit(1)
    
    # Determine export mode
    if args.start:
        mode = "date range"
        # For date range mode, we export regardless of export flag
        # Get all transactions in range, not just unexported
        transactions = db.get_transactions_by_date_range(start_date, end_date or to_epoch(datetime.now()))
        if not transactions:
            console.print(f"[yellow]No transactions found in date range[/yellow]")
            db.close()
            sys.exit(0)
        
        # Get actual date range from transactions
        actual_start = min(t.date for t in transactions)
        actual_end = max(t.date for t in transactions)
    else:
        mode = "new only"
        # Check for unexported transactions
        transactions = db.get_unexported_transactions()
        if not transactions:
            console.print(f"[green]No new transactions to export[/green]")
            db.close()
            sys.exit(0)
        
        actual_start = min(t.date for t in transactions)
        actual_end = max(t.date for t in transactions)
    
    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        start_str = from_epoch(actual_start).strftime("%Y-%m-%d")
        end_str = from_epoch(actual_end).strftime("%Y-%m-%d")
        output_dir = Path("processed/gnucash")
        output_path = output_dir / f"stripe_gnucash_{start_str}_{end_str}.csv"
    
    # Create exporter and export
    console.print(f"[blue]Exporting ({mode}):[/blue] {len(transactions)} transactions")
    
    exporter = GnuCashExporter(db, config)
    
    if args.start:
        # Date range mode - export all in range, mark as exported
        result = exporter.export_all(
            output_path=output_path,
            start_date=start_date,
            end_date=end_date,
            mark_exported=True,
        )
    else:
        # New only mode
        result = exporter.export_all(
            output_path=output_path,
            mark_exported=True,
        )
    
    # Print summary
    console.print()
    console.print("[green]Export Complete[/green]")
    console.print(f"  Total exported: [cyan]{result['total']}[/cyan]")
    console.print(f"    Revenue: [cyan]{result['revenue']}[/cyan]")
    console.print(f"    Platform fees: [cyan]{result['platform_fee']}[/cyan]")
    console.print(f"    Payouts: [cyan]{result['payout']}[/cyan]")
    if result['skipped'] > 0:
        console.print(f"  Skipped (unbalanced): [yellow]{result['skipped']}[/yellow]")
    console.print(f"  Output file: [cyan]{result['file']}[/cyan]")
    
    # Show date range
    start_str = from_epoch(actual_start).strftime("%Y-%m-%d")
    end_str = from_epoch(actual_end).strftime("%Y-%m-%d")
    console.print(f"  Date range: [cyan]{start_str}[/cyan] to [cyan]{end_str}[/cyan]")
    
    db.close()

def cmd_status(args):
    """Handle status command."""
    from hgt_bookkeeper.database import get_database, from_epoch
    from rich.table import Table
    
    db = get_database()
    summary = db.get_summary()
    
    # Main statistics table
    table = Table(title="Database Summary", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    
    table.add_row("Total Transactions", str(summary['total_transactions']))
    table.add_row("Revenue Transactions", str(summary['revenue_transactions']))
    table.add_row("Payout Transactions", str(summary['payout_transactions']))
    table.add_row("Pending Revenue (not paid out)", str(summary['pending_revenue']))
    
    if summary['date_range'][0]:
        start = from_epoch(summary['date_range'][0]).strftime("%Y-%m-%d")
        end = from_epoch(summary['date_range'][1]).strftime("%Y-%m-%d")
        table.add_row("Date Range", f"{start} to {end}")
    
    table.add_row("Total Gross Revenue", f"${summary['total_gross']:.2f}")
    table.add_row("Total Fees", f"${summary['total_fees']:.2f}")
    table.add_row("Total Net Revenue", f"${summary['total_net']:.2f}")
    
    console.print(table)
    
    # Export status
    console.print()
    console.print("[blue]Export Status:[/blue]")
    
    unexported = len(db.get_unexported_transactions())
    if unexported > 0:
        console.print(f"  [yellow]{unexported}[/yellow] transactions not yet exported")
    else:
        console.print(f"  [green]All transactions exported[/green]")
    
    db.close()
