"""
beancount.py — Pure-Python Beancount Plain-Text Accounting (PTA) export engine.

Provides zero-heavy-dependency formatting for Beancount directives:
- Account taxonomy with Direct Firm Naming (Assets:Firm:Account, Liabilities:Firm:Account)
- Double-entry transaction balancing with category mapping and suspense accounts
- Ground-truth balance assertions for cash, liabilities, and investment commodities
- Real-time commodity price directives from portfolio holdings
- Modular and single-file export generation adhering to Beancount 2.x and 3.x standards
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from empower_personal_dashboard.models import (
    DashboardBalances,
    DashboardHoldings,
    DashboardTransactions,
)
from empower_personal_dashboard.sanitizers import clean_api_text

logger = logging.getLogger(__name__)

# Strict Beancount account regex:
# Must start with one of 5 root types, each segment starts with capital letter or digit
BEANCOUNT_ACCOUNT_REGEX = re.compile(
    r"^(Assets|Liabilities|Equity|Income|Expenses):[A-Z0-9][A-Za-z0-9\-]*"
    r"(:[A-Z0-9][A-Za-z0-9\-]*)*$"
)


def _clean_segment(text: str) -> str:
    """Normalize and format a text string into a valid Beancount account subsegment."""
    cleaned = clean_api_text(text)
    raw_tokens = re.findall(r"[A-Za-z0-9]+", cleaned)
    if not raw_tokens:
        return "Unknown"
    parts = []
    for token in raw_tokens:
        if token[0].islower():
            parts.append(token[0].upper() + token[1:])
        else:
            parts.append(token)
    segment = "".join(parts)
    # If the segment contains numbers preceded by words, preserve clean hyphenation (e.g. Checking-1234)
    m = re.match(r"^([A-Za-z]+)(\d+)$", segment)
    if m:
        segment = f"{m.group(1)}-{m.group(2)}"
    # Ensure segment starts with an uppercase letter or number
    if not segment[0].isalnum() or segment[0].islower():
        segment = "A" + segment
    return segment


def slugify_account_name(
    firm_name: str,
    account_name: str,
    account_type: str,
) -> str:
    """Derive a compliant Beancount account name using Direct Firm Naming.

    Examples:
        - Ally Bank, Interest Checking - 1234, bank -> Assets:AllyBank:InterestChecking-1234
        - Chase, Freedom Unlimited (...5678), credit -> Liabilities:Chase:FreedomUnlimited-5678
        - Vanguard, Taxable Brokerage, investment -> Assets:Vanguard:TaxableBrokerage
        - Rocket Mortgage, Primary Loan, mortgage -> Liabilities:RocketMortgage:PrimaryHomeLoan

    Args:
        firm_name: Name of the financial firm / institution.
        account_name: Name of the individual account.
        account_type: Empower account category (bank, credit, investment, mortgage, etc.).

    Returns:
        A strictly valid Beancount account name string.
    """
    type_lower = (account_type or "").strip().lower()
    if type_lower in ("credit", "loan", "mortgage", "other_liabilities", "other_liability"):
        root = "Liabilities"
    else:
        root = "Assets"

    firm_seg = _clean_segment(firm_name or "Institution")
    acct_seg = _clean_segment(account_name or "Account")

    candidate = f"{root}:{firm_seg}:{acct_seg}"
    if not BEANCOUNT_ACCOUNT_REGEX.match(candidate):
        # Fallback to safe alphanumeric characters
        firm_safe = re.sub(r"[^A-Za-z0-9]", "", firm_seg) or "Firm"
        acct_safe = re.sub(r"[^A-Za-z0-9]", "", acct_seg) or "Account"
        candidate = f"{root}:{firm_safe}:{acct_safe}"

    return candidate


class BeancountMapper:
    """Handles mapping of Empower accounts, categories, and payee rules to Beancount accounts."""

    def __init__(self, mapping_path: Optional[Union[str, Path]] = None):
        self.accounts: Dict[str, str] = {}
        self.categories: Dict[str, str] = {}
        self.regex_rules: List[Dict[str, str]] = []

        if mapping_path:
            self.load_mapping(mapping_path)

    def load_mapping(self, mapping_path: Union[str, Path]) -> None:
        """Load mapping rules from a YAML or JSON configuration file."""
        p = Path(mapping_path).expanduser().resolve()
        if not p.exists():
            logger.warning("Beancount mapping file not found: %s", p)
            return

        content = p.read_text(encoding="utf-8")
        data: Dict[str, Any] = {}

        # Attempt to load using PyYAML if available
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(content) or {}
        except ImportError:
            # Fallback to JSON parsing if PyYAML is not installed
            try:
                data = json.loads(content)
            except Exception as e:
                logger.warning(
                    "PyYAML is not installed and mapping file is not valid JSON. "
                    "Install 'pyyaml' to use YAML mapping files: %s",
                    e,
                )
                return

        self.accounts = {str(k): str(v) for k, v in data.get("accounts", {}).items()}
        self.categories = {str(k): str(v) for k, v in data.get("categories", {}).items()}
        self.regex_rules = data.get("regex_rules", [])

    def resolve_account(
        self,
        firm_name: str,
        account_name: str,
        account_id: Optional[str] = None,
        account_type: str = "bank",
    ) -> str:
        """Resolve Beancount account name using overrides or slugification."""
        # Check explicit overrides by account_name or account_id
        if account_name and account_name in self.accounts:
            return self.accounts[account_name]
        if account_id and str(account_id) in self.accounts:
            return self.accounts[str(account_id)]

        return slugify_account_name(firm_name, account_name, account_type)

    def resolve_category_or_payee(
        self,
        category: Optional[str] = None,
        description: Optional[str] = None,
        is_spending: bool = True,
        is_income: bool = False,
    ) -> str:
        """Resolve the offsetting balancing leg for a transaction."""
        desc_clean = clean_api_text(description or "")

        # 1. Match regex payee rules first
        for rule in self.regex_rules:
            pattern = rule.get("pattern", "")
            target_acct = rule.get("account", "")
            if pattern and target_acct:
                if re.search(pattern, desc_clean):
                    return target_acct

        # 2. Match category name if available
        cat_clean = clean_api_text(category or "")
        if cat_clean and cat_clean in self.categories:
            return self.categories[cat_clean]

        # 3. Fallbacks based on transaction type
        if is_income:
            return "Income:Uncategorized"
        if is_spending:
            return "Expenses:Uncategorized"

        return "Expenses:Uncategorized"


class BeancountGenerator:
    """Generates Beancount directives and ledger files from Empower data models."""

    def __init__(self, mapper: Optional[BeancountMapper] = None):
        self.mapper = mapper or BeancountMapper()

    def generate_main_bean(self) -> str:
        """Generate the root main.bean linking modular components."""
        return (
            ";; ==============================================================================\n"
            ";; Empower Personal Dashboard - Root Beancount Ledger\n"
            ";; ==============================================================================\n\n"
            'option "title" "Empower Personal Dashboard Ledger"\n'
            'option "operating_currency" "USD"\n\n'
            'include "accounts.bean"\n'
            'include "balances.bean"\n'
            'include "prices.bean"\n'
            'include "transactions.bean"\n'
        )

    def generate_accounts_bean(
        self,
        balances: Optional[DashboardBalances] = None,
    ) -> str:
        """Generate account open and pad directives."""
        lines = [
            ";; ==============================================================================\n"
            ";; Empower Personal Dashboard - Account Declarations\n"
            ";; ==============================================================================\n\n"
            ";; Standard Equity & Suspense Accounts\n"
            "2000-01-01 open Equity:Opening-Balances USD\n"
            "2000-01-01 open Equity:Transfers USD\n"
            "2000-01-01 open Expenses:Uncategorized USD\n"
            "2000-01-01 open Income:Uncategorized USD\n\n"
            ";; Linked Institution Accounts & Pads\n"
        ]

        seen_accounts = set()
        if balances and balances.accounts:
            for acct in balances.accounts:
                firm = acct.get("firm_name") or "Institution"
                name = acct.get("account_name") or "Account"
                acct_id = str(acct.get("account_id") or "")
                acct_type = acct.get("account_type") or "bank"
                curr = acct.get("currency") or "USD"

                b_account = self.mapper.resolve_account(firm, name, acct_id, acct_type)
                if b_account not in seen_accounts:
                    seen_accounts.add(b_account)
                    lines.append(f"2000-01-01 open {b_account} {curr}\n")
                    lines.append(f"2000-01-01 pad {b_account} Equity:Opening-Balances\n")

        return "".join(lines)

    def generate_balances_bean(
        self,
        balances: Optional[DashboardBalances] = None,
        holdings: Optional[DashboardHoldings] = None,
    ) -> str:
        """Generate balance assertions for cash, liabilities, and investment commodities."""
        lines = [
            ";; ==============================================================================\n"
            ";; Empower Personal Dashboard - Balance Assertions\n"
            ";; ==============================================================================\n\n"
        ]

        if balances:
            as_of = balances.as_of_date
            lines.append(f";; Cash & Liability Balances (as of {as_of})\n")
            for acct in balances.accounts:
                firm = acct.get("firm_name") or "Institution"
                name = acct.get("account_name") or "Account"
                acct_id = str(acct.get("account_id") or "")
                acct_type = acct.get("account_type") or "bank"
                is_asset = acct.get("is_asset", True)
                curr = acct.get("currency") or "USD"
                raw_bal = float(acct.get("balance", 0.0))

                b_account = self.mapper.resolve_account(firm, name, acct_id, acct_type)
                # In Beancount, liabilities (credit cards, loans, mortgages) are represented as negative balances
                bal_amt = raw_bal if is_asset else -abs(raw_bal)
                lines.append(f"{as_of} balance {b_account} {bal_amt:.2f} {curr}\n")

        if holdings and holdings.holdings:
            as_of = holdings.as_of_date
            lines.append(f"\n;; Investment Commodity Unit Balances (as of {as_of})\n")
            for h in holdings.holdings:
                ticker = (h.get("ticker") or "").strip().upper()
                qty = float(h.get("quantity") or 0.0)
                acct_name = h.get("account_name") or "Brokerage"
                firm = h.get("firm_name") or "Vanguard"
                if ticker and qty > 0:
                    b_account = self.mapper.resolve_account(firm, acct_name, account_type="investment")
                    lines.append(f"{as_of} balance {b_account} {qty:.2f} {ticker}\n")

        return "".join(lines)

    def generate_prices_bean(
        self,
        holdings: Optional[DashboardHoldings] = None,
    ) -> str:
        """Generate price directives for investment commodities."""
        lines = [
            ";; ==============================================================================\n"
            ";; Empower Personal Dashboard - Commodity Price Points\n"
            ";; ==============================================================================\n\n"
        ]

        if holdings and holdings.holdings:
            as_of = holdings.as_of_date
            seen_tickers = set()
            for h in holdings.holdings:
                ticker = (h.get("ticker") or "").strip().upper()
                price = float(h.get("price") or 0.0)
                if ticker and price > 0 and ticker not in seen_tickers:
                    seen_tickers.add(ticker)
                    lines.append(f"{as_of} price {ticker} {price:.2f} USD\n")

        return "".join(lines)

    def generate_holdings_bean(
        self,
        holdings: Optional[DashboardHoldings] = None,
    ) -> str:
        """Generate investment positions with lot cost-basis and price tracking."""
        lines = [
            ";; ==============================================================================\n"
            ";; Empower Personal Dashboard - Portfolio Holdings & Lots\n"
            ";; ==============================================================================\n\n"
        ]

        if not holdings or not holdings.holdings:
            return "".join(lines)

        as_of = holdings.as_of_date
        for h in holdings.holdings:
            ticker = (h.get("ticker") or "").strip().upper()
            qty = float(h.get("quantity") or 0.0)
            price = float(h.get("price") or 0.0)
            cost_basis = h.get("cost_basis")
            acct_name = h.get("account_name") or "Brokerage"
            firm = h.get("firm_name") or "Vanguard"

            if ticker and qty > 0:
                b_account = self.mapper.resolve_account(firm, acct_name, account_type="investment")
                lines.append(f'{as_of} * "Empower Portfolio Snapshot" "{ticker} Position"\n')
                if cost_basis is not None and float(cost_basis) > 0:
                    unit_cost = float(cost_basis) / qty
                    lines.append(f"  {b_account:<36} {qty:>8.2f} {ticker} {{{unit_cost:.2f} USD}} @ {price:.2f} USD\n")
                else:
                    lines.append(f"  {b_account:<36} {qty:>8.2f} {ticker} @ {price:.2f} USD\n")
                lines.append(f"  {'Equity:Opening-Balances':<36}\n\n")

        return "".join(lines)

    def generate_transactions_bean(
        self,
        transactions: DashboardTransactions,
    ) -> str:
        """Generate balanced double-entry transaction directives."""
        lines = [
            ";; ==============================================================================\n"
            ";; Empower Personal Dashboard - Double-Entry Transactions\n"
            ";; ==============================================================================\n\n"
        ]

        if not transactions or not transactions.transactions:
            return "".join(lines)

        for tx in transactions.transactions:
            tx_id = str(tx.get("user_transaction_id") or "")
            acct_id = str(tx.get("account_id") or "")
            firm = tx.get("firm_name") or "Institution"
            acct_name = tx.get("account_name") or "Account"
            acct_type = tx.get("account_type") or "bank"
            date = tx.get("transaction_date") or "2000-01-01"
            payee = clean_api_text(tx.get("description") or "Unknown Payee")
            narration = clean_api_text(tx.get("category") or tx.get("original_description") or "")
            amount = float(tx.get("amount") or 0.0)

            is_credit = bool(tx.get("is_credit"))
            is_cash_in = bool(tx.get("is_cash_in"))
            is_income = bool(tx.get("is_income"))
            is_spending = bool(tx.get("is_spending", True))

            primary_account = self.mapper.resolve_account(firm, acct_name, acct_id, acct_type)
            category_account = self.mapper.resolve_category_or_payee(
                category=tx.get("category"),
                description=tx.get("description"),
                is_spending=is_spending,
                is_income=is_income,
            )

            # Determine double-entry posting signs:
            # - For Liabilities (credit cards, loans):
            #   Spending/Charges: Liabilities leg is negative (-amount), balancing leg (Expenses) is positive (+amount)
            #   Credits/Payments: Liabilities leg is positive (+amount), balancing leg (Transfers/Bank) is negative (-amount)
            # - For Assets (bank, cash):
            #   Cash in / Income: Assets leg is positive (+amount), balancing leg (Income) is negative (-amount)
            #   Cash out / Spending: Assets leg is negative (-amount), balancing leg (Expenses) is positive (+amount)
            is_liability = primary_account.startswith("Liabilities")

            if is_liability:
                if is_credit or is_cash_in:
                    acct_amount = amount
                    bal_amount = -amount
                else:
                    acct_amount = -amount
                    bal_amount = amount
            else:
                if is_credit or is_cash_in or is_income:
                    acct_amount = amount
                    bal_amount = -amount
                else:
                    acct_amount = -amount
                    bal_amount = amount

            # Format Beancount transaction block
            link_id = tx_id[3:] if tx_id.startswith("tx-") else tx_id
            lines.append(f'{date} * "{payee}" "{narration}" ^empower-tx-{link_id}\n')
            lines.append(f'  empower_id: "{tx_id}"\n')
            if acct_id:
                lines.append(f'  empower_account_id: "{acct_id}"\n')
            lines.append(f"  {primary_account:<36} {acct_amount:>8.2f} USD\n")
            lines.append(f"  {category_account:<36} {bal_amount:>8.2f} USD\n\n")

        return "".join(lines)

    def export_modular_ledger(
        self,
        destination_dir: Union[str, Path],
        balances: Optional[DashboardBalances] = None,
        holdings: Optional[DashboardHoldings] = None,
        transactions: Optional[DashboardTransactions] = None,
    ) -> List[Path]:
        """Export complete modular ledger directory (main.bean, accounts.bean, etc.)."""
        dest = Path(destination_dir).expanduser().resolve()
        dest.mkdir(parents=True, exist_ok=True)
        created_files: List[Path] = []

        # 1. main.bean
        main_path = dest / "main.bean"
        main_path.write_text(self.generate_main_bean(), encoding="utf-8")
        created_files.append(main_path)

        # 2. accounts.bean
        accounts_path = dest / "accounts.bean"
        accounts_path.write_text(self.generate_accounts_bean(balances), encoding="utf-8")
        created_files.append(accounts_path)

        # 3. balances.bean
        balances_path = dest / "balances.bean"
        balances_path.write_text(self.generate_balances_bean(balances, holdings), encoding="utf-8")
        created_files.append(balances_path)

        # 4. prices.bean
        prices_path = dest / "prices.bean"
        prices_path.write_text(self.generate_prices_bean(holdings), encoding="utf-8")
        created_files.append(prices_path)

        # 5. transactions.bean
        tx_path = dest / "transactions.bean"
        tx_content = self.generate_transactions_bean(transactions) if transactions else ""
        tx_path.write_text(tx_content, encoding="utf-8")
        created_files.append(tx_path)

        return created_files

    def export_single_file(
        self,
        filepath: Union[str, Path],
        balances: Optional[DashboardBalances] = None,
        holdings: Optional[DashboardHoldings] = None,
        transactions: Optional[DashboardTransactions] = None,
    ) -> Path:
        """Export all directives into a single comprehensive .bean ledger file."""
        target = Path(filepath).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        chunks = [
            ";; ==============================================================================\n"
            ";; Empower Personal Dashboard - Single Ledger Export\n"
            ";; ==============================================================================\n\n"
            'option "title" "Empower Personal Dashboard Ledger"\n'
            'option "operating_currency" "USD"\n\n',
            self.generate_accounts_bean(balances),
            "\n",
            self.generate_balances_bean(balances, holdings),
            "\n",
            self.generate_prices_bean(holdings),
            "\n",
        ]
        if transactions:
            chunks.append(self.generate_transactions_bean(transactions))

        target.write_text("".join(chunks), encoding="utf-8")
        return target
