# Privacy controls: deployment verification

This public guide records implementation checks and reusable verification steps.
Personal account details, source coverage, memory counts, approval conversations,
production screenshots, timestamps and backup identifiers belong in private
operating records.

## Implemented controls

- Every Memory Review view has a sticky privacy bar showing state, recipient,
  source sections, date scope, purpose flags, settings, pause/resume and revoke.
- Historical initialization, incremental updates, organization and recall use
  separate consent flags. Consent binds the actual models, destinations, scope,
  source versions and budget policy.
- Whole-note `memory: none/local/cloud`, `private: true` and restrictive inline
  blocks apply to extraction and original retrieval, including stale indexes.
- Individual memory permissions and derived-source restrictions cannot silently
  widen a source's permission through another support or a manual correction.
- Checks run before cloud requests and before committing results. Pausing or
  revoking does not retract already-sent requests or delete user-held backups.
- Raw diary and memory data remain under local control, but authorized bounded
  context may reach cloud models. This is not a zero-egress guarantee.

## Verification procedure

1. Verify the authorized deployment host, backups and the exact release manifest.
2. Use synthetic sources to test denied access without consent and after a model,
   destination, scope or budget change.
3. Exercise private/local/none while old indexes or queued model requests exist;
   ensure restricted text cannot leave through extraction or retrieval.
4. Verify separate history/incremental/organization/recall flags, CSRF protection,
   user ownership, stale-scope rejection, pause and revoke.
5. Check the sticky privacy display at desktop and mobile widths. Actual model
   destinations and the saved/current consent binding must be visible.
6. Before real initialization, scan and inspect the intended scope, obtain valid
   consent, then resume. Keep the resulting personal metadata and evidence private.

The privacy increment's recorded release regression passed 518 tests. This proves
those code checks, not that every real deployment or model result is correct.
Provider switching and fixed-batch budget behavior have additional verification
in [Codex deployment and acceptance](codex-provider-air-2026-09-09.md).

## Budget policy

Normal journal processing defaults to 100000 request characters per UTC day,
with 900 per fragment and 4000 original characters per source version. An explicitly
authorized fixed historical batch can have a separately metered daily-total
exception. It does not widen source permissions or future incremental scope and
closes persistently when the batch terminates. See [PRD 16.6](PRD.md#166-用户确认的预算调整2026-09-09).

No public report should contain real diary or memory bodies, personal identities,
full request logs, runtime databases or private backup locations. Use synthetic
reproductions and redacted error categories for issue reports.
