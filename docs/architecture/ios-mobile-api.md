# Personal mobile API v1

Status: implementation contract for issue #55. JSON keys use snake_case. All routes
are below `/mobile/v1`. The approved personal installation uses dedicated public
HTTPS port 10000 forwarding only to the authenticated `127.0.0.1:8767` proxy.
The existing private proxy `127.0.0.1:8766`, Serve 443/8443 mappings and mixed
service `127.0.0.1:8765` remain separate and unchanged. No raw diary upload exists.

The 2026-09-17 managed-connection specification supersedes the earlier private-only
phone assumption for this personal installation. The phone requires no Tailscale,
URL entry or pairing code. Installation provisions a distinct revocable device
credential; there is no public registration, account switching or public enrollment.
Code readiness does not establish deployment, USB import or phone connectivity.

## Managed installation and public admission

`mobile provision` verifies an existing principal against the current mentor
configuration, issues a unique hashed/revocable device credential and writes a new
exclusive mode-0600 file outside the vault. Failure removes the output and revokes
the issued device. Origins are bounded to 2,048 UTF-8 bytes and the complete
serialized package to 16 KiB before any credential is issued. Tokens are never
printed or embedded in the app. The trusted
USB installer supplies `Library/Caches/MobileInstallation.json`; cache transport
avoids backup. Its snake_case contract is:

```json
{"api_version":1,"endpoint":"https://service.example:10000","previous_endpoint":"https://service.example:8443","device_id":"unique-device-id","device_token":"example-token"}
```

The endpoint, device ID and token above are nonfunctional examples. Use the
private package produced by `mobile provision`; do not put real credentials in
documentation, fixtures or a shared app bundle.

`previous_endpoint` is optional; `device_id` and `device_token` must occur together
or both be absent. Origins must already be canonical HTTPS origins without path,
userinfo, query or fragment. Packages are bounded to 16 KiB and reject symlinks,
nonregular files and incomplete/invalid credentials. Keychain is read before import;
a locked or unreadable Keychain leaves the package retryable and pauses access.
Existing credentials always win: same origin retains the identity; a changed
origin requires exact `previous_endpoint` binding and retains the same token and
device. Drafts and pending operation IDs migrate only for that device; the original
archive remains. Unreadable state and different destination content fail closed.

After validation, the importer atomically claims the package pathname into a
private recovery directory, revalidates those exact bytes and removes only the
claimed file before state/credential activation. A replacement or mismatch fails
closed and preserves unexpected bytes for controlled recovery; USB transfer
requires the app to be quiescent. A retained .MobileInstallationRecovery directory
blocks subsequent imports until operator resolution. Final encoded migration data is bounded to 8 MiB
before package consumption, and a newly appeared destination is rechecked for
equality before exclusive atomic publication. Consumption failure activates
nothing. Failed Keychain update keeps
the old record; a new package is required after consumption. The device-only,
nonsynchronizing Keychain item uses `WhenUnlockedThisDeviceOnly`. The small
`MobileInstallationReceipt.json` contains only `status` (`success`/`error`),
`used_device_id` and `unused_provisioned_device_id`; missing receipts need operator
inspection, never replay. Previous receipts are invalidated before processing a
new package; receipt failure does not roll back working credentials. Operator
acceptance requires package absence and the current issuance device ID matching
either receipt ID, in addition to successful status and capabilities.
Explicit disconnect/revocation discards any leftover package before removing the
old credential, preventing a rejected package from becoming a new enrollment.

Startup, foreground and explicit retry restore authorization and fetch capabilities;
ordinary foregrounding preserves the navigation identity. No retained operation is
sent automatically. Settings provides service status/retry, permissions, retained
drafts and explicit revoke. Missing installation requests an updated personal
installation; outages leave local diary editing available.

Public admission rejects every enrollment, admin, Hermes, health and Fleet route.
Exact mobile route/method/query filtering and fresh read-only SQLite token lookup
precede body reads and upstream access; revocation decisions are never cached.
Requests are bounded to 64 KiB, 5 seconds to read the body, 8 concurrent handlers
(admission occurs before authorization work), 120 requests/device/minute and 600
authorized requests/minute. Failed authentication has its own 600/minute budget,
so anonymous quota exhaustion cannot consume the authorized minute budget.
Rate entries remain bounded to 1,024. Bounded read-only SQLite lookups run off the
event loop and return 503 when storage is unavailable, rather than revoking clients.
The standard h11 transport and an application header check bound headers to 16 KiB.
Upstream work has a 30-second total deadline and 4 MiB response limit. Saturation,
rate limits, deadlines, malformed responses and redirects return safe JSON errors.
Only authorization/content-type are forwarded; cookies, forwarded headers and
environment proxies are excluded. Access logs and proxy-header trust are disabled.
Framework route/method errors use generic non-cacheable JSON. Uvicorn can return
plain-text overload 503 (with the configured default no-store header), and pre-ASGI
HTTP parsing failures do not guarantee application JSON or headers; the client
treats both as failed requests. These limits belong to the separate public proxy; the legacy private factory stays
compatible. The existing backend revalidates authorization and privacy as before.

## Authentication and errors

Every public request requires `Authorization: Bearer <device_token>`. Pairing remains
available only through the legacy private adapter for compatibility.
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
{"api_version":1,"device":{"id":"...","name":"My iPhone"},"capabilities":{"mentors":true,"memory":true,"privacy":true,"memory_overview":true,"roundtable":true,"workflows":false,"diary_upload":false},"mentors":[{"id":"gentle_reviewer","name":"温柔回顾者"},{"id":"blunt_coach","name":"直率教练"},{"id":"future_self","name":"未来的我"},{"id":"wang_yangming","name":"王阳明"}]}
```
`memory`/`privacy` reflect runtime availability. `roundtable` advertises the device-owned host and invitation flow below;
`workflows` and `diary_upload` remain false. `POST /device/revoke` takes `{"operation_id":"..."}`;
its successful receipt is the final response this credential may receive.

## Durable operations

Every mutation except pairing takes `operation_id`. A valid accepted mutation
returns HTTP 200 with:
```json
{"operation_id":"...","kind":"mentor_message","state":"completed","result":{"conversation_id":"..."},"error":null}
```
Kinds: `mentor_message`, `mentor_control`, `memory_action`, `memory_organize`, `privacy_action`,
`device_revoke`, `roundtable_create`, `roundtable_message`, `roundtable_command`,
`roundtable_result`. States: `pending`, `completed`, `failed`, `unknown`. `result` is a
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

## Structured memory overview (#57)

`capabilities.memory_overview` advertises the projection independently of the raw
`memory` capability. Old servers retain the native raw-record entry and show that
the overview is unsupported. Both private and public proxy admission use the same
exact GET/action routes; only GET accepts a bounded `cursor` query.

`GET /memory-overview?cursor=<opaque-cursor>` returns these allowlisted fields:

| Field | Contract |
| --- | --- |
| `version` | Opaque SHA-256 of owner, current permission binding, catalog epoch, decorated memory versions/states, full stored report, stopped seeds and latest job; excludes the request clock. |
| `updated_at`, `organized_at` | ISO read time and nullable successful report time. |
| `cache_expires_at` | Epoch seconds, at most 900 seconds after the read. |
| `state` | `ready`, `partial`, `unorganized`, `empty`, `permission_denied`, `failed`, or `invalid`. Failed latest jobs may retain valid previous results. |
| `job_status` | Actual latest job status or `idle`; does not imply every unorganized record is queued. |
| `coverage` | `eligible`, `organized`, `unorganized`, `stopped`, `invalid`. Current covered seeds are counted separately from explicit stopped seed versions; invalid includes stale groups/source evidence. |
| `categories` | Six `{id,title,summary_count}` themes. Counts include shared summaries only; mentor/derived observations have their own entry. |
| `summaries` | `{id,category,title,summary,scope,persona_id,states,evidence_ids,updated_at}`. States distinguish historical, plan, time-bound and observation material. |
| `observations` | `{id,scope,persona_id,summary,limitation,support_ids,counter_ids}`; current independent dates and counterevidence revalidated by the existing journal validator. |
| `comparisons` | `{id,scope,persona_id,kind,reason,evidence_ids}`; two different current evidence IDs, no automatic merge or deletion. |
| `evidence` | Existing memory view plus nullable `observed_at`, `kind`, `certainty`; only references used on this page. |
| `next_cursor` | Nullable version-bound offset; at most 20 combined summary/observation/comparison entries and 20 references per entry, with the whole response at most 1 MiB. |
| `can_organize`, `organize_binding`, `organize_reason` | Current permission/version-bound action admission; paused, restoring, unauthorized, pending or no new eligible work disables organization. |

The projection trusts neither stored JSON nor previous HTML review. It checks the
entire legacy group against top-level versions or the journal group against its
own versions; owner/scope/persona, active state, safe content and current decorated
sources must agree. Managed journal evidence cannot become legacy evidence when
the journal engine is absent. Journal organization consent and catalog epoch are
authoritative. A shared mutation guard surrounds projection, followed by a second
current source/permission/version read. Nothing in GET calls a model, requests a
job, resets a seed or repairs initialization. Arbitrary report metadata and raw
reports are never returned. Changed cursors fail with HTTP 409
`memory_overview_changed`; discard accumulated pages and reload page one.

`POST /memory-overview/actions` body:

```json
{"operation_id":"new-uuid","action":"organize","expected_version":"current-64-character-hash","binding":"current-64-character-binding","acknowledged":true}
```

The ordinary `memory_organize` receipt contains
`{"accepted":true,"run_id":123,"state":"pending"}` or
`{"accepted":true,"run_id":null,"state":"already_pending"}`.
`OrganizationStore.request_if_idle` preserves pending/processing jobs and their
backoff. Existing bounded workers may continue multiple batches. This action does
not reopen stopped/failed historical versions, grant consent or reset a budget.
Timeout/relaunch recovery reconciles the original operation ID before another
request. A failed correction retains its existing input and version draft.

The native read cache contains only one bounded first page, dated and scoped to
endpoint/device/credential, protected on device and excluded from backup. It is
separate from durable draft/operation archives. Read admission additionally needs
an independent persistent content digest: known invalidation removes admission
before deleting bytes, so deletion failure or restoration of old bytes cannot
resurrect a revoked snapshot after relaunch. No cache supplies mutation authority.
Expiry, device/account change, known source changes and purpose revocation remove
the old projection. A fresh same-version read retains loaded later pages and the
current reading position; a changed version discards them. Offline fallback is
explicit, time-limited, and only for transport/server failures. The recent-change
entry is scoped to loaded overview evidence, not a complete audit history. Raw
memory search explicitly covers the currently loaded raw page.
Foreground and pull-to-refresh fetch current state. The overview polls at 15
seconds while a job is pending/processing and 60 seconds while idle. Expiry has
its own timer so a shorter cache deadline does not wait for polling.

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

## Native persistent roundtables (#56)

The native capability is advertised separately as `roundtable`. Device-owned
mobile host bindings use the existing personal tenant and the four fixed mentor
applications. Their persistent `mobile-*` rooms route only to the mobile channel;
room checks and delivery revalidate the originating device. Neither another device
of the same principal nor a private/Feishu conversation can become the native
roundtable origin. Existing non-mobile handoff cleanup keeps its own draft service.

- `GET /roundtables?cursor=`: at most 100 `{id,title,status,updated_at,revision}` entries per page,
  with `current_id`, `truncated`, `next_cursor` and `history_version`. Cursors bind to the device and current list snapshot; changed snapshots return `409 roundtable_list_changed`. Native navigation retains its own
  selected history entry or explicit new-chat draft independently of this default.
  Local reading anchors and result selections are scoped to that account and conversation.
- `POST /roundtables`: `{operation_id,text,statement_kind?}` creates a persistent host conversation
  immediately, without a setup form. `statement_kind` defaults to `user_statement` and preserves the same plan/feedback classification as subsequent messages. No memory retrieval occurs at creation.
- `GET /roundtables/{id}?cursor=`: a version-bound chronological page of at most
  200 whole messages, at most 1 MiB. Includes current input/state/summary/correction
  versions, run ID/kind/speakers/mode/rounds, suspended run, current summary, up to
  30 run records, fixed audience and notice. `material_sources` contains only currently
  usable selected source text (or an empty unavailable placeholder); `progress` gives
  the next/current actor, stage and round for active work. Messages preserve numeric timestamps,
  statement kind, run, superseded status and source IDs. Only sent AI artifacts
  are visible; invalid sources become non-sensitive unavailable placeholders.
- `POST /{id}/messages`: `{operation_id,text,expected_revision,statement_kind}`.
  Statement kinds are `user_statement`, `user_plan`, `user_feedback`. Ordinary
  messages address the host; active discussion supplements invalidate old output.
- `POST /{id}/commands`: `{operation_id,action,expected_revision,...}`. Actions are
  `start_run`, `stop`, `summarize`, `resume`, `continue`, `correct`, `archive`,
  `restore`. Invitation adds `personas`, `mode`, `rounds`, `preview_hash`; correction
  adds `text` and explicit user-artifact `supersedes`. Two to four speakers and one
  to two rounds are bounded. Unknown generation/delivery cannot be blindly resumed.
- `POST /{id}/materials/preview`: `{expected_revision,artifact_ids,memories,
  personas,mode,rounds}`. Memories are explicit `{id,expected_version}` entries,
  active, owner-shared and cloud-eligible. Journal-derived evidence is available
  through those authoritative memory records; there is no raw journal upload.
  The response shows all five audience members and the exact selected materials.
  Preview fingerprints bind device, conversation, state/input revisions, artifacts,
  source versions, speakers, mode and rounds. Source replacement and lifecycle
  start commit atomically; a failed start preserves the former context and grant.
- `POST /{id}/result-preview`: `{operation_id,artifact_ids}` for one to five
  currently available selected artifacts. Its durable receipt contains `result_id`
  only. It never creates a diary draft or writes a diary on the server.
- `POST /{id}/result-validation`: `{result_id}` is a fresh read, never an operation
  receipt. Returns current text, typed provenance, binding and validation time
  after device, selection, source, correction and hash checks.

All `/{id}` paths above are below `/mobile/v1/roundtables`. Strict request models
and the exact proxy route allowlist apply. Operation fingerprints include the
conversation and the full body. The native history cache is bounded and dated to
15 minutes, scoped to endpoint/device/credential, protected and excluded from
backup. Persistent digest admission prevents old bytes from being reintroduced
after a known invalidation. Cached reads cannot authorize mutations or diary saves.

The local AI result transaction is separate from manual body autosave recovery.
Its stable result-to-operation binding survives date changes and explicit retries.
A preview binds account, result, date, source identity, exact before/after bytes,
text and rendered independent AI block. Confirmation fetches fresh result
validation and uses coordinated source replacement under the unique parsed Notes
anchor. Existing Markdown/frontmatter and CRLF remain intact. Dedicated receipts
survive later manual edits and never recreate a result the user deleted. When a
write applied before the receipt, exact after bytes or one complete unique exact
block can reconcile it; ambiguous changed documents remain unresolved. A still-
before transaction requires an explicit retry and fresh service validation.

Production-model quality, Air deployment and actual phone/iCloud acceptance remain
separate from the synthetic API, Core, State and native UI test evidence.

### Ten-round mobile invitation correction (2026-09-21)

Material preview and `start_run` accept integer `rounds` from 1 through 10;
confirmation must exactly match the preview's rounds, mode, speakers and sources.
The native selector applies to debate mode; reference mode sends one round.
Existing non-mobile ingress limits/defaults remain unchanged. Private/followup
budgets remain 7 requests / 120 seconds and reference or 1–2 round budgets remain
24 / 600. For longer debates with n speakers and r rounds, a new run records
max(24, 2 × [n × (r + 1) + 2]) requests and 600 + 60 × n × (r − 2) active seconds
(up to 92 / 2520). Reactivation never resets this ledger. Original per-source
900-character snippet / 4000-character version limits remain in force, so
source exhaustion, consensus or explicit stop can end a run before its maximum.
The model's 24-artifact history retains current-run initial opinions and comparison,
then the latest discussion; it never imports another run or revision.

## Member-only persistent group chats (approved 2026-10-15)

The selected-member group path is gated by `capabilities.group_chat`. The native
client keeps private chats under the original per-mentor identity; only the
portrait stage opens those chats. Existing hosted roundtables retain legacy
membership and access rather than being silently converted.

`POST /roundtables` accepts `chat_kind: "group"`, `personas` with two to four
unique fixed mentor IDs and empty `text`. It returns the ordinary
`roundtable_create` operation receipt containing `conversation_id`. Creation
persists an empty group and its actual room/grant without calling a model. The
default name combines member names. Distinct operation IDs create distinct groups
even for the same members; retries reconcile the same durable operation.

List items and details add `chat_kind`, `title`, `personas` and `last_reply_id`. The
last field identifies the most recent AI artifact actually delivered to this
authorized room, not a metadata revision. Mentor capability summaries and private
details expose the same optional field. Native read cursors are local to each
credential/chat, and foreground inbox polling reads metadata without invoking a
model. Legacy defaults
remain compatible. New group `audience` is exactly `personas`, while
`run_personas` represents responders for the current bounded reply only.
`POST /roundtables/{id}/messages` adds optional `reply_personas`: an empty list
means all members, a nonempty list must be a unique member subset. Each requested
mentor contributes at most one reply; no comparison/synthesis host is invoked.
The displayed @ scope must match this request and never narrows message visibility.
Group context contains only eligible group-local messages and actually delivered
outputs, with bounded recent model context; complete history remains pageable.

Versioned group commands are `rename` (new name in `text`, 1–120 characters),
`stop`, `resume`, `end_turn`, `archive` and `restore`. Rename and archive require an idle,
reconciled group; metadata changes advance the existing revision/grant without
rewriting old artifact provenance. Stop preserves delivered results. Resume can
redeliver a known never-attempted output, but unknown sends require reconciliation
before resuming that turn. Explicit `end_turn` preserves the unknown attempt audit,
never replays it, and lets a new message start a distinct run. Late delivery receipts
remain bound to the original artifact/run and cannot interrupt the newer run.
Legacy invitation/material/start-run commands are unavailable
for new groups. Membership is immutable in this first increment.

Native draft/operation archives remain per credential and chat target. The
protected, backup-excluded read cache holds up to eight first pages under the
existing 15-minute/1-MiB total limit; revocation invalidates cache admission before
removing bytes. Switching chats and late unrelated receipts do not move drafts or
navigation. Local read caching is not cross-device chat synchronization. Selected
AI results continue through the existing revalidation and local diary preview.
Selection is limited to one discussion run (up to five messages); provenance uses
that run's user message as its topic, even after the group moves to another topic.
Working summaries retain each item's actor.

Empty-group creation records an `atomic_group_create_v2` operation protocol, then
commits the room, grant, group, device/operation/fingerprint-bound proof and completed
receipt together through a SQLite attached-database transaction. Both databases must
use file-backed rollback journals with synchronization enabled; unsupported modes
fail visibly rather than changing live configuration. This follows SQLite's
[attached transaction contract](https://www.sqlite.org/lang_attach.html).
A later operation lookup can repair a missing completed receipt from the proof.
A v2 interrupted operation with no committed proof is safely failed, allowing a
fresh user creation; an unversioned historical unknown without proof stays unknown.
Active pending operations are never declared failed by this lookup, and the commit
rechecks pending state to reject an obsolete late creator. It does not blindly
repeat creation or infer ownership from a similar group name/member set.
