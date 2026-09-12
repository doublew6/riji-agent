# Model failure diagnostics

Issue #47 adds fixed provider error codes to new HTTP-provider failures and keeps
them in mentor step records and private EvalMesh observations. Existing historical
generic errors are not reclassified. The original failed evaluation request has
an unknown cause; a successful later request does not establish that cause.

| Observation | Code | Mentor step |
| --- | --- | --- |
| Timeout | `model_timeout` | unknown outcome |
| Connection establishment failure | `model_connection_failed` | unknown outcome |
| Other transport interruption | `model_transport_failed` | unknown outcome |
| HTTP 401 / 403 | `model_authentication_failed` / `model_permission_denied` | failed |
| HTTP 429 | `model_rate_limited` | failed; not proof of exhausted account quota |
| Explicit Codex quota failure | `model_quota_exhausted` | failed |
| HTTP 5xx | `model_server_failed` | unknown outcome |
| Other HTTP rejection | `model_request_rejected` | failed |
| Explicit refusal or content-filter completion | `model_refused` | failed; refusal text is not persisted |
| Invalid JSON or completion shape | `model_output_invalid` | unknown outcome |
| Unclassified model failure | `model_request_failed` | unknown outcome |

Codes describe directly observed failure categories. An ambiguous network or server
failure does not establish whether the remote service processed the request. Such
steps still require reconciliation before resume. Known rejections may be resumed
explicitly after correction; neither category automatically retries within mentor
generation, resets spent budget, creates substitute artifacts, or switches provider. Existing maximum
generation-repair counts are unchanged, and transport failures are not repair prompts.
The journal extractor also performs one provider request per invocation. The existing
production journal-task scheduler has its own bounded retry policy; this change does
not remove or redefine that policy.

The provider and persistent diagnostics never include raw exception text, response
bodies, URLs, headers, model keys or journal content. Provider/model identity remains
separate request metadata. An arbitrary error containing words such as `401`, `quota`
or `malformed` does not become an allowlisted category through substring matching.

The current boundary corpus now uses the actual Codex timeout/login/quota codes and
expects classified results. Older frozen corpora and initial scores are retained;
do not compare their error-field assertions as if the grader contract were unchanged.

## Explicit synthetic evaluation route (Issue #49)

The synthetic DeepSeek factory injects an HTTPX client with `trust_env=False`
and TLS verification enabled. Environment/system proxies and `SSL_CERT_FILE` /
`SSL_CERT_DIR` CA overrides are ignored for this controlled evaluation client.
Production DeepSeek/OpenAI-compatible defaults and Codex runtime routing remain
unchanged. There is no automatic route fallback or additional retry.

A single route-policy helper supplies the client settings, sealed snapshot receipt
and each provider result's `provider_route_policy` metadata. The policy contains
no proxy address, credentials or exception text. Preparation requires the frozen
`providers.py` digest to match the current route implementation; a programmatic
`SuiteOptions.subject` pointing at an old/different implementation fails closed
before any receipt is sealed, without importing code from that subject.
Boundary-only snapshots record
`no_model` and boundary execution creates no live client. Metadata describes the
application configuration, not a claim about every intermediary on the network.

HTTPX can discover macOS system proxies even without proxy environment variables;
its injected MockTransport bypasses that discovery, so route tests construct a real
HTTPX client and simulate the system-proxy discovery seam without network calls.
TLS verification, the inherited production route, failed-attempt counting, safe
error categories and absence of automatic retries are checked separately. See the
[HTTPX source](https://github.com/encode/httpx/blob/0.28.1/httpx/_utils.py) and
[environment documentation](https://www.python-httpx.org/environment_variables/).

DeepSeek documents non-streaming empty-line keep-alives and streaming SSE comments;
its ten-minute cutoff applies when inference has not started. HTTPX read timeouts
measure waiting for a data chunk, not total request duration. Existing JSON parsing
accepts leading keep-alive whitespace. The observed hidden route difference does
not establish the cause of earlier interruptions; paired diagnostic requests have
succeeded on both routes. Preserve historical failures and use new sealed batches
for reassessment. See [DeepSeek keep-alives](https://api-docs.deepseek.com/quick_start/rate_limit/)
and [HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/).

## Bounded DeepSeek streaming (Issue #50)

DeepSeek now requests SSE by default; the generic OpenAI-compatible adapter keeps
its non-streaming JSON default. Neither model selection, thinking effort, client
route, TLS settings nor retry policy changes. The adapter assembles content and
indexed tool-call fragments across UTF-8 boundaries, comments and multiline data
events. It also accepts usage-only chunks. Only a successful `stop` or `tool_calls`
terminal followed by `[DONE]` produces an `AssistantTurn`. A truncated stream,
invalid JSON/shape, duplicate tool IDs, incomplete fields or exceeded data limit
produces `model_output_invalid`; partial tools and partial answers are discarded.
Refusal uses `model_refused`; transport and timeout retain the existing safe codes.
The HTTP response context closes on success and every failure, without retry.

Limits are 4 MiB incoming decoded bytes including comments/blank lines, 256 KiB
per line, 512 Ki characters per content/reasoning/argument field, and 32 tool calls.
A monotonic 600-second elapsed check runs when a data block arrives, measured from
request start. This is not a strict wall-clock cancellation deadline: a blocking
read remains governed by the existing HTTPX read timeout. The caller's permission,
lease and budget checks remain authoritative: this transport cap does not extend
the caller's 120/600-second business budget or tie request duration to a 180-second
worker lease. Keep-alives cannot bypass the byte
budget or elapsed checks indefinitely.

`AssistantTurn.reasoning_content` is optional internal metadata excluded from its
repr. AgentRunner passes back only actual model-provided reasoning from tool turns
within the current run. All previous tool subturns in that run remain available;
missing reasoning is not invented. Chat history still admits only role/content,
and a new run does not recover prior reasoning. The field does not enter final
answers, personal-memory candidates or runtime trace content. EvalMesh counts the
actual request messages, including continuation reasoning, but its private trace
stores only reasoning character counts. Those traces therefore cannot reconstruct
the full reasoning-bearing wire request. Final-answer reasoning is discarded.

This implements the documented tool continuation requirement within the current
run; it does not claim full cross-session thinking-context persistence. See the
[DeepSeek completion API](https://api-docs.deepseek.com/api/create-chat-completion/)
and [thinking-mode tool contract](https://api-docs.deepseek.com/guides/thinking_mode/).
The later streaming and non-streaming diagnostic calls both succeeded; this is not
a reproduction or explanation of the original timeout. Reliability and semantic
acceptance require a new frozen batch, retaining all earlier failed attempts.

## Live evaluation environment validity (Issue #52)

Explicit live batches containing model cases acquire a task-owned macOS idle-sleep
assertion using `/usr/bin/caffeinate -i -w <runner PID>`. The runner confirms that
its child remains alive after a 0.1-second startup check, and releases that exact
child on completion or exception. Cleanup first requests termination, waits one
second, and, if needed, kills only that child and waits one more second. The PID
watch also bounds the assertion to the runner's lifetime after abrupt runner exit.
Setup failure prevents batch execution. An assertion that exits early, or a cleanup
failure, invalidates the environment assessment. No display assertion, persistent
power setting, unrelated process operation or power-log collection is performed.
A running child is an assertion setup check, not proof that every sleep mode is
prevented. Clamshell and manual sleep remain possible: keep the host awake and do
not close its lid while collecting a live stability sample. On other operating
systems this macOS assertion is not applicable; timing checks still run.

Each provider attempt records wall and monotonic elapsed seconds and their signed
difference in its existing private trace, including failed attempts and traces
whose content is omitted at the size limit. The batch uses the same timing check.
An absolute difference greater than five seconds, a negative elapsed clock or a
non-finite measurement fails environment validation. Five seconds is a conservative
clock-continuity tolerance, not a new model timeout or business budget. Ordinary
long requests with consistent clocks remain valid. Both forward and backward
wall-clock corrections, including NTP changes, can invalidate a batch; these
measurements alone do not establish that suspension caused a provider failure.
Small or compensating clock changes and sleep that advances both clocks equally
are not a complete OS sleep detector.

`environment.json` records this assessment separately from the unmodified machine
summary, including planned attempts, numeric durations and fixed error codes.
Missing/malformed available provider timing is a validation failure, not a clean
measurement. Live batches also compare the total private-record count against
planned attempts, including boundary cases in mixed batches; an entirely missing
record is an environment failure even when the machine summary reports success. When a command target is killed before saving a private record, its
original harness failure remains in the machine denominator; no provider attempt
count is invented. The CLI returns nonzero for either machine/reporting failure
or environment failure, even when every machine grader passed. No case, model,
request timeout, repetition, grader, 120/600-second caller budget or retry policy
is changed, and invalid batches are never automatically retried or reused.

The sealed receipt contains `environment_policy`. Preparation and the supported
`run_prepared` path verify the frozen environment, provider, suite and CLI source
bytes against the current runner implementation, without importing an arbitrary
subject. The CLI source is a batch-level frozen artifact, outside the target
fixture. Old/different helpers or a mismatched policy fail closed before starting
the assertion or target. This contract does not retroactively modify an old runner
or old results; run a fresh process with the matching implementation and create a
new sealed batch after changing the evaluation environment implementation.
Boundary-only runs, even with `--live`, and preparation without execution do not
start an idle-sleep assertion. Boundary results record the environment check as
`not_required` and do not sample these clocks.
