# Journal memory development probes

These are synthetic development examples, not real diaries or a blind acceptance set.
The runner calls DeepSeek only when `--run-model` is supplied. It never scans the vault,
connects to Mem0, mutates production data or sends credentials in the report.

```sh
.venv/bin/python scripts/evaluate_journal_memory.py
.venv/bin/python scripts/evaluate_journal_memory.py --run-model --output /private/tmp/journal-probe.json
```

`cases.json` fixes extraction inputs and supplied old-memory neighbors. The latter
tests relation decisions, not semantic retrieval recall. All requests used
`deepseek-chat`; reports record requested model, output, client latency and request
characters. They do not report billing tokens, monetary cost or answer quality.

Three genuine runs on 2026-09-09 are retained under `results/`:

| Run | Finding |
| --- | --- |
| development-v1 | Protocol valid in 10/10 cases, but the unknown-date volunteer event was omitted. |
| development-v2 | After clarifying that an unknown date does not exclude an explicit event, the event was retained with a null date. Distinct interviews remained separate but missed a useful relation. |
| development-v3 | After clarifying related distinct events, all 10 development expectations were met on manual inspection by the implementing agent. 12624 request characters; 23.15 seconds aggregate client latency. |

The prompts were tuned on these examples, so these outcomes are not an unbiased
accuracy estimate. Hypotheses and empty content returned no committed personal fact;
conditions and exceptions were retained; duplicates, enrichment, state changes and
same-time conflicts were distinguished. The last case did not invent support for
details only present in an old fact.

The v3 extraction/relationship source SHA256 is
`d1fe6c760cd4eb4a5c9b4d5ec26f255970f941790c6dd3162b3311d787570720`
for `src/riji_agent/memory/journal_extract.py`. The source and corpus together define
this development revision; earlier runs predate the two described prompt clarifications.

Before a full release, prepare a separate labeled set with source evidence, expected
relations, frozen question dates and chronological/shuffled initialization. Keep the
same source coverage for the four PRD comparisons: existing retrieval/capture,
extraction-only, structured relation handling, and effective observation recall.
Measure omissions, unsupported claims, false merges, state handling, conflict handling,
evidence precision, context cost and latency. Establish thresholds from the baseline
before evaluating the untouched holdout. This four-pipeline benchmark and real
Mem0/BGE retrieval measurements have not yet been executed.
