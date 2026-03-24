from __future__ import annotations


class FeishuBotMCPError(Exception):
    """Base exception for the project."""


class FeishuAuthError(FeishuBotMCPError):
    """Raised when tenant token retrieval fails."""


class FeishuAPIError(FeishuBotMCPError):
    """Raised when a Feishu API call returns a non-zero code or bad response."""

    def __init__(self, message: str, *, code: int | None = None, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class MessageValidationError(FeishuBotMCPError):
    """Raised when message input is invalid."""
