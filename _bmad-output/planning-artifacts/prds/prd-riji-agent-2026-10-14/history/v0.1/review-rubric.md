# PRD Quality Review — 导师自然交互与话题连续性

Reviewed: 2026-10-14. Scope: v0.1 personal-product discussion draft, not an implementation gate. No application changes or production actions were performed.

## Overall verdict

This is a useful discussion draft: it answers the user's three concerns, keeps the approved warm cosmic visual direction, and names the important change from a fixed full-table audience to selected recipients. It should remain a draft and can be discussed now. Before approval for implementation, clarify how member changes create or resume an exchange segment, where the unsent text belongs during those changes, and how a failed first send remains recoverable.

## Decision-readiness — strong

Sections 2 and 8 put the real choices in front of the owner: topic organization, pointer/touch selection, and participant visibility including the host. A1–A5 are explicitly proposals; the document does not turn the latest feedback into permission to build. The distinction between pointer feedback and actual sending is especially useful because the original phrase could mean either hover preview or automatic switching.

No decision needs to be forced merely to complete this draft. The unified-topic proposal is a meaningful option, although its alternative deserves a less dismissive comparison: separate private and group topics simplify context and access boundaries even if moving between them takes an extra step.

## Substance over theater — strong

The problem statement is grounded in the current UI: unselected stage portraits use reduced opacity and the stage does not accept interaction. The proposed remedies address those facts rather than introducing unrelated controls, personas, growth metrics, or architectural prescriptions. Synthetic scenarios are correctly labeled, and the draft does not claim new interviews or completed usability results.

## Strategic coherence — strong

The thesis, “以话题保留事情，以导师决定当下和谁交流,” connects selection, input, history, and return visits. FR-01 preserves clear faces without brightening the entire background; FR-04 protects reading and input space; FR-09–14 make continuity an explicit outcome. The acceptance criteria measure successful selection, understandable recipients, and retained context rather than stronger visual effects or more usage.

## Done-ness clarity — adequate

Most requirements already have observable consequences. The remaining gaps concern transitions that a user will encounter while changing their mind, rather than missing section headings or numerical targets.

### Findings

- **medium** Exchange-segment identity is not yet consistent across participant changes (§2 glossary; §4 selection table; FR-07/08) — An exchange segment has “确定对象,” but the draft also allows adding and removing recipients while referring to “该交流段.” It is unclear whether A → A+B → A resumes the first private segment, creates another private segment, or changes the audience of one segment. This affects what returning users see and what each mentor may use. *Fix:* Add a short proposed transition example: messages keep their original audience, recipient changes start or explicitly resume an appropriate segment, and returning to a private mentor never imports the intervening group history automatically. Mark the exact resume-versus-new choice as open if needed.

- **medium** Pending member selection needs a clear draft owner (§4 selection table; FR-09) — Switching from one mentor to another explicitly leaves the old draft behind, but adding a mentor, removing the final mentor, and changing selection while a reply is running do not specify which segment owns the unsent text. “准备下一次成员选择” also needs to remain visibly distinct from the active request. *Fix:* State a proposed rule for unsent text when the recipient set changes, including a way to return to the prior draft; show current reply recipients separately from the next send's recipients. Add one acceptance example with typed text, add/remove, cancel, and an arriving reply. This can stay a discussion item rather than a detailed state machine.

- **medium** First-send failure has no explicit topic-recovery outcome (FR-03; FR-11/12) — A topic starts after the first message is “成功保留,” while an unsent new topic can be canceled without leaving a record. The meaning of success is ambiguous if the first send is pending or its receipt is unknown. FR-09 generally protects content, but the user still needs somewhere identifiable to return to that first request. *Fix:* Say that a failed or unknown first send remains available as a clearly labeled pending conversation/topic with its text and original recipients; it is never silently removed as an empty topic or resent as a new request. Clarify that title generation does not wait for an AI response.

## Scope honesty — strong

The draft keeps implementation unauthorized, does not promise phone/Feishu history synchronization, and explicitly distinguishes the existing full-table visibility rule from A5. This is particularly important: removing the invitation form must not silently relabel a fixed audience as selected recipients. The host is included in recipient disclosure when it actually reads materials, and old private history is not automatically shared.

The statement that there will be no portrait redraw is a sensible current boundary, but it should not preclude discussing a later asset adjustment if lighting and state changes still fail the user's warmth/readability review. That is a future product choice, not a missing requirement now.

## Downstream usability — adequate

The main concepts and contiguous FR/NFR identifiers can support later UX work. The old-baseline replacement list is explicit and should be preserved when the candidate becomes approved. There is no need to add technical models or issue decomposition before the user answers the three principal questions.

### Findings

- **low** “一次讨论” is broader than the existing run term (§2 glossary; FR-06) — The glossary currently calls any mentor response to one send a discussion, whereas the existing product distinguishes a single reply from a bounded multi-mentor reference/debate run. This may blur whether each follow-up triggers the whole table. *Fix:* Define “一次回复” separately, or restrict “一次讨论” to the explicitly requested multi-mentor run; identify group follow-up behavior as a proposal if it is intended to change.

## Shape fit — strong

The length and structure suit a single-owner discussion: three concrete scenarios, a small glossary, practical behaviors, and a short decision list. More personas, quantitative business metrics, a full technical state machine, or preemptively resolving every open item would add weight without improving this conversation. The document correctly treats the owner's actual reading and touch experience as separate from automated verification.

## Mechanical notes

- A1–A5 all appear in the closing discussion list, grouped into three owner decisions. FR-01–14 and NFR-01–02 are contiguous.
- The visual-document links resolve relative to this planning directory. The current implementation evidence supports the introductory claims about dimmed, non-interactive stage portraits.
- FR-02 appropriately limits hover behavior to supported pointer surfaces, and A2 gives a complete touch alternative. A later implementation plan should verify actual pointer-event support in the target Simulator/OS rather than assuming that a Mac cursor automatically grants an iPhone screen native hover behavior; this does not block discussing the desired feedback now.
- “归档只整理列表” proposes a lighter meaning than the earlier roundtable baseline, where archived conversations stop accepting ordinary continuation until restored. If that change is intentional, include it in the eventual old-to-new replacement list; otherwise retain the requirement to explicitly resume an archived topic.
- Local retention, Air history, and temporary device caches should be distinguished in later architecture work. “默认长期保留” must not imply every record is permanently available offline, and NFR-02 already correctly limits offline reading to cached content.
