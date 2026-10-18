# Managed personal connection — implementation verification

This is the implementation evidence for the approved Issue #55 managed-connection
specification. The public Air ingress is deployed and the signed app is installed
on the personal iPhone. First-launch authorization verification is pending because
iOS refused to launch the locked phone. The original four tabs, diary editor and
existing private adapter remain intact. Earlier agent results below are retained
as intermediate evidence; the final root verification supersedes their status.

## Final root verification and deployment

- Final complete backend: **2,045 passed, 5 optional skips**, with two installed
  websockets deprecation warnings. This run includes all review repairs.
- Full Swift package: **444 passed**. The final review repairs then passed the
  focused 81-case run and the 23-case installation/lifecycle refinement run.
- Native Simulator: **40 native/editor/Markdown tests and 3 UI tests passed**.
  A further three managed-connection, draft/history and memory UI flows passed
  after review fixes. The last recovery-directory guard has focused Swift coverage
  and is included in the final signed build; that last guard was not rerun in UI.
- Final device build and strict code-signature verification passed. Twenty original
  Simulator files remained byte-identical after all isolated UI testing.
- Verified Air hardware, fresh Fleet preflight and actual process/container
  ownership. A transient Fleet timeout recovered without a restart. Backed up the
  nine-file deployment whitelist, environments, existing launch definitions and
  ingress configuration. Reviewed the final rsync dry-run; copied without deletion.
- Air runtime: **110 mobile tests passed**, followed by a successful `doctor`.
  Installed only the new `ai.riji-agent.mobile-public-proxy`, listening on loopback
  port 8767, and added HTTPS Funnel port 10000. Existing private/main listeners,
  IM bridge, Mem0, environment files, launch definitions and other Serve mappings
  were preserved. Fleet records the new listener as an observed production service.
- Public DNS resolved to a global relay address. The smoke test forced its socket
  to that address, with normal hostname/certificate verification and no inherited
  proxy. It verified authentication, blocked enrollment/non-mobile routes,
  capabilities, memory/privacy reads, an actual synthetic mentor reply, mentor
  isolation and immediate token revocation. No real memory was edited. The
  synthetic test credential was revoked and both temporary packages were removed.
- A fresh iPhone backup contains **41 files**. The signed app replaced the existing
  app without uninstalling; all 41 files had identical hashes before first launch.
  The private authorization package was transferred to Library/Caches with mode
  0600 while Riji was not running. iOS then rejected launch because the phone was
  locked; **no successful installation receipt has yet been observed**. The package
  remains pending. After unlock, verify receipt IDs and package consumption, revoke
  any unused issued device, then remove local/remote installation copies.

Physical-phone acceptance still needs first launch, foreground/cold-launch service
access, a mentor reply over mobile data with phone Tailscale off, and the user's
long-diary input-confirmation check. Computer public-HTTPS evidence is separate
from a successful carrier-network phone test. The diary viewport correction is
included in this installed build.

Final evidence is in the ignored local directory
`output/ios-managed-connection-2026-09-17/`: `python-final.log`,
`managed-native.xcresult`, `managed-final-ui.xcresult`, `phone-release-build.log`,
`air-mobile-tests.log`, `air-doctor.log`, `public-smoke-result.json`,
`air-source-integrity.json` and `phone-install-integrity.json`. Private packages,
phone backups and host details must not be copied into tracked records.

The independently deployable backend increment is saved as local commit `5b87e54`;
native pre-commit and commit-message privacy guards both passed. It was not pushed.
Pre-existing staged and other worktree changes were preserved. The iOS increment
remains with the broader uncommitted iOS work; the signed artifact and deployment
manifest identify the tested bytes, rather than claiming the entire app equals
that backend commit.

## Automated evidence

| Check | Result |
|---|---|
| Full backend suite, `PATH=/Library/Developer/CommandLineTools/usr/bin:$PATH PYTHONPATH=. uv run --no-sync pytest` | 2,036 passed, 5 optional skips |
| Final targeted backend, same environment with `tests/test_mobile_managed.py tests/test_mobile_proxy.py tests/test_mobile_composition.py tests/test_mobile_api.py` | 57 passed |
| Full package, `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test --package-path ios` | 444 passed: 214 State, 225 Core, 5 probe-support |
| Final mobile package regression, same environment with `--filter Mobile` | 89 passed: 54 State, 35 Core |
| Final managed-fixture helper compilation and lifecycle regression, `--filter MobileManagedLifecycleTests` | 7 passed |
| LaunchAgent template and whitespace checks | `plutil -lint` and `git diff --check` passed |

The full backend run preceded the final read-only-authentication-storage failure
repair and its two regression checks; all final targeted tests passed afterward.
The full Swift run preceded exclusive atomic publication of migration destinations;
the complete mobile subset passed after that repair. Independent native/UI/signing
results are owned by the root deployment task and recorded separately. No automated
check uses real credentials, the real Keychain, production memory or model calls.

Logs are retained under the ignored local directory
`output/ios-managed-connection-2026-09-17/implementation/`: `backend-full.log`,
`backend-targeted.log`, `swift-full.log` and `swift-mobile-final.log`.

## Failure matrix exercised

- Operator issuance verifies the existing owner; private/exclusive output rejects
  vault destinations and symlinks, stores only token hashes and revokes on failure.
- Public admission rejects missing/invalid/revoked authentication, enrollment and
  mixed-service routes before reading the request body or reaching upstream.
  Missing/locked authentication storage returns 503, preserving client credentials.
- Oversize requests, slow bodies, handler saturation, device/global rate limits,
  upstream deadlines and redirects fail within bounds; later valid work succeeds.
  Existing private route/header/body/response limits continue to pass.
- Fresh installation consumes once, restores on relaunch and fetches capabilities.
  Foregrounding retains navigation identity, drafts and pending IDs without replay.
- Existing same-origin credentials win; exact prior-origin binding migrates the
  same token/device state, retains the original archive and refuses conflicts or
  unreadable state. Identical migration destinations are accepted.
- Locked Keychain leaves the package retryable. Invalid JSON, excessive size,
  symlinks, incomplete credentials and noncanonical/non-HTTPS origins cannot activate.
- Failed consumption makes no credential change. Failed Keychain update after
  consumption retains the old record. Failed receipt writing cannot roll back a
  working connection or make its package importable again.
- Explicit disconnect and server revocation discard a rejected leftover package,
  so restarting cannot treat it as a fresh device. Offline service failure is
  distinct from missing installation and does not involve diary storage.

Earlier validation exposed a newline-origin acceptance and three legacy recovery
test failures caused by broadening missing-state semantics. The origin validator
now rejects whitespace, and stricter namespace checks apply to installation
migration while the existing recoverable local-draft behavior remains compatible.
The final runs above passed after these corrections.

The isolated native fixture uses a UUID-specific synthetic installation package,
fixture credential file and in-process transport. Its UI test is
`testMobileManagedInstallationConnectsWithoutLeavingFourPeerTabs`; it checks no
URL/code controls, automatic mentor/memory access and cold relaunch. Production
factory paths always use the device-only Keychain and real HTTPS transport.

Actual Air public ingress, USB delivery, the secret-free receipt, unused-device
revocation, physical-phone relaunch and the diary-to-memory journey remain separate
operator acceptance steps. A missing/error receipt requires inspection and a fresh
package; it must never be used as permission to replay a consumed credential.


## Review repairs — 2026-09-17

This addendum preserves the earlier record and documents the narrowly scoped
repairs requested by independent review. No full suite was rerun by the repair
agent; final complete verification remains owned by the root task.

- Migration validates the final sorted JSON encoding before package consumption:
  exactly 8,388,608 bytes succeeds, while the next byte fails without changing
  credentials or the original archive. Destination equality is rechecked at
  publication, including a destination created after planning; exclusive atomic
  publication remains in place.
- New installation processing invalidates the preceding receipt. Regression
  coverage starts with an old success, fails the current receipt write and proves
  that the old success disappears while the activated credential remains working.
  Operators must check package absence and that the current issuance device ID
  matches a receipt ID; a success string alone is insufficient.
- Consumption atomically claims the pathname before validating/removing the
  claimed file. Tests replace the USB file both before and after the claim:
  unexpected/newer bytes are preserved, credentials are unchanged, and a private
  recovery directory blocks subsequent automatic import until operator resolution.
  Transfer requires the app to be terminated; secrets remain in protected files.
- CLI issuance checks 2,048-byte origins and the 16 KiB serialized package budget
  before issuing a token. Public admission now bounds concurrency before offloaded
  SQLite lookup. Failed-auth and authorized minute quotas are separate; exhaustion
  by anonymous requests cannot spend the authorized budget. Storage lock failures
  remain 503 and do not block the event loop.
- Standard Uvicorn h11 is explicitly selected. Actual h11 protocol tests on an
  in-memory transport cover complete and incomplete excessive headers, unsupported
  methods/routes, transport-level plain-text 503 and later recovery. Applications
  reject excessive total headers before authentication and return no-store errors;
  pre-ASGI transport parsing errors do not guarantee application JSON/headers.
- The public proxy now has an integration test through ASGITransport to the real
  mobile router. It verifies exact mentor text, operation ID, accepted conversation
  effect and durable receipt, plus a real synthetic memory correction and receipt.

Commands and results:

| Targeted check | Result |
|---|---|
| PATH=/Library/Developer/CommandLineTools/usr/bin:$PATH PYTHONPATH=. uv run --no-sync pytest tests/test_mobile_managed.py tests/test_mobile_public_protocol.py tests/test_mobile_composition.py tests/test_mobile_proxy.py | 46 passed; two installed websockets deprecation warnings |
| DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test --package-path ios --filter 'Mobile(Installation\|Persistence\|ManagedLifecycle\|ClientModel)Tests' | 81 passed: 27 Core and 54 State |
| Final recovery-directory refinement: same Swift command with --filter 'Mobile(Installation\|ManagedLifecycle)Tests' | 23 passed: 16 Core and 7 State |

Logs are retained in the same ignored implementation directory as
review-backend-targeted.log, review-swift-targeted.log and
review-swift-claim-final.log. Deployment guidance now requires preserving/exporting
both conflicting rollback archives and resolving differences before retrying;
auto-overwrite and pending-operation replay remain prohibited.
