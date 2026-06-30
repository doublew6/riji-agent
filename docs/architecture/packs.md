# Journal Capability Packs

**Status**: foundation design for issue #1

riji-agent is the local-first journal core. Packs are optional product
capability bundles layered on top of that core. A pack can describe templates,
skills, automations, and optional data sources, but loading a pack is only
capability metadata: it must not read the user's vault, start an automation, or
grant a model any new filesystem access.

The first built-in pack is `personal-growth`. It maps the reusable ideas from
`doublew6/whit-riji-skills` and the riji-related schedules from
`doublew6/codex-automations` into a single product boundary.

## Why Packs

The external repositories contain useful journaling workflows, but they mix
several kinds of material:

- templates for daily, weekly, and monthly notes;
- skill definitions for review, weather, sleep, tasks, and focused work;
- scheduled automation prompts and deployment templates;
- personal-machine assumptions that must not become public defaults.

Packs give riji-agent a safer shape. They let the product say "this is a
personal growth journal bundle" without copying private paths, webhooks, browser
state, or machine-specific prompts into the runtime core.

## Boundary Rules

- Core remains the only owner of journal indexing, retrieval, draft state,
  writes, and audit metadata.
- Pack manifests are static metadata. They do not read the complete vault, raw
  Markdown files, SQLite databases, API keys, or webhook URLs.
- Any journal mutation introduced by a pack must still go through a draft
  preview or the controlled writer boundary.
- Automations installed from a pack must not bypass riji-agent's private-note
  exclusion, snippet limits, idempotency, or audit expectations.
- Optional data sources such as Apple Health, TickTick, Toggl, and weather
  providers must be configured explicitly.

## Built-In Pack: personal-growth

The initial manifest lives under `src/riji_agent/packs/personal_growth/` and
describes:

- templates: daily journal, weekly review, monthly review;
- skills: weekly review from daily notes, monthly review from weekly notes,
  daily weather, Apple Health sleep, TickTick task context, Toggl deep work;
- automations: daily note creation, previous weekly review, previous monthly
  review, daily weather fill;
- config expectations and privacy notes.

The manifest intentionally does not contain runnable automation prompts yet.
Future issues should add an installer that renders templates into the user's
chosen scheduler only after a dry run and privacy scan.

## Migration Shape

Suggested order:

1. Keep pack loading metadata-only.
2. Add template export or copy commands with dry-run support.
3. Add automation template installation with backup and privacy scan.
4. Convert direct prompt-based writes into calls to safe riji-agent workflows.
5. Add optional source connectors one at a time, each with failure-path tests.

This keeps `whit-riji-skills` and `codex-automations` useful as source material
while making riji-agent the coherent open-source product surface.
