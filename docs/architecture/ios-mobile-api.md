# Personal mobile API v1

Status: implementation contract for issue #55. JSON keys use snake_case. All routes
are below `/mobile/v1`. HTTPS is terminated on the private Tailscale network and
forwarded only through the fixed mobile proxy `127.0.0.1:8766`; the existing mixed
service stays on `127.0.0.1:8765`. No raw diary file upload is supported.

## Authentication and errors

Except pairing, every request requires `Authorization: Bearer <device_token>`.
Tokens are distinct from Review/transport/model credentials. The client stores the
token in Keychain. All responses use `Cache-Control: no-store`. HTTP errors have
`{"detail":"stable_error_code"}`; 401 means missing/revoked device, 409 a conflicting
version/operation, 422 invalid input, 503 unavailable. Never show a failure as an
empty successful list. Redirects are unsupported; clients must not forward tokens
to another origin. Request payload limit is 64 KiB; response limit is 4 MiB (oversize returns 503). Identifiers are ASCII letters, digits,
`_` and `-`, at most 128 characters. Operation IDs should be newly generated UUIDs.

`POST /pair` body: `{"code":"<one-time-code>","device_name":"My iPhone"}`.
Response: `{"api_version":1,"device_id":"...","device_token":"..."}`.
The operator creates a 10 minute single-use code bound to an existing verified
owner. An uncertain pairing must be replaced with a new operator code, not replayed.

`GET /capabilities` response:
```json
{"api_version":1,"device":{"id":"...","name":"My iPhone"},"capabilities":{"mentors":true,"memory":true,"privacy":true,"roundtable":false,"workflows":false,"diary_upload":false},"mentors":[{"id":"gentle_reviewer","name":"温柔回顾者"},{"id":"blunt_coach","name":"直率教练"},{"id":"future_self","name":"未来的我"},{"id":"wang_yangming","name":"王阳明"}]}
```
`memory`/`privacy` reflect runtime availability. The other three unsupported
capabilities are always false. `POST /device/revoke` takes `{"operation_id":"..."}`;
its successful receipt is the final response this credential may receive.

## Durable operations

Every mutation except pairing takes `operation_id`. A valid accepted mutation
returns HTTP 200 with:
```json
{"operation_id":"...","kind":"mentor_message","state":"completed","result":{"conversation_id":"..."},"error":null}
```
Kinds: `mentor_message`, `mentor_control`, `memory_action`, `privacy_action`,
`device_revoke`. States: `pending`, `completed`, `failed`, `unknown`. `result` is a
small action-specific object or null; `error` is a safe code or null. Completed
means the action was accepted/applied, not that a mentor has finished replying.
`GET /operations/{operation_id}` returns this same envelope; 404 means no durable
record exists for that authenticated device. Receipts are not garbage-collected;
deleting a memory does not delete its operation receipt. After a 404 the user may
explicitly retry the exact same operation ID and body; a delayed original request
is deduplicated by the same ledger. Never create a new ID for that retry.
Same ID and identical body returns the receipt; changed input is HTTP 409
`operation_conflict`. Persist ID/body before sending. On timeout query this ID;
never automatically create a replacement mutation. A recorded unknown state needs review
and must not be replayed. Restart turns interrupted pending receipts into unknown.

## Mentor private conversations

Four fixed persona IDs are as shown above. Each device/persona has its own continuing
conversation and draft; Feishu history is not implicitly imported. Each new user
message selects currently valid sources. Safe delivered history has its own
immutable source dependencies, rechecked before model use and delivery; old
source-dependent text is not made current by starting another turn.

`GET /mentors/{persona_id}/conversation` (latest page), optionally
`?cursor=<next_cursor>` for older history:
```json
{"conversation_id":null,"status":"empty","revision":0,"messages":[],"notice":null,"next_cursor":null,"history_has_more":false,"history_version":"opaque-sha256"}
```
An existing conversation uses its ID, actual status (`queued`, `running`,
`delivering`, `completed`, `interrupted`, `stopped`, etc.) and input revision.
Messages: `{"id":"...","actor":"user|persona_id","text":"...","created_at":0.0,"unavailable":null,"source_ids":[]}`.
Only delivered AI replies appear; unavailable source-dependent messages have empty
text and `unavailable:"source_changed_or_revoked"`. `notice` is a safe code or null.
Each page is chronological and contains at most 200 complete messages, with the
entire serialized JSON bounded to 1 MiB. `next_cursor` addresses the older page;
`history_version` binds pages to their device/persona and exact response snapshot.
Changed history or a foreign cursor fails with HTTP 409
`conversation_page_changed`; reload the latest page. Source IDs are memory IDs
for opening memory details. No internal owner or binding IDs escape.

`POST /mentors/{persona_id}/messages` body:
`{"operation_id":"...","text":"User-written message"}` (1–10,000 characters).
Receipt result: `{"conversation_id":"..."}`. Read/poll conversation for the real
answer. Sending a new message while generation is active fails with
`mentor_busy`; retained client input must remain editable.

`POST /mentors/{persona_id}/control` body:
`{"operation_id":"...","action":"stop|resume","expected_revision":1}`.
Result: `{"conversation_id":"..."}`. Stop targets the verified current device/persona
conversation even when expected_revision is stale; resume strictly checks revision.
Resume is a deliberate user action and may
fail when the existing worker cannot safely resume an uncertain operation.

## Memory

`GET /memories` and optional `?cursor=<opaque-cursor>` return
`{"items":[<memory>],"next_cursor":null,"updated_at":"<UTC ISO timestamp>"}`.
Pages hold at most 100 records. Membership/content changes invalidate a cursor with
HTTP 409 `memory_page_changed`; refresh from page one. `GET /memories/{memory_id}`
returns one memory object:
```json
{"id":"...","content":"...","version":"opaque-sha256","scope":"shared|persona","persona_id":null,"status":"active|archived","state":"current","privacy":"cloud|local|none","updated_at":null,"reviewed_at":null,"sources":[{"id":"riji/daily/2026-01-01","relative_path":"daily/2026-01-01.md","line":1,"section":"Notes","version":"...","kind":"daily"}]}
```
`state` preserves actual source state (e.g. `source_invalid`, `historical`,
`conflict`, `pending`); it is not proof of current truth. Source metadata may be
unavailable; `sources` then is empty, without manufacturing an original document.
Only safe relative `.md` paths may be opened under the already authorized vault.

`POST /memories/{memory_id}/actions` body:
```json
{"operation_id":"...","expected_version":"...","action":"update","content":"Corrected text"}
```
Actions: `update` (1–2,000 characters), `reconfirm`, `archive`, `restore`, `delete`
(add `"confirmation":"DELETE"`), `permission` (add `"permission":"cloud|local|none"`).
Other optional fields may be omitted. Result: `{"memory_id":"...","deleted":false}`.
Read detail again after success for the authoritative version. Changed versions
fail with `memory_version_changed`; deletion preserves the source diary and uses
existing suppression. No raw Mem0 endpoint is accessible.

## Extraction and purpose permissions

`GET /privacy`:
```json
{"available":true,"valid":true,"binding":"...","permissions":{"history":false,"incremental":true,"organization":true,"recall":true},"paused":false,"targets":{"extraction":{"provider":"...","model":"...","host":"..."},"recall":{"provider":"...","model":"...","host":"..."}},"source_scope":{"sections":["Notes"],"date_from":null,"date_to":null,"mentors":["gentle_reviewer"]},"limits":{"daily_chars":100000,"fragment_chars":900,"source_chars":4000},"progress":{"discovered":0,"completed":0,"pending":0,"failed":0}}
```
Unavailable returns `{"available":false}`. No absolute paths, initialization
exceptions, secret destination URLs, raw source text or database details escape.
The binding covers the existing server configuration. Directory authorization is
not purpose consent. Show actual targets, source_scope and all four toggles before explicit grant.

`POST /privacy/actions`: `operation_id`, `action` (`grant|revoke|pause|resume|scan`),
`binding` (from current GET), and for grant `acknowledged:true` plus all four exact
boolean `permissions`. Result: `{"applied":true}`. Stale bindings fail; resume needs
valid existing consent. Grant keeps extraction paused until explicit resume. No
mobile action reopens initialization or requests unlimited processing.

Memory version checks and complete cooperating read/modify/write operations share
a process/thread mutation guard with riji-agent writers wired to the same runtime,
including existing Review and background jobs. Explicit local/none permission also
filters legacy recall without a journal engine. Grant and paused state are
published in the same consent-store transaction before change callbacks.
This is cooperative serialization, not server CAS against direct Mem0 administrator
mutations. Do not run uncoordinated direct backend writes during mobile use.

## Verification and deployment limits

Synthetic composed verification covers device pairing/replay/expiry, owner and
persona separation, continuous private generation through the existing worker,
revocation before model execution and before delivery, disabled-mobile recovery,
source-invalidated replies, journal deletion suppression, guarded version conflicts,
unknown outcomes without replay, stale stop versus strict resume, privacy bindings,
proxy route/header/body/redirect limits, private CLI output and frozen Fleet data.

Final integrated backend regression after both review repairs passed **2,020 tests
with five optional skips**. The skips require an explicit Codex test executable
(four) or an optional browser runtime (one). The first rerun encountered eight
local toolchain failures from system Xcode shims; synthetic launcher tests now use
their running Python interpreter, and the test environment selects the installed
Command Line Tools Git. All tests passed on the subsequent complete run.

Additional regressions cover both directions of actual overlapping memory writes
(the negative control fails when the guard is removed), production memory factory
composition, grant callbacks before resume, complete owner pagination, changed
memory followed by safe continued chat, whole-message history byte limits,
historical-source deletion and shared retention, and exact prospective-message
history boundaries.

Command: PYTHONPATH=. uv run --no-sync pytest with the project environment and
installed Command Line Tools on PATH. No real model calls, Air deployment, phone
pairing, Tailscale routing or production memory changes are implied by these tests.
Actual device/network and source-consumption acceptance remain required before
reporting a working phone-to-Air memory loop.
