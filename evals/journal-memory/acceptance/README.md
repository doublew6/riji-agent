# Synthetic acceptance preparation

This is a new, assistant-authored synthetic corpus, separate from the ten development
examples used to tune extraction prompts. No diary or memory was read to create it.
The proposed checks are review guidance, **not user labels or an independently
validated ground truth**. The corpus is visible to the implementer, so it is not a
blind holdout. No real model run has been performed on these new cases.

Each case fixes its source type, recording date and future question date, with
supplied old-memory neighbors and proposed checks. The current runner uses only
extraction and relationship inputs. Question dates reserve a future answer-evaluation
contract; they do not mean answers, real Mem0/BGE retrieval or four pipelines are tested.

```sh
# Plan only: no settings, credentials, provider construction or network access.
.venv/bin/python scripts/evaluate_journal_memory.py --suite acceptance --provider codex

# Explicit cloud execution on the authorized runtime, using synthetic inputs only.
.venv/bin/python scripts/evaluate_journal_memory.py --suite acceptance --provider codex --run-model --output /private/tmp/journal-acceptance.json
```

Codex uses the existing memory adapter and `RIJI_MEMORY_CODEX_MODEL`, with the configured
isolated home, login, timeout and optional child proxy. It does not copy credentials,
create a production data directory, start services or connect to Mem0. Configure/login
that existing runtime separately. DeepSeek retains `deepseek-chat` for compatibility.
Provider selection is explicit and never falls back automatically. The default command
still plans the original development suite; `--run-model` without `--provider` still
selects DeepSeek. `--case` accepts only identifiers in the selected bundled suite.

Reports preserve legacy `requested_model`, `corpus`, `results`, `request_chars` and
`valid_contract` fields, adding provider identity, corpus hash and per-stage timing.
The requested model comes from the constructed provider; the adapters do not expose
the server's returned model identity. Guarded send attempts and application-message
characters are not proof of remote delivery, billing tokens or cost. A provider using
business JSON Schema also counts that transmitted schema; the legacy path does not
send or count it. Codex's transport envelope is excluded. Successful protocol validation leaves semantic review unreviewed.
Failures return safe categories, retain partial reports and produce a nonzero exit.
Outputs are atomic mode-0600 JSON files outside the journal root; symlinks and overwriting
the bundled source corpora are rejected. Model-generated synthetic text still requires
review before publication.

For independent acceptance, a human reviewer should review proposed expectations,
record rubric/version and question dates, and freeze thresholds using a separate
baseline before evaluating an untouched holdout. Use `review-template.json` as a local
copy for annotations; leave unavailable judgments null rather than counting them as passes.
Future real-neighbor/answer runners must report their own dataset, pipeline, source
coverage, evidence IDs, omission/false-merge metrics and latency. They must not relabel
this supplied-neighbor contract probe as an end-to-end benchmark.
