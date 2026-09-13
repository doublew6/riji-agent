# iOS implementation status

The user authorized continued implementation and Mac manual acceptance on 2026-09-13. The full scope remains in `docs/PRD.md`; the increments below organize delivery rather than defer requirements indefinitely.

| Increment | Scope | Status | Evidence |
|---|---|---|---|
| Durable daily diary | Today-first capture, atomic Markdown append, recovery, directory, export | Implemented | Commit `9f3b3d0`; 40 Swift and 5 Simulator UI tests |
| Daily workflows | Template import, explicit new date, search, North Star, MIT/feedback, manual weekly/monthly reviews, Mac launcher | Implemented | `spec-ios-daily-workflows.md`; 79 Swift, 7 Simulator UI flows and 9 launcher tests |
| Mobile service and AI | Isolated account/device sessions, selected sync and receipts, native mentors/memory, real provider integration | Pending | Existing mentor/memory services can be reused; no product mobile gateway exists yet |
| Audio and complete acceptance | Recording recovery/playback, on-device Chinese transcription, structured export/restore, automation controls and end-to-end acceptance | Pending | Must distinguish Simulator tests from physical-device evidence |

The existing Obsidian configuration identified the user's daily template outside this repository. Its formatted-date placeholders and styled Notes heading still require compatibility work; its private path and contents are not tracked. Weekly/monthly templates, North Star definitions and actual automation settings have not yet been located. Generic template import is available, but personal migration still requires raw preview, explicit confirmation and a field comparison. Public TLS access, product account provisioning and physical-device acceptance remain deployment inputs; synthetic fixtures cannot establish those capabilities. A local provider credential presence check does not establish mobile AI connectivity.

Production on Air and the existing Feishu/Hermes/Memory interfaces stay independent of native development. No new mobile feature is enabled in the old production application by default.

Verification on the development Mac, 2026-09-13: after the native workflow fixes, 79 Swift Core/State tests, a generic Simulator build, 7 actual Simulator UI flows and 9 launcher tests passed. The Python regression suite also passed (1,934 passed, 5 skipped). These results establish local native behavior and existing backend regression coverage, not mobile AI connectivity or production deployment.
