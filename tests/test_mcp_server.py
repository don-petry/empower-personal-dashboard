"""
test_mcp_server.py — Unit tests for Empower Personal Dashboard FastMCP server.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from empower_personal_dashboard.client import EmpowerDashboardClient
from empower_personal_dashboard.exceptions import EmpowerError, SessionExpiredError
from empower_personal_dashboard.mcp_server import (
    FastMCP,
    _DataCache,
    _mask_account_identifier,
    create_mcp_server,
)


class TestMCPServer(unittest.TestCase):
    def setUp(self) -> None:
        if FastMCP is not None:
            self.client = EmpowerDashboardClient(mock_mode=True)
            self.server = create_mcp_server(client=self.client)
        else:
            self.client = None
            self.server = None

    def _require_server(self) -> bool:
        if self.server is None:
            with self.assertRaises(ImportError):
                create_mcp_server()
            return False
        return True

    def test_mask_account_identifier(self) -> None:
        self.assertEqual(_mask_account_identifier(""), "")
        self.assertEqual(_mask_account_identifier(None), "")
        self.assertEqual(_mask_account_identifier("1234"), "1234")
        self.assertEqual(_mask_account_identifier("9876543210"), "****3210")
        self.assertEqual(_mask_account_identifier("123"), "123")

    def test_create_mcp_server_registration(self) -> None:
        if not self._require_server():
            return

        async def run_check() -> None:
            tools = await self.server.list_tools()
            tool_names = [t.name for t in tools]
            self.assertIn("get_net_worth_summary", tool_names)
            self.assertIn("get_balances", tool_names)
            self.assertIn("get_holdings", tool_names)
            self.assertIn("get_transactions", tool_names)
            self.assertIn("check_auth_status", tool_names)

            resources = await self.server.list_resources()
            resource_uris = [str(r.uri) for r in resources]
            self.assertIn("empower://balances/summary", resource_uris)
            self.assertIn("empower://holdings/portfolio", resource_uris)
            self.assertIn("empower://accounts/list", resource_uris)

            prompts = await self.server.list_prompts()
            prompt_names = [p.name for p in prompts]
            self.assertIn("portfolio_review", prompt_names)
            self.assertIn("spending_audit", prompt_names)

        asyncio.run(run_check())

    def test_tool_get_net_worth_summary(self) -> None:
        if not self._require_server():
            return

        async def run_check() -> None:
            res = await self.server.call_tool("get_net_worth_summary", {})
            self.assertFalse(res.is_error)
            payload = json.loads(res.content[0].text)
            self.assertEqual(payload["status"], "success")
            self.assertGreater(payload["net_worth"], 0)
            self.assertGreater(payload["total_cash"], 0)
            self.assertGreater(payload["total_investment"], 0)
            self.assertEqual(payload["accounts_count"], 5)

        asyncio.run(run_check())

    def test_tool_get_balances_with_filtering(self) -> None:
        if not self._require_server():
            return

        async def run_check() -> None:
            # All accounts
            res_all = await self.server.call_tool("get_balances", {})
            payload_all = json.loads(res_all.content[0].text)
            self.assertEqual(payload_all["status"], "success")
            self.assertEqual(payload_all["accounts_count"], 5)

            # Cash filter
            res_cash = await self.server.call_tool("get_balances", {"account_types": ["CASH"]})
            payload_cash = json.loads(res_cash.content[0].text)
            self.assertEqual(payload_cash["status"], "success")
            self.assertEqual(payload_cash["accounts_count"], 2)
            for a in payload_cash["accounts"]:
                self.assertEqual(a["account_type"], "BANK")

            # Investment filter
            res_inv = await self.server.call_tool("get_balances", {"account_types": ["INVESTMENT"]})
            payload_inv = json.loads(res_inv.content[0].text)
            self.assertEqual(payload_inv["status"], "success")
            self.assertEqual(payload_inv["accounts_count"], 2)

        asyncio.run(run_check())

    def test_tool_get_holdings_with_filter_and_limit(self) -> None:
        if not self._require_server():
            return

        async def run_check() -> None:
            # Filter by ticker
            res_vti = await self.server.call_tool("get_holdings", {"ticker": "VTI"})
            payload_vti = json.loads(res_vti.content[0].text)
            self.assertEqual(payload_vti["status"], "success")
            self.assertEqual(payload_vti["matching_count"], 1)
            self.assertEqual(payload_vti["holdings"][0]["ticker"], "VTI")

            # Clamped limit
            res_limit = await self.server.call_tool("get_holdings", {"limit": 1})
            payload_limit = json.loads(res_limit.content[0].text)
            self.assertEqual(payload_limit["returned_count"], 1)

        asyncio.run(run_check())

    def test_tool_get_transactions_with_filter(self) -> None:
        if not self._require_server():
            return

        async def run_check() -> None:
            res = await self.server.call_tool(
                "get_transactions",
                {"start_date": "2026-09-01", "end_date": "2026-09-30", "limit": 10},
            )
            payload = json.loads(res.content[0].text)
            self.assertEqual(payload["status"], "success")
            self.assertGreaterEqual(payload["total_transactions"], 1)
            self.assertGreaterEqual(payload["returned_count"], 1)

            # Category filter
            res_cat = await self.server.call_tool("get_transactions", {"category": "payroll"})
            payload_cat = json.loads(res_cat.content[0].text)
            self.assertEqual(payload_cat["status"], "success")

        asyncio.run(run_check())

    def test_tool_check_auth_status(self) -> None:
        if not self._require_server():
            return

        async def run_check() -> None:
            res = await self.server.call_tool("check_auth_status", {})
            payload = json.loads(res.content[0].text)
            self.assertTrue(payload["authenticated"])
            self.assertEqual(payload["status"], "authenticated")

        asyncio.run(run_check())

    def test_auth_required_error_handling(self) -> None:
        if not self._require_server():
            return

        async def run_check() -> None:
            mock_client = MagicMock()
            mock_client.fetch_balances.side_effect = SessionExpiredError("Session token expired")
            failing_server = create_mcp_server(client=mock_client)

            res = await failing_server.call_tool("get_net_worth_summary", {})
            payload = json.loads(res.content[0].text)
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["error_code"], "AUTH_REQUIRED")
            self.assertIn("empower --login", payload["message"])

        asyncio.run(run_check())

    def test_api_error_handling(self) -> None:
        if not self._require_server():
            return

        async def run_check() -> None:
            mock_client = MagicMock()
            mock_client.fetch_balances.side_effect = EmpowerError("Upstream timeout")
            failing_server = create_mcp_server(client=mock_client)

            res = await failing_server.call_tool("get_net_worth_summary", {})
            payload = json.loads(res.content[0].text)
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["error_code"], "API_ERROR")
            self.assertIn("Upstream timeout", payload["message"])

        asyncio.run(run_check())

    def test_in_memory_ttl_caching(self) -> None:
        if not self._require_server():
            return

        async def run_check() -> None:
            mock_client = MagicMock()
            mock_client.fetch_balances.return_value = self.client.fetch_balances()
            cached_server = create_mcp_server(client=mock_client, cache_ttl_seconds=60)

            # First call triggers fetch_balances
            await cached_server.call_tool("get_net_worth_summary", {})
            self.assertEqual(mock_client.fetch_balances.call_count, 1)

            # Second call within TTL hits cache
            await cached_server.call_tool("get_net_worth_summary", {})
            self.assertEqual(mock_client.fetch_balances.call_count, 1)

            # Different tool sharing balance cache hits cache
            await cached_server.call_tool("get_balances", {})
            self.assertEqual(mock_client.fetch_balances.call_count, 1)

        asyncio.run(run_check())

    def test_resources_and_prompts(self) -> None:
        if not self._require_server():
            return

        async def run_check() -> None:
            # Read resource
            res_content = await self.server.read_resource("empower://balances/summary")
            self.assertTrue(len(res_content) > 0)

            # Get prompt
            prompt_res = await self.server.get_prompt("portfolio_review", {"risk_profile": "aggressive"})
            self.assertIn("portfolio review", prompt_res.messages[0].content.text)
            self.assertIn("aggressive", prompt_res.messages[0].content.text)

        asyncio.run(run_check())

    def test_import_error_when_mcp_missing(self) -> None:
        with patch("empower_personal_dashboard.mcp_server.FastMCP", None):
            with self.assertRaises(ImportError) as ctx:
                create_mcp_server()
            self.assertIn("pip install 'empower-personal-dashboard[mcp]'", str(ctx.exception))



if __name__ == "__main__":
    unittest.main()
