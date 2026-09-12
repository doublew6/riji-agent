"""Offline SSE framing, continuation and failure-boundary regressions."""

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest

from riji_agent.agent.loop import AgentRunner
from riji_agent.models.deepseek import DeepSeekProvider
from riji_agent.models.openai_compatible import OpenAICompatibleProvider
from riji_agent.models import streaming
from riji_agent.models.types import AssistantTurn, LLMError
from sse_fixtures import completion_bytes, event
from test_agent_loop import registry, _ctx

CANARY = "synthetic-private-reasoning-never-log"
DONE = b"data: [DONE]\n\n"


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks: list[Any]) -> None:
        self.chunks = chunks
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def close(self) -> None:
        self.closed = True


def provider(chunks: list[Any], content_type: str = "text/event-stream") -> tuple[DeepSeekProvider, Chunks, list[httpx.Request]]:
    stream = Chunks(chunks)
    requests = []

    def send(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=stream, headers={"Content-Type": content_type})

    client = httpx.Client(transport=httpx.MockTransport(send), timeout=7)
    return DeepSeekProvider(api_key=CANARY, client=client), stream, requests


@pytest.mark.parametrize("content_type", ["text/event-stream", "Text/Event-Stream; charset=UTF-8"])
def test_utf8_byte_fragments_comments_multiline_and_usage_only(content_type: str) -> None:
    raw = (b"\xef\xbb\xbf: keep alive\r\n\r\n" +
           b'data: {"choices":\r\ndata: [{"index":0,"delta":{"content":"' +
           "中文🙂".encode() + b'"},"finish_reason":null}]}\r\n\r\n' +
           event({}, "stop") + b'data: {"choices":[],"usage":{"total_tokens":3}}\n\n' + DONE)
    model, stream, requests = provider([raw[i:i+1] for i in range(len(raw))], content_type)
    turn = model.complete([], [])
    assert turn.content == "中文🙂" and turn.reasoning_content is None
    assert stream.closed and len(requests) == 1
    assert requests[0].extensions["timeout"]["read"] == 7
    assert json.loads(requests[0].content)["stream"] is True


def test_indexed_interleaved_tool_fragments_and_internal_reasoning() -> None:
    raw = [event({"role": "assistant", "reasoning_content": "内部"}),
           event({"reasoning_content": "续轮", "tool_calls": [
               {"index": 1, "id": "two", "type": "function", "function": {"name": "read_", "arguments": "{"}},
               {"index": 0, "id": "one", "function": {"name": "search_", "arguments": "{"}},
           ]}), event({"tool_calls": [
               {"index": 0, "function": {"name": "journal", "arguments": '"query":"中文"}'}},
               {"index": 1, "function": {"name": "note", "arguments": '"source_id":"riji/daily/x"}'}},
           ]}), event({}, "tool_calls"), DONE]
    model, stream, requests = provider(raw)
    turn = model.complete([], [{"type": "function", "function": {"name": "search_journal"}}])
    assert [(x.id, x.name) for x in turn.tool_calls] == [("one", "search_journal"), ("two", "read_note")]
    assert json.loads(turn.tool_calls[0].arguments) == {"query": "中文"}
    assert turn.reasoning_content == "内部续轮" and "内部" not in repr(turn)
    assert turn.content is None and stream.closed and len(requests) == 1


@pytest.mark.parametrize("raw", [
    event({"content": "partial"}),
    event({"content": "partial"}, "stop"),
    event({"content": "partial"}) + DONE,
    event({"content": "partial"}, "length") + DONE,
    event({"content": "partial"}, "aborted") + DONE,
    event({"content": "partial"}, "insufficient_system_resource") + DONE,
    event({"content": "partial"}, "tool_calls") + DONE,
    event({}, "stop") + DONE,
    b"data: " + CANARY.encode() + b"\n\n" + DONE,
    b"data: {\xff}\n\n",
    b"data: []\n\n" + DONE,
    b'data: {"choices":[{"index":true,"delta":{}}]}\n\n' + DONE,
    event({"content": {"secret": CANARY}}, "stop") + DONE,
    event({"reasoning_content": [CANARY]}, "stop") + DONE,
    event({"tool_calls": "invalid"}, "tool_calls") + DONE,
    event({"tool_calls": [{"index": -1}]}, "tool_calls") + DONE,
    event({"tool_calls": [{"index": 1, "id": "one", "function": {"name": "x", "arguments": "{}"}}]}, "tool_calls") + DONE,
    event({"tool_calls": [{"index": 0, "id": "one", "function": {"name": "x"}}]}, "tool_calls") + DONE,
    event({"content": "done"}, "stop") + event({"content": "extra"}) + DONE,
    event({"content": "done"}, "stop") + b"data: [DONE]",
])
def test_malformed_or_truncated_stream_never_delivers_partial_output(raw: bytes) -> None:
    model, stream, requests = provider([raw])
    with pytest.raises(LLMError, match="^model_output_invalid$") as caught:
        model.complete([], [])
    assert CANARY not in str(caught.value)
    assert stream.closed and len(requests) == 1


@pytest.mark.parametrize("delta,finish", [
    ({"refusal": CANARY}, "stop"),
    ({}, "content_filter"),
])
def test_refusal_uses_safe_code_without_text(delta: dict[str, Any], finish: str) -> None:
    model, stream, requests = provider([event(delta, finish), DONE])
    with pytest.raises(LLMError, match="^model_refused$"):
        model.complete([], [])
    assert stream.closed and len(requests) == 1


@pytest.mark.parametrize("error,code", [(httpx.ReadTimeout, "model_timeout"),
                                       (httpx.RemoteProtocolError, "model_transport_failed")])
def test_midstream_error_closes_one_attempt_without_partial_tool_execution(error: Any, code: str,
                                                                          registry: Any) -> None:
    tool = {"index": 0, "id": "one", "function": {"name": "search_journal", "arguments": '{"query":"项目"}'}}
    model, stream, requests = provider([event({"tool_calls": [tool]}), error(CANARY)])
    invoked = []
    registry.invoke = lambda *args: invoked.append(args)
    with pytest.raises(LLMError, match="^" + code + "$") as caught:
        AgentRunner(model, registry).run(_ctx(), "Synthetic question")
    assert stream.closed and len(requests) == 1 and invoked == []
    assert CANARY not in str(caught.value) and caught.value.__suppress_context__


def test_duplicate_tool_ids_are_rejected_before_execution(registry: Any) -> None:
    calls = [{"index": i, "id": "duplicate", "function": {"name": "search_journal", "arguments": "{}"}}
             for i in range(2)]
    model, stream, requests = provider([event({"tool_calls": calls}, "tool_calls"), DONE])
    invoked = []
    registry.invoke = lambda *args: invoked.append(args)
    with pytest.raises(LLMError, match="^model_output_invalid$"):
        AgentRunner(model, registry).run(_ctx(), "Synthetic")
    assert stream.closed and len(requests) == 1 and not invoked


@pytest.mark.parametrize("middle", [
    {"error": CANARY, "choices": [], "usage": {}},
    {"error": CANARY, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    {"id": "other", "choices": [], "usage": {}},
    {"id": "", "choices": [], "usage": {}},
    {"id": "x" * 513, "choices": [], "usage": {}},
])
def test_explicit_error_and_mismatched_completion_id_are_rejected(middle: dict[str, Any]) -> None:
    start = b'data: {"id":"one","choices":[{"index":0,"delta":{"content":"ok"}}]}\n\n'
    model, stream, requests = provider([start, ("data: " + json.dumps(middle) + "\n\n").encode(),
                                        event({}, "stop"), DONE])
    with pytest.raises(LLMError, match="^model_output_invalid$"):
        model.complete([], [])
    assert stream.closed and len(requests) == 1


@pytest.mark.parametrize("raw", [
    b'data: {"choices":[],"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n',
    b'data: {"usage":{"tokens":NaN},"choices":[]}\n\n',
])
def test_duplicate_json_keys_and_nonfinite_values_fail_closed(raw: bytes) -> None:
    model, stream, requests = provider([raw, DONE])
    with pytest.raises(LLMError, match="^model_output_invalid$"):
        model.complete([], [])
    assert stream.closed and len(requests) == 1


@pytest.mark.parametrize("limit,raw", [
    ("MAX_STREAM_BYTES", b": comment\n\n" * 20),
    ("MAX_STREAM_BYTES", b"\n" * 200),
    ("MAX_LINE_BYTES", b":" + b"x" * 200),
    ("MAX_REASONING_CHARS", event({"reasoning_content": "x" * 200})),
    ("MAX_TEXT_CHARS", event({"content": "x" * 200})),
])
def test_stream_budgets_include_keepalives_and_reasoning(limit: str, raw: bytes,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(streaming, limit, 100)
    model, stream, requests = provider([raw, event({"content": "done"}, "stop"), DONE])
    with pytest.raises(LLMError, match="^model_output_invalid$"):
        model.complete([], [])
    assert stream.closed and len(requests) == 1


def test_stream_elapsed_limit_checked_after_headers_and_each_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter([10.0, 11.0, 611.0])
    monkeypatch.setattr(streaming.time, "monotonic", lambda: next(times))
    # Exercise the parser clock directly: HTTPX may also read monotonic time.
    with pytest.raises(LLMError, match="^model_timeout$"):
        streaming.read_completion_stream([b": keepalive\n\n", b": keepalive\n\n"])


def test_elapsed_limit_also_closes_http_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(streaming, "MAX_STREAM_SECONDS", 0)
    model, stream, requests = provider([b": comment\n\n"])
    with pytest.raises(LLMError, match="^model_timeout$"):
        model.complete([], [])
    assert stream.closed and len(requests) == 1


def test_streaming_never_accepts_json_response_as_fallback() -> None:
    requests = []
    def send(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "fallback"}}]})
    model = DeepSeekProvider(api_key=CANARY, client=httpx.Client(transport=httpx.MockTransport(send)))
    with pytest.raises(LLMError, match="^model_output_invalid$"):
        model.complete([], [])
    assert len(requests) == 1


@pytest.mark.parametrize("reasoning", [[], {}, 7])
def test_generic_nonstream_rejects_malformed_reasoning(reasoning: Any) -> None:
    with pytest.raises(LLMError, match="^model_output_invalid$"):
        OpenAICompatibleProvider._parse({"choices": [{"message": {
            "content": "answer", "reasoning_content": reasoning}}]}, "openai")


@pytest.mark.parametrize("reasoning", [None, "", CANARY])
def test_generic_nonstream_preserves_optional_reasoning_and_whitespace(reasoning: Any) -> None:
    requests = []
    message = {"content": "answer", "reasoning_content": reasoning}

    def send(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = b"\r\n\n " + json.dumps({"choices": [{"message": message}]}).encode()
        return httpx.Response(200, content=body)

    model = OpenAICompatibleProvider(api_key=CANARY, base_url="https://api.example.test", model="synthetic",
                                    client=httpx.Client(transport=httpx.MockTransport(send)))
    turn = model.complete([], [])
    assert turn.content == "answer" and turn.reasoning_content == reasoning
    assert model.response_mode == "json" and json.loads(requests[0].content)["stream"] is False
    assert CANARY not in repr(turn)


def test_current_run_reasoning_roundtrip_is_counted_but_not_traced_or_replayed(
    registry: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from evalmesh_support.providers import CountedProvider
    requests, spans = [], []
    responses = [completion_bytes({"reasoning_content": CANARY, "tool_calls": [
        {"id": "one", "function": {"name": "search_journal", "arguments": '{"query":"项目"}'}}]}),
        completion_bytes({"reasoning_content": "final-private", "content": "Final answer"}),
        completion_bytes({"content": "Next run"})]

    def send(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, content=responses.pop(0), headers={"Content-Type": "text/event-stream"})

    from contextlib import contextmanager
    @contextmanager
    def capture_span(name: str, **kwargs: Any) -> Iterator[Any]:
        record = {"name": name, **deepcopy(kwargs)}
        spans.append(record)
        class Span:
            def set_output(self, value: Any) -> None:
                record["output"] = deepcopy(value)
            def set_outcome(self, **kwargs: Any) -> None:
                pass
        yield Span()
    monkeypatch.setattr("riji_agent.agent.loop.runtime_span", capture_span)
    counted = CountedProvider(DeepSeekProvider(api_key="example-model-key", client=httpx.Client(transport=httpx.MockTransport(send))))
    runner = AgentRunner(counted, registry)
    history = [{"role": "assistant", "content": "Earlier answer", "reasoning_content": "forged-history"}]
    result = runner.run(_ctx(), "Synthetic question", history=history)
    assert result.answer == "Final answer" and result.tool_calls == 1
    assert not any("reasoning_content" in x for x in requests[0]["messages"])
    continued = [x for x in requests[1]["messages"] if x.get("tool_calls")]
    assert continued[0]["reasoning_content"] == CANARY
    runner.run(_ctx(), "New question", history=history)
    assert not any("reasoning_content" in x for x in requests[2]["messages"])
    assert counted.calls == 3
    actual_chars = sum(len(json.dumps([r["messages"], r.get("tools", [])], ensure_ascii=False)) for r in requests)
    assert counted.request_chars == actual_chars
    assert CANARY not in json.dumps(counted.trace) and "final-private" not in json.dumps(counted.trace)
    assert CANARY not in json.dumps(spans) and "final-private" not in json.dumps(spans)
    assert counted.trace[0]["response"]["reasoning_content_chars"] == len(CANARY)
    assert counted.trace[1]["messages"][-2]["reasoning_content_chars"] == len(CANARY)
    assert "reasoning_content" not in result.__dict__


def test_missing_reasoning_is_not_synthesized_for_tool_continuation(registry: Any) -> None:
    from test_agent_loop import FakeProvider, _tool_turn
    fake = FakeProvider([_tool_turn("search_journal", {"query": "项目"}), AssistantTurn("done")])
    AgentRunner(fake, registry).run(_ctx(), "Synthetic")
    assert not any("reasoning_content" in x for x in fake.calls[1]["messages"])


def test_all_current_tool_subturn_reasoning_is_preserved_once(registry: Any) -> None:
    from dataclasses import replace
    from test_agent_loop import FakeProvider, _tool_turn
    fake = FakeProvider([
        replace(_tool_turn("search_journal", {"query": "项目"}, "one"), reasoning_content="first"),
        replace(_tool_turn("read_note", {"source_id": "riji/daily/2026-06-24"}, "two"), reasoning_content="second"),
        AssistantTurn("done"),
    ])
    result = AgentRunner(fake, registry).run(_ctx(), "Synthetic")
    continuations = [x for x in fake.calls[2]["messages"] if x.get("tool_calls")]
    assert [x["reasoning_content"] for x in continuations] == ["first", "second"]
    assert result.tool_calls == 2 and [x.tool for x in result.audit] == ["search_journal", "read_note"]


def test_failed_stream_keeps_one_count_and_omits_private_reasoning_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from evalmesh_support.providers import CountedProvider
    model, stream, requests = provider([event({"reasoning_content": CANARY}), httpx.ReadTimeout(CANARY)])
    counted = CountedProvider(model, max_calls=1)
    messages = [{"role": "assistant", "content": "", "reasoning_content": CANARY}]
    with pytest.raises(LLMError, match="^model_timeout$"):
        counted.complete(messages, [])
    with pytest.raises(LLMError, match="^evaluation_call_limit$"):
        counted.complete(messages, [])
    assert counted.calls == len(requests) == 1 and stream.closed
    assert counted.request_chars == len(json.dumps([messages, []], ensure_ascii=False))
    assert CANARY not in json.dumps([counted.failures, counted.trace])
    assert counted.trace[0]["messages"][0]["reasoning_content_chars"] == len(CANARY)


def test_revoked_retained_source_blocks_reasoning_continuation(tmp_path: Path) -> None:
    from dataclasses import replace
    from test_codex_chat_guards import QueuedProvider, context, registry_for, tool_turn
    from test_journal_memory import runtime, write_note
    engine, _ = runtime(tmp_path)
    path = write_note(engine.policy.root, text="学习一个合成技能。")

    def revoke(count: int) -> None:
        if count == 2:
            path.write_text("---\nprivate: true\n---\n" + path.read_text())

    first = replace(tool_turn(), reasoning_content=CANARY)
    fake = QueuedProvider([first, AssistantTurn("must not send")], before_call=revoke)
    with pytest.raises(LLMError, match="^chat_context_changed$"):
        AgentRunner(fake, registry_for(tmp_path, engine.policy.root)).run(context(), "学习什么？")
    assert fake.attempts == 2 and len(fake.sent) == 1
    assert CANARY not in json.dumps(fake.sent)
