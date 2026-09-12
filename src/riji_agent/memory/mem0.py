"""Synchronous REST adapter for the Mem0 Self-Hosted server."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

import httpx

from riji_agent.memory.backend import MemoryBackendError
from riji_agent.memory.models import (
    LongTermMemory,
    MemoryHistoryEntry,
    MemoryScope,
    MemoryStatus,
)


class Mem0Client:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 5.0,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key}
        # Memory text and credentials must go directly to the configured local API,
        # including on hosts where urllib falls back to an OS-level proxy.
        self._client = client or httpx.Client(timeout=timeout, trust_env=False)

    def health(self) -> bool:
        try:
            response = self._client.get(
                self._base_url + "/configure/providers", headers=self._headers
            )
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def configuration(self) -> Mapping[str, Any]:
        data = self._request("GET", "/configure")
        if not isinstance(data, dict):
            raise MemoryBackendError("mem0_invalid_response")
        return data

    def search(
        self,
        query: str,
        *,
        user_id: str,
        scope: MemoryScope,
        persona_id: Optional[str] = None,
        limit: int = 8,
    ) -> Sequence[LongTermMemory]:
        filters: Dict[str, Any] = {
            "user_id": user_id,
            "scope": scope.value,
            "status": MemoryStatus.ACTIVE.value,
        }
        if scope is MemoryScope.PERSONA:
            filters["agent_id"] = persona_id
        payload = {"query": query, "filters": filters, "top_k": limit}
        data = self._request("POST", "/search", json=payload)
        return self._normalize_results(data)

    def list_memories(
        self,
        *,
        user_id: str,
        persona_id: Optional[str] = None,
        include_archived: bool = False,
        limit: int = 1000,
    ) -> Sequence[LongTermMemory]:
        params: Dict[str, Any] = {"user_id": user_id, "top_k": limit}
        if persona_id:
            params["agent_id"] = persona_id
        data = self._request("GET", "/memories", params=params)
        rows = self._normalize_results(data)
        if include_archived:
            return rows
        return tuple(row for row in rows if row.status is MemoryStatus.ACTIVE)

    def get(self, memory_id: str) -> LongTermMemory:
        data = self._request("GET", f"/memories/{memory_id}")
        if data is None:
            raise MemoryBackendError("memory_not_found")
        return self._normalize_memory(data)

    def add(
        self,
        content: str,
        *,
        user_id: str,
        scope: MemoryScope,
        persona_id: Optional[str],
        metadata: Mapping[str, Any],
    ) -> Sequence[LongTermMemory]:
        payload: Dict[str, Any] = {
            "messages": [{"role": "user", "content": content}],
            "user_id": user_id,
            "metadata": dict(metadata),
            "infer": False,
        }
        if scope is MemoryScope.PERSONA:
            payload["agent_id"] = persona_id
        data = self._request("POST", "/memories", json=payload)
        return tuple(item if item.user_id else self.get(item.id) for item in self._normalize_results(data))

    def update(
        self,
        memory_id: str,
        *,
        content: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> LongTermMemory:
        payload: Dict[str, Any] = {}
        if content is not None:
            payload["text"] = content
        if metadata is not None:
            payload["metadata"] = dict(metadata)
        self._request("PUT", f"/memories/{memory_id}", json=payload)
        return self.get(memory_id)

    def find_operation(self, operation_id: str, *, user_id: str,
                       content: str) -> Optional[LongTermMemory]:
        results = self._normalize_results(self._request("GET", "/riji/memories/operation",
                                          params={"user_id": user_id, "operation_id": operation_id}))
        matches = [item for item in results if item.user_id == user_id
                   and item.metadata.get("journal_operation_id") == operation_id]
        if len(matches) > 1:
            raise MemoryBackendError("journal_operation_ambiguous")
        return matches[0] if matches else None

    def add_explicit(self, content: str, *, user_id: str,
                     metadata: Mapping[str, Any]) -> Sequence[LongTermMemory]:
        payload = {"content": content, "user_id": user_id, "metadata": dict(metadata),
                   "operation_id": metadata["journal_operation_id"],
                   "scope": metadata.get("scope", "shared"), "persona_id": metadata.get("persona_id")}
        return self._normalize_results(self._request("POST", "/riji/memories/explicit", json=payload))

    def export_memories(self, *, user_id: str) -> Sequence[LongTermMemory]:
        data = self._request("GET", "/riji/memories/export", params={"user_id": user_id, "limit": 100001})
        if not isinstance(data, dict) or data.get("complete") is not True:
            raise MemoryBackendError("incomplete_memory_export")
        return self._normalize_results(data)

    def purge(self, memory_id: str) -> None:
        self._request("DELETE", f"/riji/memories/{memory_id}/purge")

    def delete(self, memory_id: str) -> None:
        self._request("DELETE", f"/memories/{memory_id}")

    def history(self, memory_id: str) -> Sequence[MemoryHistoryEntry]:
        data = self._request("GET", f"/memories/{memory_id}/history")
        rows = data.get("results", data) if isinstance(data, dict) else data
        return tuple(self._normalize_history(row) for row in rows or [])

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(
                method, self._base_url + path, headers=self._headers, **kwargs
            )
            # Inspect the response contract directly: optional framework extras
            # may supply an httpx2 TestClient with different exception classes.
            if not 200 <= response.status_code < 300:
                raise MemoryBackendError(f"mem0_http_{response.status_code}")
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise MemoryBackendError(f"mem0_http_{exc.response.status_code}") from None
        except httpx.HTTPError:
            raise MemoryBackendError("mem0_unavailable") from None
        except ValueError:
            raise MemoryBackendError("mem0_invalid_response") from None

    def _normalize_results(self, data: Any) -> Sequence[LongTermMemory]:
        rows = data.get("results", data) if isinstance(data, dict) else data
        return tuple(self._normalize_memory(row) for row in rows or [])

    @staticmethod
    def _normalize_memory(row: Mapping[str, Any]) -> LongTermMemory:
        metadata = dict(row.get("metadata") or {})
        try:
            scope = MemoryScope(metadata.get("scope", MemoryScope.SHARED.value))
        except ValueError:
            raise MemoryBackendError("mem0_invalid_scope") from None
        try:
            status = MemoryStatus(metadata.get("status", MemoryStatus.ACTIVE.value))
        except ValueError:
            raise MemoryBackendError("mem0_invalid_status") from None
        return LongTermMemory(
            id=str(row.get("id", "")),
            content=str(row.get("memory") or row.get("data") or ""),
            user_id=str(row.get("user_id") or metadata.get("user_id") or ""),
            scope=scope,
            persona_id=row.get("agent_id") or metadata.get("persona_id"),
            status=status,
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            metadata=metadata,
            score=float(row["score"]) if row.get("score") is not None else None,
        )

    @staticmethod
    def _normalize_history(row: Mapping[str, Any]) -> MemoryHistoryEntry:
        return MemoryHistoryEntry(
            event=str(row.get("event") or row.get("action") or "UPDATE").upper(),
            created_at=row.get("created_at") or row.get("updated_at"),
            before=row.get("old_memory") or row.get("before"),
            after=row.get("new_memory") or row.get("after") or row.get("memory"),
        )
