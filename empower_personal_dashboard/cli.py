"""
cli.py — Command-line interface for Empower Personal Dashboard.

Usage:
  # One-time interactive setup with 2FA:
  empower --login

  # Unattended balance extraction:
  empower

  # Extract investment holdings:
  empower --holdings --csv

  # Extract transactions:
  empower --transactions --start-date 2026-01-01 --end-date 2026-09-30 --limit 50

  # Full extraction (balances, holdings, transactions):
  empower --all --csv

  # Offline sandbox mode (test without network or credentials):
  empower --sandbox --all --csv
"""

import argparse
import csv
import getpass
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .client import (
    DEFAULT_BASE_URL,
    DEFAULT_SESSION_FILE,
    MIGRATED_BASE_URL,
    EmpowerDashboardClient,
)
from .exceptions import (
    EmpowerError,
    LoginFailedException,
    RequireTwoFactorException,
    SessionExpiredError,
)
from .models import DashboardBalances, DashboardHoldings, DashboardTransactions

DEFAULT_OUTPUT_DIR = Path.cwd() / "data"
DEFAULT_BALANCES_FILE = DEFAULT_OUTPUT_DIR / "empower_balances.json"
DEFAULT_HOLDINGS_FILE = DEFAULT_OUTPUT_DIR / "empower_holdings.json"
DEFAULT_TRANSACTIONS_FILE = DEFAULT_OUTPUT_DIR / "empower_transactions.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="empower",
        description="Extract account balances, investment holdings, and transaction history via Empower Personal Dashboard.",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Run interactive login to authenticate with 2FA and save persistent session.",
    )
    parser.add_argument(
        "--balances",
        action="store_true",
        help="Extract account balances and net worth snapshot.",
    )
    parser.add_argument(
        "--holdings",
        action="store_true",
        help="Extract investment holdings and positions.",
    )
    parser.add_argument(
        "--transactions",
        action="store_true",
        help="Extract individual account transactions.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Extract balances, holdings, and transactions in a single run.",
    )
    parser.add_argument(
        "--session-file",
        type=Path,
        default=DEFAULT_SESSION_FILE,
        help=f"Path to session storage file (default: {DEFAULT_SESSION_FILE}).",
    )
    parser.add_argument(
        "--sandbox",
        "--mock",
        action="store_true",
        help="Force execution in offline sandbox/mock mode using synthetic benchmark accounts.",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Transactions start date (YYYY-MM-DD). Defaults to Jan 1 of current year.",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="Transactions end date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--account-id",
        type=str,
        default=None,
        help="Filter transactions by specific account ID (userAccountId).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of transactions or holdings to process/display.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_BALANCES_FILE,
        help=f"File path to save balance snapshot JSON (default: {DEFAULT_BALANCES_FILE}).",
    )
    parser.add_argument(
        "--output-holdings",
        type=Path,
        default=DEFAULT_HOLDINGS_FILE,
        help=f"File path to save holdings JSON (default: {DEFAULT_HOLDINGS_FILE}).",
    )
    parser.add_argument(
        "--output-transactions",
        type=Path,
        default=DEFAULT_TRANSACTIONS_FILE,
        help=f"File path to save transactions JSON or JSONL (default: {DEFAULT_TRANSACTIONS_FILE}).",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Export companion CSV files alongside JSON output.",
    )
    parser.add_argument(
        "--email",
        "--username",
        type=str,
        default=None,
        help="Empower email/username (optional, otherwise reads env or prompts).",
    )
    parser.add_argument(
        "--password",
        type=str,
        default=None,
        help="Empower password (optional, otherwise reads env or prompts).",
    )
    parser.add_argument(
        "--mode",
        choices=["SMS", "EMAIL"],
        default=None,
        help="2FA challenge delivery method (SMS or EMAIL).",
    )
    parser.add_argument(
        "--code",
        type=str,
        default=None,
        help="6-digit 2FA verification code.",
    )
    parser.add_argument(
        "--format",
        choices=["table", "markdown", "json"],
        default="table",
        help="Console output format (table, markdown, json).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    parser.add_argument(
        "--migrated",
        action="store_true",
        help="Connect directly to Empower unified migrated API (https://pc-api.empower-retirement.com).",
    )
    parser.add_argument(
        "--api-host",
        type=str,
        default=None,
        help="Custom base API host URL.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="File path to write detailed debug logs.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress console output, only writing files.",
    )
    return parser.parse_args()


def format_currency(val: float) -> str:
    if val < 0:
        return f"-${abs(val):,.2f}"
    return f"${val:,.2f}"


def export_balances_csv(balances: DashboardBalances, filepath: Path) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["institution", "account_name", "account_type", "balance", "is_asset", "currency", "last_refreshed"]
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for acct in balances.accounts:
            writer.writerow({
                "institution": acct.get("firm_name", ""),
                "account_name": acct.get("account_name", ""),
                "account_type": acct.get("account_type", ""),
                "balance": acct.get("balance", 0.0),
                "is_asset": acct.get("is_asset", True),
                "currency": acct.get("currency", "USD"),
                "last_refreshed": acct.get("last_refreshed", ""),
            })


def export_holdings_csv(holdings: DashboardHoldings, filepath: Path) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "account_name", "ticker", "cusip", "description", "holding_type",
        "quantity", "price", "value", "cost_basis", "holding_percentage",
        "one_day_percent_change", "one_day_value_change"
    ]
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for h in holdings.holdings:
            writer.writerow({
                "account_name": h.get("account_name", ""),
                "ticker": h.get("ticker", ""),
                "cusip": h.get("cusip", ""),
                "description": h.get("description", ""),
                "holding_type": h.get("holding_type", ""),
                "quantity": h.get("quantity", 0.0),
                "price": h.get("price", 0.0),
                "value": h.get("value", 0.0),
                "cost_basis": h.get("cost_basis") if h.get("cost_basis") is not None else "",
                "holding_percentage": h.get("holding_percentage") if h.get("holding_percentage") is not None else "",
                "one_day_percent_change": h.get("one_day_percent_change") if h.get("one_day_percent_change") is not None else "",
                "one_day_value_change": h.get("one_day_value_change") if h.get("one_day_value_change") is not None else "",
            })


def export_transactions_csv(transactions: DashboardTransactions, filepath: Path) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "transaction_date", "account_name", "description", "amount",
        "transaction_type", "investment_type", "symbol", "price", "quantity",
        "status", "category_id", "user_transaction_id"
    ]
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for tx in transactions.transactions:
            writer.writerow({
                "transaction_date": tx.get("transaction_date", ""),
                "account_name": tx.get("account_name", ""),
                "description": tx.get("description", ""),
                "amount": tx.get("amount", 0.0),
                "transaction_type": tx.get("transaction_type", ""),
                "investment_type": tx.get("investment_type") or "",
                "symbol": tx.get("symbol") or "",
                "price": tx.get("price") if tx.get("price") is not None else "",
                "quantity": tx.get("quantity") if tx.get("quantity") is not None else "",
                "status": tx.get("status", ""),
                "category_id": tx.get("category_id") if tx.get("category_id") is not None else "",
                "user_transaction_id": tx.get("user_transaction_id", ""),
            })


def render_balances_markdown(balances: DashboardBalances) -> str:
    lines = [
        f"### Empower Personal Dashboard Balance Snapshot ({balances.as_of_date})",
        "",
        f"- **Mode**: `{balances.mode}`",
        f"- **Total Net Worth**: **{format_currency(balances.net_worth)}**",
        f"- **Total Investments**: {format_currency(balances.total_investment)}",
        f"- **Total Cash / Banking**: {format_currency(balances.total_cash)}",
        f"- **Real Estate & Physical Assets**: {format_currency(balances.total_other_assets)}",
        f"- **Credit Card Liabilities**: {format_currency(balances.total_credit_card)}",
        f"- **Mortgages & Loans**: {format_currency(balances.total_loan + balances.total_mortgage)}",
        "",
        "#### Account Breakdown",
        "",
        "| Institution | Account Name | Type | Balance |",
        "| :--- | :--- | :--- | :--- |",
    ]

    for acct in balances.accounts:
        firm = acct.get("firm_name", "—")
        name = acct.get("account_name", "Account")
        acct_type = acct.get("account_type", "OTHER")
        bal = acct.get("balance", 0.0)
        lines.append(f"| {firm} | {name} | `{acct_type}` | {format_currency(bal)} |")

    return "\n".join(lines)


def render_balances_table(balances: DashboardBalances) -> str:
    md = render_balances_markdown(balances)
    divider = "=" * 80
    return f"{divider}\n{md}\n{divider}"


def render_holdings_markdown(
    holdings: DashboardHoldings,
    limit: Optional[int] = 25,
    preserve_order: bool = False,
) -> str:
    title_suffix = "" if preserve_order else " by Value"
    lines = [
        f"### Empower Personal Dashboard Holdings ({holdings.as_of_date})",
        "",
        f"- **Mode**: `{holdings.mode}`",
        f"- **Total Portfolio Value**: **{format_currency(holdings.total_value)}**",
        f"- **Total Positions**: {len(holdings.holdings)}",
        "",
        f"#### Top Positions{title_suffix}",
        "",
        "| Ticker | Description | Account | Type | Shares | Price | Value | Weight | 1-Day Chg |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    sorted_holdings = holdings.holdings if preserve_order else sorted(holdings.holdings, key=lambda x: x.get("value", 0.0), reverse=True)
    display_holdings = sorted_holdings[:limit] if limit else sorted_holdings

    for h in display_holdings:
        ticker = h.get("ticker") or "—"
        desc = h.get("description", "—")
        if len(desc) > 35:
            desc = desc[:32] + "..."
        acct = h.get("account_name", "—")
        if len(acct) > 25:
            acct = acct[:22] + "..."
        htype = h.get("holding_type", "OTHER")
        shares = f"{h.get('quantity', 0.0):,.2f}"
        price = format_currency(h.get("price", 0.0))
        val = format_currency(h.get("value", 0.0))
        weight = f"{h.get('holding_percentage', 0.0):.1f}%" if h.get("holding_percentage") is not None else "—"
        chg = format_currency(h.get("one_day_value_change", 0.0)) if h.get("one_day_value_change") is not None else "—"
        lines.append(f"| `{ticker}` | {desc} | {acct} | `{htype}` | {shares} | {price} | {val} | {weight} | {chg} |")

    if limit and len(sorted_holdings) > limit:
        lines.append("")
        lines.append(f"*... and {len(sorted_holdings) - limit} additional positions.*")

    return "\n".join(lines)


def render_holdings_table(
    holdings: DashboardHoldings,
    limit: Optional[int] = 25,
    preserve_order: bool = False,
) -> str:
    md = render_holdings_markdown(holdings, limit=limit, preserve_order=preserve_order)
    divider = "=" * 80
    return f"{divider}\n{md}\n{divider}"


def render_transactions_markdown(transactions: DashboardTransactions, limit: Optional[int] = 25) -> str:
    lines = [
        f"### Empower Personal Dashboard Transactions ({transactions.start_date} to {transactions.end_date})",
        "",
        f"- **Mode**: `{transactions.mode}`",
        f"- **Total Transactions**: {transactions.total_transactions}",
        f"- **Money In**: {format_currency(transactions.money_in)}",
        f"- **Money Out**: {format_currency(transactions.money_out)}",
        f"- **Net Cashflow**: **{format_currency(transactions.net_cashflow)}**",
        "",
        "#### Recent Transactions",
        "",
        "| Date | Account | Description | Type | Amount | Status |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    display_txs = transactions.transactions[:limit] if limit else transactions.transactions

    for tx in display_txs:
        date = tx.get("transaction_date", "—")
        acct = tx.get("account_name", "—")
        if len(acct) > 25:
            acct = acct[:22] + "..."
        desc = tx.get("description", "—")
        if len(desc) > 35:
            desc = desc[:32] + "..."
        ttype = tx.get("transaction_type", "—")
        amt = tx.get("amount", 0.0)
        amt_str = f"+{format_currency(amt)}" if tx.get("is_credit") or tx.get("is_cash_in") else f"-{format_currency(amt)}"
        status = tx.get("status", "posted")
        lines.append(f"| {date} | {acct} | {desc} | `{ttype}` | {amt_str} | `{status}` |")

    if limit and len(transactions.transactions) > limit:
        lines.append("")
        lines.append(f"*... and {len(transactions.transactions) - limit} additional transactions.*")

    return "\n".join(lines)


def render_transactions_table(transactions: DashboardTransactions, limit: Optional[int] = 25) -> str:
    md = render_transactions_markdown(transactions, limit=limit)
    divider = "=" * 80
    return f"{divider}\n{md}\n{divider}"


def interactive_login(
    client: EmpowerDashboardClient,
    session_file: Path,
    cli_email: Optional[str] = None,
    cli_password: Optional[str] = None,
    cli_mode: Optional[str] = None,
    cli_code: Optional[str] = None,
) -> int:
    """Run interactive 2FA login workflow."""
    print("=" * 68)
    print("  Empower Personal Dashboard — Login & 2FA Setup")
    print("=" * 68)

    email = cli_email or os.environ.get("EMPOWER_USERNAME") or os.environ.get("PERSONAL_CAPITAL_EMAIL")
    if not email:
        try:
            email = input("Empower Email / Username: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[!] Canceled.", file=sys.stderr)
            return 1

    password = cli_password or os.environ.get("EMPOWER_PASSWORD") or os.environ.get("PERSONAL_CAPITAL_PASSWORD")
    if not password:
        try:
            password = getpass.getpass("Empower Password: ")
        except (EOFError, KeyboardInterrupt):
            print("\n[!] Canceled.", file=sys.stderr)
            return 1

    try:
        print(f"[*] Identifying user '{email}'...")
        try:
            client.login(username=email, password=password)
            print("[+] Login successful! No additional 2FA needed for this device.")
        except RequireTwoFactorException:
            print("[*] Two-factor authentication (2FA) is required.")

            mode = cli_mode
            if not mode:
                mode_input = input("Send 2FA challenge via [S]MS or [E]mail? (default: SMS): ").strip().upper()
                mode = "EMAIL" if mode_input.startswith("E") else "SMS"

            print(f"[*] Requesting 2FA challenge code via {mode}...")
            client.request_2fa_challenge(mode=mode)
            print(f"[+] Verification challenge accepted by Empower! Check your {mode.lower()} for code.")

            code = cli_code
            if not code:
                code = input("Enter the 6-digit verification code: ").strip()

            print("[*] Submitting verification code...")
            client.submit_2fa_code(code=code, mode=mode)
            print("[+] 2FA verified successfully.")

            print("[*] Finalizing device registration with password...")
            client.authenticate_password(password=password)
            print("[+] Password authenticated and device registered.")

        saved_path = client.save_session(session_file)
        print(f"[+] Session saved successfully to: {saved_path} (mode 0600)")
        print("[+] You can now run unattended periodic extractions without prompts.")
        return 0

    except LoginFailedException as e:
        print(f"[!] Login failed: {e}", file=sys.stderr)
        return 1
    except EmpowerError as e:
        print(f"[!] Empower error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[!] Unexpected error during login: {e}", file=sys.stderr)
        return 1


def main() -> int:
    args = parse_args()

    base_url = args.api_host
    if not base_url and args.migrated:
        base_url = MIGRATED_BASE_URL

    client = EmpowerDashboardClient(
        session_file=args.session_file,
        base_url=base_url or DEFAULT_BASE_URL,
        mock_mode=args.sandbox,
        debug=args.debug,
        log_file=args.log_file,
    )

    if args.login:
        return interactive_login(
            client=client,
            session_file=args.session_file,
            cli_email=args.email,
            cli_password=args.password,
            cli_mode=args.mode,
            cli_code=args.code,
        )

    # Determine execution mode
    has_session = args.session_file.exists()
    if not has_session and not args.sandbox:
        if not args.quiet:
            print(f"[*] No session file found at {args.session_file}.")
            print("[*] Defaulting to sandbox/mock mode. Run with '--login' to connect live.")
        client.mock_mode = True

    if not args.quiet:
        mode_label = "SANDBOX / MOCK MODE" if client.mock_mode else "LIVE"
        print(f"[*] Querying Empower Personal Dashboard [{mode_label}]...")

    do_balances = args.balances or args.all or (not args.holdings and not args.transactions)
    do_holdings = args.holdings or args.all
    do_transactions = args.transactions or args.all

    balances_res: Optional[DashboardBalances] = None
    holdings_res: Optional[DashboardHoldings] = None
    transactions_res: Optional[DashboardTransactions] = None

    try:
        if do_balances:
            balances_res = client.fetch_balances()
            b_data = balances_res.to_dict()
            b_data["extracted_at"] = datetime.now(timezone.utc).isoformat()

            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with open(args.output, "w", encoding="utf-8") as f:
                    json.dump(b_data, f, indent=2, ensure_ascii=False)
                if not args.quiet:
                    print(f"[+] Balances snapshot saved to: {args.output}")

            if args.csv and args.output:
                csv_path = args.output.with_suffix(".csv")
                export_balances_csv(balances_res, csv_path)
                if not args.quiet:
                    print(f"[+] Balances CSV saved to: {csv_path}")

        if do_holdings:
            holdings_res = client.fetch_holdings()
            h_data = holdings_res.to_dict()
            h_data["extracted_at"] = datetime.now(timezone.utc).isoformat()

            if args.output_holdings:
                args.output_holdings.parent.mkdir(parents=True, exist_ok=True)
                with open(args.output_holdings, "w", encoding="utf-8") as f:
                    json.dump(h_data, f, indent=2, ensure_ascii=False)
                if not args.quiet:
                    print(f"[+] Holdings snapshot saved to: {args.output_holdings}")

            if args.csv and args.output_holdings:
                csv_path = args.output_holdings.with_suffix(".csv")
                export_holdings_csv(holdings_res, csv_path)
                if not args.quiet:
                    print(f"[+] Holdings CSV saved to: {csv_path}")

        if do_transactions:
            start_date = args.start_date or f"{datetime.now(timezone.utc).year}-01-01"
            end_date = args.end_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
            transactions_res = client.fetch_transactions(
                start_date=start_date,
                end_date=end_date,
                user_account_ids=args.account_id,
                limit=args.limit,
            )
            t_data = transactions_res.to_dict()
            t_data["extracted_at"] = datetime.now(timezone.utc).isoformat()

            if args.output_transactions:
                args.output_transactions.parent.mkdir(parents=True, exist_ok=True)
                if str(args.output_transactions).endswith(".jsonl"):
                    with open(args.output_transactions, "w", encoding="utf-8") as f:
                        for tx in transactions_res.transactions:
                            f.write(json.dumps(tx, ensure_ascii=False) + "\n")
                else:
                    with open(args.output_transactions, "w", encoding="utf-8") as f:
                        json.dump(t_data, f, indent=2, ensure_ascii=False)
                if not args.quiet:
                    print(f"[+] Transactions saved to: {args.output_transactions}")

            if args.csv and args.output_transactions:
                csv_path = args.output_transactions.with_suffix(".csv")
                export_transactions_csv(transactions_res, csv_path)
                if not args.quiet:
                    print(f"[+] Transactions CSV saved to: {csv_path}")

    except SessionExpiredError as e:
        print(f"[!] {e}", file=sys.stderr)
        print("[!] Your session has expired. Re-authenticate by running: empower --login", file=sys.stderr)
        return 1
    except EmpowerError as e:
        print(f"[!] Empower API error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[!] Unexpected error: {e}", file=sys.stderr)
        return 1

    if not args.quiet:
        if args.format == "json":
            combined = {}
            if balances_res:
                combined["balances"] = balances_res.to_dict()
            if holdings_res:
                combined["holdings"] = holdings_res.to_dict()
            if transactions_res:
                combined["transactions"] = transactions_res.to_dict()
            print(json.dumps(combined, indent=2))
        elif args.format == "markdown":
            if balances_res:
                print(render_balances_markdown(balances_res))
            if holdings_res:
                print(render_holdings_markdown(holdings_res, limit=args.limit or 25))
            if transactions_res:
                print(render_transactions_markdown(transactions_res, limit=args.limit or 25))
        else:
            if balances_res:
                print(render_balances_table(balances_res))
            if holdings_res:
                print(render_holdings_table(holdings_res, limit=args.limit or 25))
            if transactions_res:
                print(render_transactions_table(transactions_res, limit=args.limit or 25))

    return 0


if __name__ == "__main__":
    sys.exit(main())
