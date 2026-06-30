"""Authentication and authorization at the Hermes boundary.

Two independent gates:
- the shared secret proves the caller is the local Hermes instance;
- the chat must be a private chat from an allowlisted Feishu user.
"""

from __future__ import annotations

import hmac
from typing import AbstractSet

from riji_agent.hermes.errors import AuthError, AuthErrorCode
from riji_agent.hermes.models import PRIVATE_CHAT_TYPE


def verify_shared_secret(provided: str, expected: str) -> None:
    if not provided or not hmac.compare_digest(provided, expected):
        raise AuthError(AuthErrorCode.UNAUTHENTICATED, "invalid shared secret")


def authorize_chat(feishu_user_id: str, chat_type: str, allowed_user_ids: AbstractSet[str]) -> None:
    if chat_type != PRIVATE_CHAT_TYPE:
        raise AuthError(AuthErrorCode.GROUP_CHAT_DENIED, "journal tools are only available in private chats")
    if feishu_user_id not in allowed_user_ids:
        raise AuthError(AuthErrorCode.FORBIDDEN_USER, "user is not allowed to use journal tools")
