"""Authentication / authorization errors for the gateway."""

from __future__ import annotations

from enum import Enum


class AuthErrorCode(str, Enum):
    UNAUTHENTICATED = "unauthenticated"  # bad or missing shared secret
    FORBIDDEN_USER = "forbidden_user"  # user not on the allowlist
    GROUP_CHAT_DENIED = "group_chat_denied"  # journal tools are private-chat only


class AuthError(Exception):
    def __init__(self, code: AuthErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
