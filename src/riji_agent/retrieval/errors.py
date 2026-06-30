"""Stable error semantics for the retrieval tools.

Errors are returned to the model/Hermes as a code plus a safe message; they
never leak file paths or note contents.
"""

from __future__ import annotations

from enum import Enum


class RetrievalErrorCode(str, Enum):
    INVALID_QUERY = "invalid_query"
    NOT_FOUND = "not_found"
    PRIVATE_BLOCKED = "private_blocked"
    NO_EVIDENCE = "no_evidence"


class RetrievalError(Exception):
    """A tool-level error carrying a stable code and a user-safe message."""

    def __init__(self, code: RetrievalErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict:
        return {"error": self.code.value, "message": self.message}
