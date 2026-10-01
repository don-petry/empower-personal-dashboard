"""Model Context Protocol (MCP) server for Empower Personal Dashboard.

Exposes aggregated financial data (net worth, balances, holdings, transactions)
to AI agents (Claude Desktop, Antigravity CLI, Cursor, Windsurf) over stdio or SSE.

Security Model:
    Out-of-band authentication: The user authenticates once via `empower --login`
    in their local terminal. The MCP server runs headlessly using the persisted
    session token (~/.empower_personal_dashboard_session.json, mode 0600).
    Passwords and 2FA SMS/Email codes are NEVER accepted or routed through the LLM context.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from empower_personal_dashboard.client import EmpowerDashboardClient
from empower_personal_dashboard.exceptions import (
    EmpowerError,
    LoginFailedException,
    RequireTwoFactorException,
    SessionExpiredError,
)
from empower_personal_dashboard.models import (
    DashboardBalances,
    DashboardHoldings,
    DashboardTransactions,
)

logger = logging.getLogger("empower_personal_dashboard.mcp")

# Compatibility import across MCP SDK versions (mcp 2.x MCPServer vs mcp 1.x FastMCP)
try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        FastMCP = None  # type: ignore[assignment,misc]


def _mask_account_identifier(ident: Any) -> str:
    """Mask sensitive account numbers or identifiers to only show last 4 characters."""
    if not ident:
        return ""
    s = str(ident).strip()
    if len(s) <= 4:
        return s
    return f"****{s[-4:]}"


def _sanitize_account_dict(acct: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize account record for safe presentation to LLMs."""
    clean = dict(acct)
    if "account_number" in clean and clean["account_number"]:
        clean["account_number"] = _mask_account_identifier(clean["account_number"])
    return clean


class _DataCache:
    """Thread-safe, lightweight in-memory TTL cache to prevent aggressive upstream polling."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl = ttl_seconds
        self.balances: Optional[tuple[float, DashboardBalances]] = None
        self.holdings: Optional[tuple[float, DashboardHoldings]] = None
        self.transactions: Dict[str, tuple[float, DashboardTransactions]] = {}

    def get_balances(self) -> Optional[DashboardBalances]:
        if self.balances and (time.time() - self.balances[0] < self.ttl):
            return self.balances[1]
        return None

    def set_balances(self, data: DashboardBalances) -> None:
        self.balances = (time.time(), data)

    def get_holdings(self) -> Optional[DashboardHoldings]:
        if self.holdings and (time.time() - self.holdings[0] < self.ttl):
            return self.holdings[1]
        return None

    def set_holdings(self, data: DashboardHoldings) -> None:
        self.holdings = (time.time(), data)

    def get_transactions(self, key: str) -> Optional[DashboardTransactions]:
        if key in self.transactions:
            ts, data = self.transactions[key]
            if time.time() - ts < self.ttl:
                return data
        return None

    def set_transactions(self, key: str, data: DashboardTransactions) -> None:
        self.transactions[key] = (time.time(), data)


def create_mcp_server(
    client: Optional[EmpowerDashboardClient] = None,
    cache_ttl_seconds: int = 300,
) -> Any:
    """Create and configure the FastMCP server instance.

    Args:
        client: Optional pre-configured EmpowerDashboardClient (useful for testing).
        cache_ttl_seconds: Time-to-live for in-memory balance/holding caches (default: 5 min).

    Returns:
        Configured FastMCP / MCPServer instance.
    """
    if FastMCP is None:
        raise ImportError(
            "The 'mcp' package is required to create the MCP server. "
            "Install it via: pip install 'empower-personal-dashboard[mcp]'"
        )

    server = FastMCP(
        "Empower Personal Dashboard",
        instructions=(
            "You have access to the user's aggregated financial data from Empower Personal Dashboard "
            "(formerly Personal Capital). Use these tools to query balances, net worth, investment holdings, "
            "and transactions. Never reveal or request master account passwords."
        ),
    )

    # Initialize client and cache
    empower_client = client or EmpowerDashboardClient()
    cache = _DataCache(ttl_seconds=cache_ttl_seconds)

    last_session_mtime: float = 0.0
    if not empower_client.mock_mode and empower_client.session_file.exists():
        try:
            last_session_mtime = empower_client.session_file.stat().st_mtime
        except Exception:
            pass

    def _ensure_session_fresh() -> None:
        nonlocal last_session_mtime
        if empower_client.mock_mode:
            return
        session_file = empower_client.session_file
        if session_file.exists():
            try:
                mtime = session_file.stat().st_mtime
                if mtime > last_session_mtime:
                    empower_client.load_session(session_file)
                    last_session_mtime = mtime
                    # Invalidate in-memory caches since session re-authenticated
                    cache.balances = None
                    cache.holdings = None
                    cache.transactions.clear()
            except Exception as e:
                logger.warning(f"Failed to refresh session from {session_file}: {e}")

    def _fetch_balances_cached() -> DashboardBalances:
        _ensure_session_fresh()
        cached = cache.get_balances()
        if cached:
            return cached
        try:
            fresh = empower_client.fetch_balances()
        except (SessionExpiredError, RequireTwoFactorException):
            _ensure_session_fresh()
            cache.balances = None
            raise
        cache.set_balances(fresh)
        return fresh

    def _fetch_holdings_cached() -> DashboardHoldings:
        _ensure_session_fresh()
        cached = cache.get_holdings()
        if cached:
            return cached
        try:
            fresh = empower_client.fetch_holdings()
        except (SessionExpiredError, RequireTwoFactorException):
            _ensure_session_fresh()
            cache.holdings = None
            raise
        cache.set_holdings(fresh)
        return fresh

    def _fetch_transactions_cached(start_date: Optional[str], end_date: Optional[str], limit: int) -> DashboardTransactions:
        _ensure_session_fresh()
        key = f"{start_date}_{end_date}_{limit}"
        cached = cache.get_transactions(key)
        if cached:
            return cached
        try:
            fresh = empower_client.fetch_transactions(start_date=start_date, end_date=end_date, limit=limit)
        except (SessionExpiredError, RequireTwoFactorException):
            _ensure_session_fresh()
            raise
        cache.set_transactions(key, fresh)
        return fresh

    # -------------------------------------------------------------------------
    # Tools
    # -------------------------------------------------------------------------

    @server.tool()
    def get_net_worth_summary() -> Dict[str, Any]:
        """Get an ultra-lightweight summary of current net worth and totals by asset class.

        Designed to minimize context window consumption (~100 tokens).

        Returns:
            Dict containing net_worth, total_cash, total_investment, total_credit,
            total_mortgage, total_loan, total_other_assets, total_other_liabilities,
            and total linked account count.
        """
        try:
            balances = _fetch_balances_cached()
            return {
                "status": "success",
                "net_worth": balances.net_worth,
                "total_cash": balances.total_cash,
                "total_investment": balances.total_investment,
                "total_credit": balances.total_credit_card,
                "total_credit_card": balances.total_credit_card,
                "total_mortgage": balances.total_mortgage,
                "total_loan": balances.total_loan,
                "total_other_assets": balances.total_other_assets,
                "total_other_liabilities": balances.total_other_liabilities,
                "accounts_count": len(balances.accounts),
            }
        except (SessionExpiredError, RequireTwoFactorException, FileNotFoundError):
            return {
                "status": "error",
                "error_code": "AUTH_REQUIRED",
                "message": (
                    "Empower session has expired or is not initialized. "
                    "Please run 'empower --login' in your local terminal to re-authenticate with 2FA."
                ),
            }
        except EmpowerError as e:
            return {"status": "error", "error_code": "API_ERROR", "message": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in get_net_worth_summary")
            return {"status": "error", "error_code": "INTERNAL_ERROR", "message": str(e)}

    @server.tool()
    def get_balances(
        account_types: Optional[List[str]] = None,
        include_inactive: bool = False,
    ) -> Dict[str, Any]:
        """Get account balances grouped by type (CASH, INVESTMENT, CREDIT, MORTGAGE, LOAN).

        Account numbers are masked by default to protect personal identifiers.

        Args:
            account_types: Optional list of account types to filter by (e.g. ['CASH', 'INVESTMENT']).
            include_inactive: Whether to include closed or zero-balance inactive accounts (default: False).

        Returns:
            Dict containing net worth, totals by type, and sanitized accounts list.
        """
        try:
            balances = _fetch_balances_cached()

            acct_filter: set[str] = set()
            if account_types:
                for t in account_types:
                    t_up = t.strip().upper()
                    if t_up in ("CASH", "BANK", "CHECKING", "SAVINGS"):
                        acct_filter.update(["BANK", "CASH", "CHECKING", "SAVINGS"])
                    elif t_up in ("CREDIT", "CREDIT_CARD", "CREDITCARD"):
                        acct_filter.update(["CREDIT", "CREDIT_CARD"])
                    elif t_up in ("INVESTMENT", "INVESTMENTS", "BROKERAGE", "IRA", "401K"):
                        acct_filter.update(["INVESTMENT", "INVESTMENTS"])
                    elif t_up in ("MORTGAGE", "MORTGAGES"):
                        acct_filter.update(["MORTGAGE", "MORTGAGES"])
                    elif t_up in ("LOAN", "LOANS"):
                        acct_filter.update(["LOAN", "LOANS"])
                    else:
                        acct_filter.add(t_up)

            filtered: List[Dict[str, Any]] = []
            for a in balances.accounts:
                acct_type = str(a.get("account_type", "")).upper()
                if acct_filter and acct_type not in acct_filter:
                    continue
                if not include_inactive and a.get("is_closed"):
                    continue
                filtered.append(_sanitize_account_dict(a))

            return {
                "status": "success",
                "net_worth": balances.net_worth,
                "totals_by_type": {
                    "cash": balances.total_cash,
                    "investment": balances.total_investment,
                    "credit": balances.total_credit_card,
                    "credit_card": balances.total_credit_card,
                    "mortgage": balances.total_mortgage,
                    "loan": balances.total_loan,
                    "other_assets": balances.total_other_assets,
                    "other_liabilities": balances.total_other_liabilities,
                },
                "accounts_count": len(filtered),
                "accounts": filtered,
            }
        except (SessionExpiredError, RequireTwoFactorException, FileNotFoundError):
            return {
                "status": "error",
                "error_code": "AUTH_REQUIRED",
                "message": (
                    "Empower session has expired or is not initialized. "
                    "Please run 'empower --login' in your local terminal to re-authenticate with 2FA."
                ),
            }
        except EmpowerError as e:
            return {"status": "error", "error_code": "API_ERROR", "message": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in get_balances")
            return {"status": "error", "error_code": "INTERNAL_ERROR", "message": str(e)}

    @server.tool()
    def get_holdings(
        ticker: Optional[str] = None,
        account_id: Optional[int] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Get investment portfolio positions, quantities, market prices, cost basis, and percentage allocations.

        Args:
            ticker: Optional ticker symbol to filter by (e.g. 'VTI', 'AAPL').
            account_id: Optional account ID to inspect a specific investment account.
            limit: Maximum number of holdings to return (1-200, default: 50).

        Returns:
            Dict containing total portfolio value, matching positions count, and holdings list.
        """
        try:
            clamped_limit = min(max(1, limit), 200)
            holdings = _fetch_holdings_cached()

            filtered: List[Dict[str, Any]] = []
            ticker_upper = ticker.strip().upper() if ticker else None

            for pos in holdings.holdings:
                if ticker_upper:
                    pos_ticker = str(pos.get("ticker") or "").upper()
                    if pos_ticker != ticker_upper:
                        continue
                if account_id is not None and pos.get("account_id") != account_id:
                    continue
                filtered.append(pos)

            return {
                "status": "success",
                "total_portfolio_value": holdings.total_value,
                "total_positions_available": len(holdings.holdings),
                "matching_count": len(filtered),
                "returned_count": len(filtered[:clamped_limit]),
                "holdings": filtered[:clamped_limit],
            }
        except (SessionExpiredError, RequireTwoFactorException, FileNotFoundError):
            return {
                "status": "error",
                "error_code": "AUTH_REQUIRED",
                "message": (
                    "Empower session has expired or is not initialized. "
                    "Please run 'empower --login' in your local terminal to re-authenticate with 2FA."
                ),
            }
        except EmpowerError as e:
            return {"status": "error", "error_code": "API_ERROR", "message": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in get_holdings")
            return {"status": "error", "error_code": "INTERNAL_ERROR", "message": str(e)}

    @server.tool()
    def get_transactions(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        account_id: Optional[int] = None,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Get historical transactions across all linked accounts with date and category filters.

        Args:
            start_date: Earliest transaction date (YYYY-MM-DD).
            end_date: Latest transaction date (YYYY-MM-DD).
            account_id: Optional account ID filter.
            category: Optional category name filter (e.g. 'Groceries', 'Utilities').
            limit: Maximum number of transactions to return (1-200, default: 50).

        Returns:
            Dict containing total transaction count, net cashflow, and transactions list.
        """
        try:
            clamped_limit = min(max(1, limit), 200)
            tx_data = _fetch_transactions_cached(
                start_date=start_date,
                end_date=end_date,
                limit=clamped_limit,
            )

            filtered: List[Dict[str, Any]] = []
            category_lower = category.strip().lower() if category else None

            for tx in tx_data.transactions:
                if account_id is not None and tx.get("account_id") != account_id:
                    continue
                if category_lower:
                    tx_cat = str(tx.get("category") or "").lower()
                    if category_lower not in tx_cat:
                        continue
                filtered.append(tx)

            return {
                "status": "success",
                "total_transactions": tx_data.total_transactions,
                "net_cashflow": tx_data.net_cashflow,
                "matching_count": len(filtered),
                "returned_count": len(filtered[:clamped_limit]),
                "transactions": filtered[:clamped_limit],
            }
        except (SessionExpiredError, RequireTwoFactorException, FileNotFoundError):
            return {
                "status": "error",
                "error_code": "AUTH_REQUIRED",
                "message": (
                    "Empower session has expired or is not initialized. "
                    "Please run 'empower --login' in your local terminal to re-authenticate with 2FA."
                ),
            }
        except EmpowerError as e:
            return {"status": "error", "error_code": "API_ERROR", "message": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in get_transactions")
            return {"status": "error", "error_code": "INTERNAL_ERROR", "message": str(e)}

    @server.tool()
    def check_auth_status() -> Dict[str, Any]:
        """Check whether a valid local session exists for Empower Personal Dashboard.

        Returns:
            Dict containing authenticated boolean status, session path, and guidance.
        """
        _ensure_session_fresh()
        session_file = empower_client.session_file
        exists = session_file.exists()
        if not exists and not empower_client.mock_mode:
            return {
                "status": "unauthenticated",
                "authenticated": False,
                "session_path": str(session_file),
                "message": "No session file found. Run 'empower --login' in terminal to authenticate.",
            }

        try:
            # Quick validation check: try to fetch balances
            balances = _fetch_balances_cached()
            return {
                "status": "authenticated",
                "authenticated": True,
                "session_path": str(session_file),
                "net_worth": balances.net_worth,
                "accounts_count": len(balances.accounts),
                "message": "Session is active and valid.",
            }
        except (SessionExpiredError, RequireTwoFactorException):
            return {
                "status": "expired",
                "authenticated": False,
                "session_path": str(session_file),
                "message": "Session token has expired. Run 'empower --login' in terminal to re-authenticate.",
            }
        except Exception as e:
            return {
                "status": "error",
                "authenticated": False,
                "session_path": str(session_file),
                "message": f"Session verification error: {e}",
            }

    # -------------------------------------------------------------------------
    # Resources
    # -------------------------------------------------------------------------

    @server.resource("empower://balances/summary")
    def get_balances_resource() -> str:
        """Resource exposing real-time net worth and asset class totals as JSON."""
        res = get_net_worth_summary()
        return json.dumps(res, indent=2)

    @server.resource("empower://holdings/portfolio")
    def get_holdings_resource() -> str:
        """Resource exposing investment portfolio positions as JSON."""
        res = get_holdings(limit=100)
        return json.dumps(res, indent=2)

    @server.resource("empower://accounts/list")
    def get_accounts_resource() -> str:
        """Resource exposing linked financial accounts list as JSON."""
        res = get_balances(include_inactive=False)
        return json.dumps(res, indent=2)

    # -------------------------------------------------------------------------
    # Prompts
    # -------------------------------------------------------------------------

    @server.prompt()
    def portfolio_review(risk_profile: str = "moderate") -> str:
        """Prompt template for auditing portfolio asset allocation and cash drag."""
        return (
            f"Please conduct an in-depth portfolio review with a {risk_profile} risk tolerance. "
            "Follow these steps:\n"
            "1. Call `get_net_worth_summary` to understand total net worth and asset breakdown.\n"
            "2. Call `get_holdings` to inspect security positions and allocations.\n"
            "3. Analyze cash drag, equity vs fixed-income balance, and single-stock concentration.\n"
            "4. Provide a structured summary with actionable recommendations."
        )

    @server.prompt()
    def spending_audit(days: int = 90) -> str:
        """Prompt template for analyzing recent transactions, recurring expenses, and burn rate."""
        return (
            f"Please perform a cashflow and spending audit over the past {days} days. "
            "Follow these steps:\n"
            "1. Call `get_transactions` for the specified timeframe.\n"
            "2. Group expenses by category and identify the highest expenditure buckets.\n"
            "3. Identify recurring subscriptions and check for unexpected spikes or fee charges.\n"
            "4. Summarize net cashflow (income vs expenses) and highlight potential budget savings."
        )

    return server


def main() -> None:
    """CLI entrypoint for running the empower-mcp server."""
    parser = argparse.ArgumentParser(
        description="Empower Personal Dashboard Model Context Protocol (MCP) Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run in standard stdio mode (Claude Desktop, Antigravity CLI, Cursor)
  empower-mcp

  # Run in SSE HTTP mode for networked or containerized deployment
  empower-mcp --transport sse --port 8000 --host 0.0.0.0

  # Run in offline sandbox mode (synthetic mock data, no network required)
  empower-mcp --sandbox
        """,
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport mode (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on when using SSE transport (default: 8000)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address to bind when using SSE transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--session-path",
        default=None,
        help="Path to saved session JSON file (default: ~/.empower_personal_dashboard_session.json)",
    )
    parser.add_argument(
        "--sandbox",
        action="store_true",
        help="Run in offline sandbox mode using synthetic financial mock data",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging to stderr",
    )

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="[%(asctime)s] [%(levelname)s] %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    if FastMCP is None:
        sys.stderr.write(
            "Error: The 'mcp' package is required to run the MCP server.\n"
            "Please install it with:\n"
            "    pip install 'empower-personal-dashboard[mcp]'\n"
        )
        sys.exit(1)

    # Initialize client
    client = EmpowerDashboardClient(
        session_file=args.session_path,
        mock_mode=args.sandbox,
        debug=args.debug,
    )

    server = create_mcp_server(client=client)

    if args.transport == "stdio":
        server.run(transport="stdio")
    elif args.transport == "sse":
        server.run(transport="sse", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
