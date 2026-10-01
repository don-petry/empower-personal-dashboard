# CLAUDE.md — empower-personal-dashboard

This file provides project-specific instructions for Claude Code when working in this repository.

## Development Standards

Read [AGENTS.md](./AGENTS.md) before making any changes. It defines the project-specific and organization-wide engineering standards, including:
- **Zero-PII & Privacy Strict Mandate**: Never introduce real credentials, names, accounts, or financial figures. Use synthetic mock data only.
- **TDD Mandate**: Write tests first; keep unit tests hermetic, fast (<1s), and deterministic.
- **Code Quality**: Maintain compatibility across Python 3.9 through 3.13.

## Quick Commands

```bash
# Run all unit tests
PYTHONPATH=. python3 -m unittest discover tests

# Run OpenAPI contract tests specifically (requires pip install -e ".[test]")
PYTHONPATH=. python3 -m unittest tests/test_openapi_contract.py

# Validate OpenAPI 3.1 specification
npx --yes @redocly/cli@2.57.0 lint docs/openapi.yaml

# Syntax and byte-compilation check
python3 -m compileall empower_personal_dashboard tests

# Run CLI in offline sandbox mode
python3 -m empower_personal_dashboard.cli --sandbox --all --format table

# Run MCP server in offline sandbox mode
python3 -m empower_personal_dashboard.mcp_server --sandbox
```

## Repository Architecture

- `empower_personal_dashboard/client.py`: Core client managing sessions, 2FA bootstrap, Code 920 routing, cookie jar persistence.
- `empower_personal_dashboard/mcp_server.py`: Model Context Protocol (MCP) server with FastMCP tools, resources, and prompts.
- `empower_personal_dashboard/models.py`: Strongly typed dataclasses (`DashboardBalances`, `DashboardHoldings`, `DashboardTransactions`).
- `empower_personal_dashboard/sanitizers.py`: Text cleaning and `\ufffd` stripping.
- `empower_personal_dashboard/exceptions.py`: Domain exception hierarchy.
- `empower_personal_dashboard/beancount.py`: Pure-Python Beancount Plain-Text Accounting (PTA) export engine.
- `empower_personal_dashboard/cli.py`: Interactive CLI with formatted tables, markdown, JSON, CSV, and Beancount exports.
- `docs/openapi.yaml`: Canonical OpenAPI 3.1 specification for upstream RPC wire protocol and canonical domain schemas.
- `docs/adr/`: Architectural Decision Records (ADR-0001 FastMCP, ADR-0002 OpenAPI 3.1, ADR-0003 Beancount Export).
- `tests/`: Offline test suite verifying models, sanitizers, client flows, CLI options, FastMCP server, Beancount exports, and OpenAPI contract tests.
