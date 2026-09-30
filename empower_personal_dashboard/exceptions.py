"""
exceptions.py — Custom exceptions for Empower Personal Dashboard API.
"""

from typing import Any, Dict, List, Optional


class EmpowerError(Exception):
    """Base exception for all Empower Personal Dashboard API errors."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
        error_code: Optional[int] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_body = response_body
        self.error_code = error_code

    def __str__(self) -> str:
        if self.error_code:
            return f"[Error {self.error_code}] {self.message}"
        return self.message


class RequireTwoFactorException(EmpowerError):
    """Raised when 2FA verification (SMS or Email) is required to proceed."""

    def __init__(
        self,
        message: str = "Two-factor authentication required.",
        available_methods: Optional[List[Dict[str, Any]]] = None,
    ):
        super().__init__(message)
        self.available_methods = available_methods or []


class SessionExpiredError(EmpowerError):
    """Raised when saved session cookies or CSRF tokens have expired."""

    pass


class LoginFailedException(EmpowerError):
    """Raised when authentication credentials or 2FA verification fails."""

    pass
