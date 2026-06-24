"""Read-only retrieval tools exposed to Hermes/DeepSeek over the local index."""

from riji_agent.retrieval.errors import RetrievalError, RetrievalErrorCode
from riji_agent.retrieval.models import (
    NoteResponse,
    PeriodItem,
    PeriodsResponse,
    RetrievalLimits,
    SearchResponse,
    SearchResultItem,
    ToolContext,
)
from riji_agent.retrieval.schemas import SCHEMA_VERSION, TOOL_DEFINITIONS
from riji_agent.retrieval.service import RetrievalService

__all__ = [
    "RetrievalError",
    "RetrievalErrorCode",
    "RetrievalService",
    "RetrievalLimits",
    "ToolContext",
    "SearchResponse",
    "SearchResultItem",
    "NoteResponse",
    "PeriodItem",
    "PeriodsResponse",
    "TOOL_DEFINITIONS",
    "SCHEMA_VERSION",
]
