"""Bounded recall of the current user's own statements in the current session."""

from __future__ import annotations

from typing import Any

from riji_agent.memory.capture import contains_credentials
from riji_agent.memory.store import MemoryStore
from riji_agent.retrieval.models import ToolContext

SESSION_SEARCH_DEF: dict[str, Any] = {
    "name": "session_search",
    "description": (
        "Recall the user's earlier statements in this mentor's current chat, including "
        "messages outside the recent context. Use a short literal keyword in the original "
        "language (Chinese supported). Returns dated user statements, not journal facts "
        "or assistant claims. Cite conversation/<id>; never imply the journal was saved."
    ),
    "parameters": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 200},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    },
}


def search_conversation(
    store: MemoryStore, context: ToolContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    prefix = f"{context.feishu_user_id}:{context.persona_id}:"
    if not context.session_id.startswith(prefix) or context.session_id == prefix:
        raise ValueError("invalid session identity")
    if set(arguments) - {"query", "top_k"}:
        raise ValueError("unsupported session search arguments")
    query, limit = arguments.get("query"), arguments.get("top_k", 5)
    if not isinstance(query, str) or not query.strip() or len(query) > 200:
        raise ValueError("query must contain 1 to 200 characters")
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("top_k must be between 1 and 10")
    records = store.search_user_messages(context.session_id, query, limit=10)
    items: list[dict[str, Any]] = []
    remaining = 1500
    truncated = False
    for message in records:
        if contains_credentials(message.content):
            continue
        if len(items) >= limit or remaining <= 0:
            truncated = True
            break
        position = message.content.lower().find(query.strip().lower())
        start = max(0, position - 100)
        snippet = message.content[start:start + min(400, remaining)]
        remaining -= len(snippet)
        cut = start > 0 or start + len(snippet) < len(message.content)
        truncated = truncated or cut
        items.append({
            "source_id": f"conversation/{message.id}", "role": "user",
            "created_at": message.created_at, "snippet": snippet, "truncated": cut,
        })
    return {"corpus": "user_conversation", "items": items, "truncated": truncated}
