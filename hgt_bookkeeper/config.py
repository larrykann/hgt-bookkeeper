"""
Configuration loading and management for hgt-bookkeeper.

Loads TOML configuration and provides typed access to settings.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import tomllib


class ConfigError(Exception):
    """Raised when configuration is invalid or missing."""

    pass

@dataclass
class Accounts:
    """All account mappings for GnuCash transactions."""

    checking: str
    operating: str
    
    # Asset accounts for withholding
    withholding_fica_employee: str
    withholding_fica_employer: str
    withholding_federal: str
    withholding_state: str

    # Stripe Expenses
    stripe_balance: str
    transaction_fees: str
    billing_fees: str

    # Stripe Income
    subscription_income: str
    invoice_income: str

    # Tax Liability Accounts
    tax_liability_fica_employee: str
    tax_liability_fica_employer: str
    tax_liability_federal: str
    tax_liability_state: str

    # Tax Exepense Accounts
    tax_expense_fica_employee: str
    tax_expense_fica_employer: str
    tax_expense_federal: str
    tax_expense_state: str

@dataclass
class TaxRates:
    """Tax withholding rates."""

    fica_employee: float
    fica_employer: float
    federal_income: float
    state_income: float

    @property
    def total_fica(self) -> float:
        """Total FICA rate (employee + employer)."""
        return self.fica_employee + self.fica_employer

    @property
    def total_income_tax(self) -> float:
        """Total income tax rate (federal + state)."""
        return self.federal_income + self.state_income


@dataclass
class Options:
    """Processing options."""

    auto_withhold: bool = True
    split_fica: bool = True
    round_to_cents: bool = True


@dataclass
class Config:
    """Complete configuration for hgt-bookkeeper."""

    accounts: Accounts
    tax_rates: TaxRates
    options: Options

    def calculate_taxes(self, gross: float, fees: float) -> dict[str, float]:
        """
        Calculate tax amounts for a charge using accurate SE tax method.

        Calculation:
        1. FICA (SE Tax) = Gross × 15.3% (split into employee/employer)
        2. Income Tax Basis = Gross - Fees - (FICA × 50%)
        3. Federal = Income Tax Basis × federal rate
        4. State = Income Tax Basis × state rate

        Args:
            gross: Gross revenue amount
            fees: Payment processing fees (Stripe + Substack)

        Returns:
            Dictionary with tax amounts for each category
        """
        # FICA calculated on gross
        fica_employee = gross * self.tax_rates.fica_employee
        fica_employer = gross * self.tax_rates.fica_employer
        total_fica = fica_employee + fica_employer

        # Income tax basis: gross minus fees minus employer FICA deduction
        # (The employer half of SE tax is deductible from income)
        income_tax_basis = gross - fees - (total_fica * 0.5)

        # Federal and state on the reduced basis
        federal = income_tax_basis * self.tax_rates.federal_income
        state = income_tax_basis * self.tax_rates.state_income

        taxes = {
            "fica_employee": fica_employee,
            "fica_employer": fica_employer,
            "federal": federal,
            "state": state,
        }

        if self.options.round_to_cents:
            taxes = {k: round(v, 2) for k, v in taxes.items()}

        return taxes

    def total_withholding(self, gross: float, fees: float) -> float:
        """Calculate total tax withholding for a charge."""
        taxes = self.calculate_taxes(gross, fees)
        return sum(taxes.values())

    def available_for_operating(self, gross: float, fees: float) -> float:
        """Calculate amount available for operating after withholding."""
        net = gross - fees
        return net - self.total_withholding(gross, fees)


def find_config_file(start_path: Optional[Path] = None) -> Path:
    """
    Find config.toml starting from the given path and searching up.

    Args:
        start_path: Directory to start searching from. Defaults to cwd.

    Returns:
        Path to config.toml

    Raises:
        ConfigError: If config.toml is not found
    """
    if start_path is None:
        start_path = Path.cwd()

    current = start_path.resolve()

    # Search up the directory tree
    while current != current.parent:
        config_path = current / "config.toml"
        if config_path.exists():
            return config_path
        current = current.parent

    # Also check the original start path
    config_path = start_path / "config.toml"
    if config_path.exists():
        return config_path

    raise ConfigError(
        f"config.toml not found. Searched from {start_path} to filesystem root.\n"
        "Create one by copying config.example.toml to config.toml"
    )


def load_config(config_path: Optional[Path] = None, exporter: Str = "gnucash") -> Config:
    """
    Load and parse configuration from TOML file.

    Args:
        config_path: Path to config.toml. If None, searches for it.

    Returns:
        Parsed Config object

    Raises:
        ConfigError: If config is missing or invalid
    """
    if config_path is None:
        config_path = find_config_file()

    try:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {config_path}")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML in {config_path}: {e}")

    try:
        # Parse accounts section
        acct = raw["accounts"][exporter]
        accounts = Accounts(
            checking=acct["checking"],
            operating=acct["withholding"]["operating"],
            withholding_fica_employee=acct["withholding"]["fica_employee"],
            withholding_fica_employer=acct["withholding"]["fica_employer"],
            withholding_federal=acct["withholding"]["federal"],
            withholding_ state=acct["withholding"]["state"],
            stripe_balance=acct["stripe_balance"],
            transaction_fees=acct["transaction_fees"],
            billing_fees=acct["billing_fees"],
            subscription_income=acct["subscription_income"],
            invoice_income=acct["invoice_income"],
            tax_liability_fica_employee=acct["tax_liability"]["fica_employee"],
            tax_liability_fica_employer=acct["tax_liability"]["fica_employer"],
            tax_liability_federal=acct["tax_liability"]["federal"],
            tax_liability_state=acct["tax_liability"]["state"],
            tax_expense_fica_employee=acct["tax_expense"]["fica_employee"],
            tax_expense_fica_employer=acct["tax_expense"]["fica_employer"],
            tax_expense_federal=acct["tax_expense"]["federal"],
           tax_expense_state=acct["tax_expense"]["state"],
        )

        # Parse tax rates
        rates = raw["tax_rates"]
        tax_rates = TaxRates(
            fica_employee=rates["fica_employee"],
            fica_employer=rates["fica_employer"],
            federal_income=rates["federal_income"],
            state_income=rates["state_income"],
        )

        # Parse options (with defaults)
        opts = raw.get("options", {})
        options = Options(
            auto_withhold=opts.get("auto_withhold", True),
            split_fica=opts.get("split_fica", True),
            round_to_cents=opts.get("round_to_cents", True),
        )

        return Config(
            accounts=accounts,
            tax_rates=tax_rates,
            options=options,
        )

    except KeyError as e:
        raise ConfigError(f"Missing required config key: {e}")
