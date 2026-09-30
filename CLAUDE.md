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

# Syntax and byte-compilation check
python3 -m compileall empower_personal_dashboard tests

# Run CLI in offline sandbox mode
python3 -m empower_personal_dashboard.cli --sandbox --all --format table
```

## Repository Architecture

- `empower_personal_dashboard/client.py`: Core client managing sessions, 2FA bootstrap, Code 920 routing, cookie jar persistence.
- `empower_personal_dashboard/models.py`: Strongly typed dataclasses (`DashboardBalances`, `DashboardHoldings`, `DashboardTransactions`).
- `empower_personal_dashboard/sanitizers.py`: Text cleaning and `\ufffd` stripping.
- `empower_personal_dashboard/exceptions.py`: Domain exception hierarchy.
- `empower_personal_dashboard/cli.py`: Interactive CLI with formatted tables, markdown, JSON, CSV exports.
- `tests/`: Offline test suite verifying models, sanitizers, client flows, and CLI options.
