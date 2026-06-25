"""Stable error semantics for the draft flow."""

from __future__ import annotations

from enum import Enum


class DraftErrorCode(str, Enum):
    DRAFT_NOT_FOUND = "draft_not_found"
    NOT_AWAITING = "not_awaiting"  # already committed/cancelled/expired
    WRONG_USER = "wrong_user"
    TOKEN_INVALID = "token_invalid"
    TOKEN_EXPIRED = "token_expired"
    NO_OPERATIONS = "no_operations"
    SECTION_NOT_FOUND = "section_not_found"
    TEMPLATE_NOT_FOUND = "template_not_found"


class DraftError(Exception):
    def __init__(self, code: DraftErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict:
        return {"error": self.code.value, "message": self.message}
