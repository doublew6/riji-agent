"""Fixed diagnostic codes; classification never serializes provider error bodies."""

from riji_agent.models.types import LLMError

MODEL_ERROR_CATEGORIES = {
    "model_timeout": "timeout",
    "model_queue_timeout": "timeout",
    "model_connection_failed": "connection",
    "model_transport_failed": "transport",
    "model_authentication_failed": "authentication",
    "model_permission_denied": "authorization",
    "model_rate_limited": "rate_limit",
    "model_quota_exhausted": "quota",
    "model_server_failed": "server",
    "model_request_rejected": "request",
    "model_refused": "refusal",
    "model_output_invalid": "model_output",
    "model_runtime_unavailable": "configuration",
    "model_request_failed": "model",
}

# A known failure category does not establish whether a remote call was processed.
UNCERTAIN_MODEL_OUTCOMES = frozenset({
    "model_timeout", "model_connection_failed", "model_transport_failed",
    "model_server_failed", "model_output_invalid", "model_request_failed",
})

_CODEX_CODES = {
    "codex_timeout": "model_timeout",
    "codex_queue_timeout": "model_queue_timeout",
    "codex_login_required": "model_authentication_failed",
    "codex_quota_exhausted": "model_quota_exhausted",
    "codex_invalid_response": "model_output_invalid",
    "codex_invalid_protocol": "model_output_invalid",
    "codex_response_too_large": "model_output_invalid",
    "codex_unavailable": "model_runtime_unavailable",
    "codex_unsupported_version": "model_runtime_unavailable",
}


def model_failure_code(error: LLMError) -> str:
    """Classify a current exception by exact allowlisted value, never by substrings."""
    value = str(error)
    if value in MODEL_ERROR_CATEGORIES:
        return value
    return _CODEX_CODES.get(value, "model_request_failed")


def http_failure_code(status: int) -> str:
    known = {401: "model_authentication_failed", 403: "model_permission_denied",
             408: "model_timeout", 429: "model_rate_limited"}
    if status in known:
        return known[status]
    return "model_server_failed" if 500 <= status <= 599 else "model_request_rejected"
