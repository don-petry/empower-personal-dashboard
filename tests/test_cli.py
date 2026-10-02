"""
test_cli.py — Unit tests for empower CLI.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from empower_personal_dashboard.cli import main as cli_main


class TestCLI(unittest.TestCase):
    def test_cli_sandbox_all_with_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_balances = Path(tmpdir) / "balances.json"
            out_holdings = Path(tmpdir) / "holdings.json"
            out_txs = Path(tmpdir) / "transactions.jsonl"

            argv = [
                "empower",
                "--sandbox",
                "--all",
                "--csv",
                "--output",
                str(out_balances),
                "--output-holdings",
                str(out_holdings),
                "--output-transactions",
                str(out_txs),
                "--format",
                "markdown",
                "--quiet",
            ]

            with patch.object(sys, "argv", argv):
                exit_code = cli_main()
                self.assertEqual(exit_code, 0)

            # Check JSON/JSONL outputs
            self.assertTrue(out_balances.exists())
            self.assertTrue(out_holdings.exists())
            self.assertTrue(out_txs.exists())

            # Check CSV outputs
            self.assertTrue(out_balances.with_suffix(".csv").exists())
            self.assertTrue(out_holdings.with_suffix(".csv").exists())
            self.assertTrue(out_txs.with_suffix(".csv").exists())

            with open(out_balances, "r", encoding="utf-8") as f:
                b_data = json.load(f)
                self.assertEqual(b_data["mode"], "sandbox_mock")
                self.assertGreater(b_data["net_worth"], 0)

    def test_cli_sandbox_json_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_balances = Path(tmpdir) / "balances.json"
            argv = [
                "empower",
                "--sandbox",
                "--balances",
                "--output",
                str(out_balances),
                "--format",
                "json",
                "--quiet",
            ]

            with patch.object(sys, "argv", argv):
                exit_code = cli_main()
                self.assertEqual(exit_code, 0)

            self.assertTrue(out_balances.exists())

    def test_cli_sandbox_beancount_modular_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_ledger = Path(tmpdir) / "ledger"
            argv = [
                "empower",
                "--sandbox",
                "--all",
                "--beancount",
                "--output-beancount",
                str(out_ledger),
                "--quiet",
            ]

            with patch.object(sys, "argv", argv):
                exit_code = cli_main()
                self.assertEqual(exit_code, 0)

            self.assertTrue((out_ledger / "main.bean").exists())
            self.assertTrue((out_ledger / "accounts.bean").exists())
            self.assertTrue((out_ledger / "balances.bean").exists())
            self.assertTrue((out_ledger / "prices.bean").exists())
            self.assertTrue((out_ledger / "transactions.bean").exists())

    def test_cli_sandbox_beancount_single_file_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "export.bean"
            argv = [
                "empower",
                "--sandbox",
                "--all",
                "--beancount",
                "--output-beancount",
                str(out_file),
                "--quiet",
            ]

            with patch.object(sys, "argv", argv):
                exit_code = cli_main()
                self.assertEqual(exit_code, 0)

            self.assertTrue(out_file.exists())
            content = out_file.read_text(encoding="utf-8")
            self.assertIn("option \"title\" \"Empower Personal Dashboard Ledger\"", content)
            self.assertIn("open Equity:Opening-Balances USD", content)

    def test_cli_rejects_conflicting_append_and_overwrite(self):
        argv = [
            "empower",
            "--sandbox",
            "--all",
            "--beancount",
            "--append",
            "--overwrite-ledger",
        ]
        with patch.object(sys, "argv", argv):
            exit_code = cli_main()
            self.assertNotEqual(exit_code, 0)

    def test_cli_beancount_format_routes_progress_to_stderr(self):
        import io
        argv = [
            "empower",
            "--sandbox",
            "--holdings",
            "--format",
            "beancount",
        ]
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch.object(sys, "argv", argv), patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
            exit_code = cli_main()
            self.assertEqual(exit_code, 0)

        stdout_val = stdout_buf.getvalue()
        stderr_val = stderr_buf.getvalue()
        # Directives should be on stdout
        self.assertIn("open Equity:Opening-Balances", stdout_val)
        # Progress messages must NOT be on stdout
        self.assertNotIn("[*] Querying Empower", stdout_val)
        # Progress messages must be on stderr
        self.assertIn("[*] Querying Empower", stderr_val)

    def test_cli_holdings_only_beancount_includes_balance_assertions(self):
        import io
        argv = [
            "empower",
            "--sandbox",
            "--holdings",
            "--format",
            "beancount",
        ]
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch.object(sys, "argv", argv), patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
            exit_code = cli_main()
            self.assertEqual(exit_code, 0)

        stdout_val = stdout_buf.getvalue()
        # Holdings-only format must include commodity unit balance assertions
        self.assertIn("balance Assets:", stdout_val)


if __name__ == "__main__":
    unittest.main()

