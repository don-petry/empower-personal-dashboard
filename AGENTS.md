# AGENTS.md — Agent Guidelines & Development Standards

This file defines project-specific development standards for **empower-personal-dashboard**.
It follows the [AGENTS.md convention](https://agents.md/) and extends the organization-wide engineering standards.

> **Organization standards:** This repository inherits shared development and security standards from
> [`petry-projects/.github/AGENTS.md`](https://github.com/petry-projects/.github/blob/main/AGENTS.md).
> Read that file for cross-cutting standards on Test-Driven Development (TDD), git workflows, commit conventions, and push protection.

---

## 1. Project Overview & Architecture

`empower-personal-dashboard` is an open-source, standalone Python client and CLI for **Empower Personal Dashboard** (formerly Personal Capital). It allows users and unattended automated systems to aggregate linked financial institution data—including bank balances, investment holdings, net worth, and historical transactions.

### Key Architecture Components

```
empower-personal-dashboard/
├── empower_personal_dashboard/
│   ├── __init__.py           # Public exports (Client, Models, Exceptions)
│   ├── client.py             # Session management, 2FA bootstrap, API communication
│   ├── models.py             # Strongly typed dataclasses for financial payloads
│   ├── sanitizers.py         # Unicode text sanitization (\ufffd stripping, normalization)
│   ├── exceptions.py         # Domain error hierarchy (EmpowerError, 2FA, SessionExpired)
│   ├── cli.py                # Command-line interface with formatted tables, JSON, CSV
│   └── mcp_server.py         # Model Context Protocol (FastMCP) server with tools & resources
├── tests/
│   ├── test_client.py        # Offline client unit tests (mocked endpoints)
│   ├── test_models.py        # Dataclass parsing and aggregation logic
│   ├── test_sanitizers.py    # Unicode replacement and whitespace cleaning
│   ├── test_cli.py           # CLI invocation, formatting, and sandbox tests
│   └── test_mcp_server.py    # FastMCP tools, error handling, and caching tests
├── pyproject.toml            # PEP 621 build configuration with SPDX MIT license
└── requirements.txt          # Minimal runtime dependencies (requests>=2.28.0)
```

### Core Design Principles

1. **Lightweight & Transport-Clean:** The core library depends only on `requests`. No heavy frameworks or unnecessary third-party dependencies.
2. **Two-Phase Authentication Lifecycle:**
   - **Phase 1 (Interactive Bootstrap):** One-time 2FA challenge via SMS or Email with device binding (`bindDevice=true`).
   - **Phase 2 (Unattended Operation):** Session cookies and CSRF tokens are securely persisted to `~/.empower_personal_dashboard_session.json` with POSIX `0600` permissions for headless cron or agent execution.
3. **Transparent Migration Routing:** Seamlessly detects and routes API traffic from deprecated legacy endpoints (`home.personalcapital.com`) to the unified platform (`pc-api.empower-retirement.com`) on Code 920.
4. **Resilient HTTP Transport:** Always passes `"Accept-Encoding": "identity"` to prevent gzip corruption across Cloudflare edge networks on large multi-megabyte transaction histories.

---

## 2. Zero-PII & Privacy Strict Mandate

This is a **public open-source repository**.

- **NEVER commit personal or sensitive information.** Full names, real bank account numbers, credentials, real portfolio balances, transaction memos, employer names, or street addresses must **never** enter this repository.
- **100% Synthetic Test Data:** All unit test fixtures, mock data, CLI sandbox mocks, and documentation examples MUST use generic synthetic placeholders (e.g., `Checking Account - 1234`, `Acme Corp`, `VTI`, toy balances like `$12,500.00`).
- **Session Protection:** Session tokens and authentication dumps must never be staged or committed. The repository `.gitignore` strictly ignores `.empower*.json`, `*session*.json`, and `*.log`.
- **Secret Redaction:** Passwords, PINs, and 2FA SMS/Email verification codes must always be masked (`***REDACTED***`) in any debug output or logging.

---

## 3. Test-Driven Development (TDD) Standards

This repository adheres to strict Test-Driven Development as mandated by the organization standards.

- **Mandatory Test First:** Write or update unit tests before implementing new features, endpoints, or bug fixes.
- **Fast & Hermetic:** Tests MUST execute in under 1 second without making real network requests. Use `unittest.mock` to mock HTTP responses or leverage the built-in synthetic mock engine.
- **Zero `.skip()` Policy:** Tests must pass unconditionally. Never use `@unittest.skip` to bypass failing assertions.
- **Zero Coverage-Ignore:** Do not add coverage bypass annotations to artificially boost metrics.
- **Verification Commands:**
  ```bash
  # Run all unit tests
  PYTHONPATH=. python3 -m unittest discover tests

  # Byte-compile syntax and import verification
  python3 -m compileall empower_personal_dashboard tests
  ```

---

## 4. Code Quality & Style Standards

- **Python Version Support:** Tested and verified across Python 3.9, 3.10, 3.11, 3.12, and 3.13.
- **Typing & Dataclasses:** All public APIs and parsed API responses must be strongly typed using `dataclasses` and Python type hints (`typing`).
- **Error Handling:** Never let unhandled API exceptions or raw HTTP errors bubble up directly to callers. Wrap network failures and API errors in `EmpowerError` or its subclasses (`SessionExpiredError`, `RequireTwoFactorException`, `LoginFailedException`).
- **Encoding & Text:** Always sanitize upstream text using `clean_api_text()` in `empower_personal_dashboard/sanitizers.py` to prevent mojibake and `\ufffd` replacement characters from corrupting downstream datasets or CSVs.

---

## 5. Git Workflow & CI Gates

- **Base Branch:** Always branch off `main` and keep working branches up to date.
- **Conventional Commits:** Use standard commit message prefixes:
  - `feat:` New feature or CLI capability
  - `fix:` Bug fix or API compatibility patch
  - `docs:` Documentation updates in README, AGENTS, or CONTRIBUTING
  - `test:` Adding or refining unit tests
  - `chore:` Dependency or packaging updates
- **Pull Requests & Merge Policy:**
  - Automated CI runs the test suite and compile checks across Python 3.9–3.13.
  - Pull requests are merged using **Squash and Merge** to maintain a clean history on `main`.
