# Backend baseline before native iOS development

Date: 2026-09-12. This records a completed local merge and Air service restart, not an iOS feature release.

## Revisions and scope

- Existing local work was committed as `f99acb7aeea7d7bf8cb5d045d957c798438c16c6`: 329 changed files, including memory and mentor work, reliability tests, deployment documentation and the iOS product design.
- The integration commit on local `main` is `767b2b8c930f4656047449bb57ac35384550537d`.
- The previous local `main` had no common ancestor with the current remote main. It was preserved as `codex/main-before-ios-20260912`; no history was deleted or force-pushed.
- The remote README updates were retained. The six implementation merge conflicts were resolved only after confirming that the remote versions exactly matched the development branch's starting baseline. The merged runtime source matches the tested implementation.
- Native iOS work starts on `codex/ios-local-diary` from the integration commit. After the user's explicit authorization, the integration commit was successfully pushed to the existing public repository's `main` branch.

## Validation

| Check | Result |
| --- | --- |
| Local full Python suite | 1925 passed, 5 skipped |
| Synthetic credential fixture changes | 269 passed |
| Merge-related diary and documentation checks | 75 passed |
| Actual staged blobs and outgoing commit range | Personal publication guard passed |
| Air current release manifest, 117 test files | 1920 passed, 10 skipped |
| Air deployed file hashes | All 455 release files matched |
| Protected runtime/source configuration and service definitions | Unchanged |
| GitHub main CI on macOS and Ubuntu | Passed |
| GitHub main CI on Windows | Failed during collection: unconditional POSIX `fcntl` imports and a default-encoding fixture read |

The Windows result is an outstanding compatibility issue in the integrated Python baseline, not an iOS build result. See [CI run 34702833743](https://github.com/doublew6/riji-agent/actions/runs/34702833743). The native iOS slice does not change this Python baseline or claim Windows validation.

The unrestricted Air directory test discovery also found four historical test files outside this release manifest. That preliminary run produced 12 failures involving older image-diary and Web UI interfaces. Those files were preserved, not deleted or silently rewritten. Verification against the backed-up source established that this release changed no existing production source contents. The successful Air result above is specifically the complete test manifest committed in this release, not a claim that every historical file in the Air directory passes.

## Air deployment and recovery

The target was verified through the existing SSH alias as a MacBook Air. Fleet confirmed the Air deployment host and an online Agent. The API process, working directory, loopback listeners and Mem0 Compose labels were checked together.

The fixed integration revision was archived and compared with rsync dry-run. Only the committed snapshot was synchronized, excluding credentials, runtime data and generated BMAD files. No deletion option was used. The verified Air-local backup `20260912-main-767b2b8-before-ios` contains 410 existing files, a manifest of 45 newly introduced files and the protected service/configuration files. It remains under the existing runtime deployment-backup directory.

No runtime dependency, production secret, database schema, memory provider or container configuration changed. After the release-manifest tests and doctor passed, only `ai.riji-agent` was restarted through the production runtime executable. The API health endpoint, service status, doctor, Mem0 API/Dashboard, memory snapshot and bridge installation checks succeeded. Fleet observed the restarted API running. The four fixed-mentor receiver processes and the existing Hermes gateway retained their process identities; the shared container runtime and other projects were not restarted.

This verification did not send a new real Feishu conversation, confirm a real diary write or test an actual model response. Those user-facing paths retain their previous deployment evidence; service health and synthetic regressions are the evidence from this maintenance run.

## iOS separation

The native client will live in `ios/`, use its own sandbox and local test fixtures, and require no running backend for its first local-capture slice. Mobile authentication, synchronization and AI integration require their own explicit contracts and validation. Local app development must not mutate the Air vault, production configuration or existing channel permissions.
