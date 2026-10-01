"""
test_beancount.py — Hermetic unit tests for Beancount plain-text accounting export engine.
"""

import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from empower_personal_dashboard.models import (
    AccountBalance,
    DashboardBalances,
    DashboardHoldings,
    DashboardTransactions,
    InvestmentHolding,
    Transaction,
)
from empower_personal_dashboard.beancount import (
    BeancountGenerator,
    BeancountMapper,
    slugify_account_name,
)

# Standard Beancount account regex
BEANCOUNT_ACCOUNT_REGEX = re.compile(
    r"^(Assets|Liabilities|Equity|Income|Expenses):[A-Z0-9][A-Za-z0-9\-]*"
    r"(:[A-Z0-9][A-Za-z0-9\-]*)*$"
)


class TestBeancountAccountSlugification(unittest.TestCase):
    """Tests for direct firm naming and Beancount account slugification."""

    def test_direct_firm_naming_asset_accounts(self):
        slug = slugify_account_name(
            firm_name="Ally Bank",
            account_name="Interest Checking - 1234",
            account_type="bank",
        )
        self.assertEqual(slug, "Assets:AllyBank:InterestChecking-1234")
        self.assertTrue(BEANCOUNT_ACCOUNT_REGEX.match(slug))

    def test_direct_firm_naming_liability_accounts(self):
        slug = slugify_account_name(
            firm_name="Chase",
            account_name="Freedom Unlimited (...5678)",
            account_type="credit",
        )
        self.assertEqual(slug, "Liabilities:Chase:FreedomUnlimited-5678")
        self.assertTrue(BEANCOUNT_ACCOUNT_REGEX.match(slug))

    def test_mortgage_and_loan_slugification(self):
        slug = slugify_account_name(
            firm_name="Rocket Mortgage",
            account_name="Primary Home Loan",
            account_type="mortgage",
        )
        self.assertEqual(slug, "Liabilities:RocketMortgage:PrimaryHomeLoan")
        self.assertTrue(BEANCOUNT_ACCOUNT_REGEX.match(slug))

    def test_investment_account_slugification(self):
        slug = slugify_account_name(
            firm_name="Vanguard",
            account_name="Taxable Brokerage",
            account_type="investment",
        )
        self.assertEqual(slug, "Assets:Vanguard:TaxableBrokerage")
        self.assertTrue(BEANCOUNT_ACCOUNT_REGEX.match(slug))

    def test_sanitizes_unicode_artifacts_and_special_chars(self):
        slug = slugify_account_name(
            firm_name="Wells Fargo & Co. \ufffd",
            account_name="Way2Save\u2122 Checking ###",
            account_type="bank",
        )
        self.assertEqual(slug, "Assets:WellsFargoCo:Way2SaveChecking")
        self.assertTrue(BEANCOUNT_ACCOUNT_REGEX.match(slug))


class TestBeancountMapper(unittest.TestCase):
    """Tests for YAML mapping, payee regex rules, and category overrides."""

    def setUp(self):
        self.yaml_content = """
accounts:
  "Chase - Freedom Unlimited": "Liabilities:Chase:Freedom"
  "1001": "Assets:Custom:Checking"

categories:
  "Groceries": "Expenses:Food:Groceries"
  "Dining Out": "Expenses:Food:Restaurants"
  "Paycheck": "Income:Job:Salary"
  "Transfer": "Equity:Transfers"

regex_rules:
  - pattern: "(?i)whole foods|trader joe"
    account: "Expenses:Food:Groceries"
  - pattern: "(?i)starbucks"
    account: "Expenses:Food:Coffee"
"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.map_file = Path(self.temp_dir.name) / "map.yaml"
        self.map_file.write_text(self.yaml_content, encoding="utf-8")
        self.mapper = BeancountMapper(mapping_path=self.map_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_account_override_from_yaml(self):
        account = self.mapper.resolve_account(
            firm_name="Chase",
            account_name="Chase - Freedom Unlimited",
            account_id="9999",
            account_type="credit",
        )
        self.assertEqual(account, "Liabilities:Chase:Freedom")

    def test_account_override_by_id(self):
        account = self.mapper.resolve_account(
            firm_name="AnyFirm",
            account_name="Any Name",
            account_id="1001",
            account_type="bank",
        )
        self.assertEqual(account, "Assets:Custom:Checking")

    def test_category_resolution(self):
        acct = self.mapper.resolve_category_or_payee(
            category="Groceries",
            description="Acme Mart",
            is_spending=True,
            is_income=False,
        )
        self.assertEqual(acct, "Expenses:Food:Groceries")

    def test_payee_regex_rule_match(self):
        acct = self.mapper.resolve_category_or_payee(
            category="General Merchandise",
            description="TRADER JOE #1234 AUSTIN TX",
            is_spending=True,
            is_income=False,
        )
        self.assertEqual(acct, "Expenses:Food:Groceries")

    def test_default_fallbacks(self):
        spending = self.mapper.resolve_category_or_payee(
            category=None,
            description="Unknown Shop",
            is_spending=True,
            is_income=False,
        )
        self.assertEqual(spending, "Expenses:Uncategorized")

        income = self.mapper.resolve_category_or_payee(
            category=None,
            description="Mystery Deposit",
            is_spending=False,
            is_income=True,
        )
        self.assertEqual(income, "Income:Uncategorized")

    def test_yaml_mapping_without_pyyaml(self):
        with patch.dict("sys.modules", {"yaml": None}):
            mapper = BeancountMapper(mapping_path=self.map_file)
            acct = mapper.resolve_account("Chase", "Chase - Freedom Unlimited", "9999", "credit")
            self.assertEqual(acct, "Liabilities:Chase:Freedom")
            cat = mapper.resolve_category_or_payee("Groceries", "Any Store", True, False)
            self.assertEqual(cat, "Expenses:Food:Groceries")
            regex_acct = mapper.resolve_category_or_payee("Other", "WHOLE FOODS #1023", True, False)
            self.assertEqual(regex_acct, "Expenses:Food:Groceries")


class TestBeancountGenerator(unittest.TestCase):
    """Tests for generating compliant Beancount directives."""

    def setUp(self):
        self.generator = BeancountGenerator()

        self.synthetic_balances = DashboardBalances(
            as_of_date="2026-10-01",
            net_worth=15000.0,
            total_cash=5000.0,
            total_investment=12000.0,
            total_credit_card=2000.0,
            total_loan=0.0,
            total_mortgage=0.0,
            accounts=[
                {
                    "account_id": "acc-1",
                    "account_name": "Everyday Checking",
                    "firm_name": "Ally Bank",
                    "account_type": "bank",
                    "balance": 5000.0,
                    "is_asset": True,
                    "currency": "USD",
                },
                {
                    "account_id": "acc-2",
                    "account_name": "Sapphire Reserve",
                    "firm_name": "Chase",
                    "account_type": "credit",
                    "balance": 2000.0,
                    "is_asset": False,
                    "currency": "USD",
                },
            ],
        )

        self.synthetic_holdings = DashboardHoldings(
            as_of_date="2026-10-01",
            total_value=12000.0,
            holdings=[
                {
                    "user_account_id": 101,
                    "account_name": "Brokerage",
                    "firm_name": "Vanguard",
                    "ticker": "VTI",
                    "cusip": "922908769",
                    "description": "Vanguard Total Stock Market ETF",
                    "holding_type": "equity",
                    "quantity": 40.0,
                    "price": 280.0,
                    "value": 11200.0,
                    "cost_basis": 8800.0,
                },
                {
                    "user_account_id": 101,
                    "account_name": "Brokerage",
                    "firm_name": "Vanguard",
                    "ticker": "BND",
                    "cusip": "921937835",
                    "description": "Vanguard Total Bond Market ETF",
                    "holding_type": "equity",
                    "quantity": 10.0,
                    "price": 80.0,
                    "value": 800.0,
                    "cost_basis": None,
                },
            ],
        )

        self.synthetic_transactions = DashboardTransactions(
            start_date="2026-09-01",
            end_date="2026-09-30",
            total_transactions=3,
            money_in=3000.0,
            money_out=150.0,
            net_cashflow=2850.0,
            transactions=[
                {
                    "user_transaction_id": "tx-101",
                    "account_id": "acc-2",
                    "account_name": "Sapphire Reserve",
                    "firm_name": "Chase",
                    "account_type": "credit",
                    "transaction_date": "2026-09-15",
                    "description": "WHOLE FOODS MARKET",
                    "original_description": "WHOLE FOODS #1023",
                    "amount": 100.0,
                    "is_credit": False,
                    "is_cash_in": False,
                    "is_cash_out": True,
                    "is_income": False,
                    "is_spending": True,
                    "transaction_type": "spending",
                    "category_id": 12,
                    "category": "Groceries",
                },
                {
                    "user_transaction_id": "tx-102",
                    "account_id": "acc-1",
                    "account_name": "Everyday Checking",
                    "firm_name": "Ally Bank",
                    "account_type": "bank",
                    "transaction_date": "2026-09-20",
                    "description": "EMPLOYER PAYROLL",
                    "original_description": "DIRECT DEP ACME CORP",
                    "amount": 3000.0,
                    "is_credit": True,
                    "is_cash_in": True,
                    "is_cash_out": False,
                    "is_income": True,
                    "is_spending": False,
                    "transaction_type": "income",
                    "category_id": 1,
                    "category": "Paycheck",
                },
                {
                    "user_transaction_id": "tx-103",
                    "account_id": "acc-1",
                    "account_name": "Everyday Checking",
                    "firm_name": "Ally Bank",
                    "account_type": "bank",
                    "transaction_date": "2026-09-25",
                    "description": "COFFEE SHOP",
                    "original_description": "COFFEE SHOP MAIN ST",
                    "amount": 5.0,
                    "is_credit": False,
                    "is_cash_in": False,
                    "is_cash_out": True,
                    "is_income": False,
                    "is_spending": True,
                    "transaction_type": "spending",
                    "category_id": 15,
                    "category": "Restaurants",
                },
            ],
        )

    def test_generate_account_open_and_pad_directives(self):
        output = self.generator.generate_accounts_bean(self.synthetic_balances)
        self.assertIn("open Assets:AllyBank:EverydayChecking USD", output)
        self.assertIn("open Liabilities:Chase:SapphireReserve USD", output)
        self.assertIn("open Equity:Opening-Balances USD", output)
        self.assertIn("open Equity:Transfers USD", output)
        self.assertIn("pad Assets:AllyBank:EverydayChecking Equity:Opening-Balances", output)
        self.assertIn("pad Liabilities:Chase:SapphireReserve Equity:Opening-Balances", output)

    def test_generate_balance_assertions(self):
        output = self.generator.generate_balances_bean(
            balances=self.synthetic_balances,
            holdings=self.synthetic_holdings,
        )
        # Cash asset balance is positive
        self.assertIn("2026-10-01 balance Assets:AllyBank:EverydayChecking 5000.00 USD", output)
        # Credit liability balance is asserted as negative in Beancount
        self.assertIn("2026-10-01 balance Liabilities:Chase:SapphireReserve -2000.00 USD", output)
        # Commodity balance assertions
        self.assertIn("2026-10-01 balance Assets:Vanguard:Brokerage 40.00 VTI", output)
        self.assertIn("2026-10-01 balance Assets:Vanguard:Brokerage 10.00 BND", output)

    def test_generate_prices_bean(self):
        output = self.generator.generate_prices_bean(self.synthetic_holdings)
        self.assertIn("2026-10-01 price VTI 280.00 USD", output)
        self.assertIn("2026-10-01 price BND 80.00 USD", output)

    def test_generate_transactions_double_entry_balance(self):
        output = self.generator.generate_transactions_bean(self.synthetic_transactions)

        # Check Whole Foods transaction
        self.assertIn('2026-09-15 * "WHOLE FOODS MARKET" "Groceries"', output)
        self.assertIn('empower_id: "tx-101"', output)
        self.assertIn('^empower-tx-101', output)
        self.assertIn("Liabilities:Chase:SapphireReserve     -100.00 USD", output)
        self.assertIn("Expenses:Uncategorized                 100.00 USD", output)

        # Check Payroll income transaction
        self.assertIn('2026-09-20 * "EMPLOYER PAYROLL" "Paycheck"', output)
        self.assertIn("Assets:AllyBank:EverydayChecking      3000.00 USD", output)
        self.assertIn("Income:Uncategorized                 -3000.00 USD", output)

        # Check Coffee Shop spending
        self.assertIn('2026-09-25 * "COFFEE SHOP" "Restaurants"', output)
        self.assertIn("Assets:AllyBank:EverydayChecking        -5.00 USD", output)
        self.assertIn("Expenses:Uncategorized                   5.00 USD", output)

    def test_generate_modular_directory_export(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "ledger"
            created_files = self.generator.export_modular_ledger(
                destination_dir=out_path,
                balances=self.synthetic_balances,
                holdings=self.synthetic_holdings,
                transactions=self.synthetic_transactions,
            )

            self.assertTrue((out_path / "main.bean").exists())
            self.assertTrue((out_path / "accounts.bean").exists())
            self.assertTrue((out_path / "balances.bean").exists())
            self.assertTrue((out_path / "prices.bean").exists())
            self.assertTrue((out_path / "transactions.bean").exists())

            main_content = (out_path / "main.bean").read_text(encoding="utf-8")
            self.assertIn('include "accounts.bean"', main_content)
            self.assertIn('include "balances.bean"', main_content)
            self.assertIn('include "prices.bean"', main_content)
            self.assertIn('include "transactions.bean"', main_content)

    def test_generate_holdings_lots_with_cost_basis(self):
        output = self.generator.generate_holdings_bean(self.synthetic_holdings)
        # VTI has cost basis 8800.0 / 40.0 = 220.00 USD
        self.assertIn("40.00 VTI {220.00 USD} @ 280.00 USD", output)
        # BND has no cost basis -> formatted with @ price
        self.assertIn("10.00 BND @ 80.00 USD", output)

    def test_generate_single_file_export(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            single_file = Path(tmp_dir) / "all.bean"
            self.generator.export_single_file(
                filepath=single_file,
                balances=self.synthetic_balances,
                holdings=self.synthetic_holdings,
                transactions=self.synthetic_transactions,
            )
            self.assertTrue(single_file.exists())
            content = single_file.read_text(encoding="utf-8")
            self.assertIn("open Assets:AllyBank:EverydayChecking USD", content)
            self.assertIn("2026-10-01 balance Assets:AllyBank:EverydayChecking 5000.00 USD", content)
            self.assertIn("2026-10-01 price VTI 280.00 USD", content)
            self.assertIn('2026-09-15 * "WHOLE FOODS MARKET"', content)


if __name__ == "__main__":
    unittest.main()
