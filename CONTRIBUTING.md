# Contributing to empower-personal-dashboard

Thank you for your interest in contributing to **empower-personal-dashboard**!

This project provides an open-source, reliable Python library and CLI for the Empower Personal Dashboard API (formerly Personal Capital). Contributions that improve API compatibility, add new endpoints, optimize extraction reliability, or improve documentation are very welcome.

---

## Ground Rules

1. **Strict Zero-PII & Secret Protection:**
   - **Never commit real credentials, session tokens, or personal financial data.**
   - All issues, pull requests, test fixtures, and sample outputs MUST use synthetic data (e.g., `Checking Account - 1234`, `Acme Corp`, toy balances).
   - Session files (`~/.empower_personal_dashboard_session.json`), `.env` files, and debug logs are git-ignored and must never be committed.
2. **Minimal Dependencies:**
   - Keep the core library runtime dependencies strictly limited to standard library modules and `requests`. Avoid introducing heavy frameworks.
3. **Multi-Version Compatibility:**
   - Code must remain compatible across all supported Python versions: **Python 3.9, 3.10, 3.11, 3.12, and 3.13**.

---

## Getting Started

1. **Fork the repository** on GitHub and clone your fork locally:
   ```bash
   git clone https://github.com/<your-username>/empower-personal-dashboard.git
   cd empower-personal-dashboard
   ```

2. **Create a feature branch** based on `main`:
   ```bash
   git checkout -b feat/my-new-feature main
   ```

3. **Set up a virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -e ".[dev]"
   ```

4. **Verify the existing test suite**:
   ```bash
   PYTHONPATH=. python3 -m unittest discover tests
   ```

---

## Development & Testing Workflow

We follow **Test-Driven Development (TDD)**:

1. **Write or Update Tests First:**
   - Unit tests live in the `tests/` directory (`test_client.py`, `test_models.py`, `test_sanitizers.py`, `test_cli.py`).
   - Mock all network requests using `unittest.mock` or test against the built-in `--sandbox` engine. Never make real HTTP calls in unit tests.
2. **Implement Your Changes:**
   - Write clean, type-annotated Python code following PEP 8.
   - Use `clean_api_text()` in `empower_personal_dashboard/sanitizers.py` for any incoming text strings to strip Unicode replacement characters (`\ufffd`).
3. **Run Local Checks:**
   ```bash
   # 1. Run full unit test suite
   PYTHONPATH=. python3 -m unittest discover tests

   # 2. Verify compilation and syntax across all files
   python3 -m compileall empower_personal_dashboard tests

   # 3. Test offline CLI sandbox execution
   empower --sandbox --all --format table
   ```

---

## Reporting API Changes or Quirks

Financial institution APIs evolve over time. If you notice changed endpoints, new challenge types, or edge-case response formats:

1. **Check for existing issues** before filing a new one.
2. **Sanitize all data:** Before sharing JSON payloads or logs, ensure that account numbers, balances, employer names, names, addresses, and authorization cookies/tokens are replaced with `***REDACTED***` or synthetic placeholders.
3. **Include helpful details:**
   - Endpoint URL (e.g. `/invest/getHoldings` vs `/newaccount/getAccounts`)
   - HTTP status code and `spHeader` status
   - Python version and OS platform

---

## Pull Request Guidelines

1. **Keep PRs Focused:** Submit small, targeted PRs for a single bug fix or feature.
2. **Use Conventional Commits:**
   - `feat: add support for net worth history endpoint`
   - `fix: handle null ticker symbols in holdings response`
   - `docs: update CLI quickstart examples in README`
   - `test: add unit tests for credit account balance parsing`
3. **CI Passing:** Every PR must pass all GitHub Actions matrix checks (Python 3.9 through 3.13) before review and merge.
4. **Squash Merging:** Pull requests are squashed and merged into `main` with a clean, descriptive summary.

---

## License

By contributing to `empower-personal-dashboard`, you agree that your contributions will be licensed under the project's [MIT License](LICENSE).
