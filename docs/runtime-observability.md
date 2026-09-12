# Private Agent runtime observability

`riji-agent` can emit one real execution Trace for each `AgentRunner.run` call.
The root Input is the current question and the root Output is the final Agent
answer. Each Agent round is a parent span containing its real
`provider.complete` LLM span and any `ToolRegistry.invoke` tool spans.

Tracing is disabled unless `RIJI_RUNTIME_TRACE_POLICY_PATH` points to a private
EvalMesh policy. The policy and its JSONL destination must be absolute paths
outside every Git worktree. EvalMesh rejects symlinks, hardlinks, permissive
file modes, unknown fields, unsafe remote endpoints, and in-repository paths.

## Install and configure

Install the pinned EvalMesh contract and its Opik extra into the same
environment as `riji-agent`:

```bash
uv sync --extra runtime-tracing
```

Create the private directory and policy with an owner-only umask. The policy
must contain the EvalMesh runtime-tracing schema fields below, but the endpoint,
output path, credentials, and redaction values must be filled only in the
untracked mode-`0600` file. Never copy their real values into documentation,
Git configuration, a fixture, or a command argument:

```json
{
  "schema_version": 1,
  "endpoint": "SET_ONLY_IN_THE_PRIVATE_POLICY",
  "workspace": "default",
  "project_name": "riji-agent",
  "output_path": "SET_ONLY_IN_THE_PRIVATE_POLICY",
  "capture_prompt": true,
  "capture_output": true,
  "capture_tool_io": true,
  "redact_values": []
}
```

Set the real absolute policy path only in the untracked local `.env` or service
manager; the tracked example intentionally leaves the value blank:

```text
RIJI_RUNTIME_TRACE_POLICY_PATH=
```

The three capture flags are explicit privacy consent. When enabled, real
questions, model messages, answers, tool arguments, and bounded tool results
are visible in the configured private Opik project. EvalMesh recursively
removes secret/environment/path fields, replaces configured sensitive values
and absolute host paths, limits nesting/item counts/string size, persists the
sanitized projection to private JSONL first, and then delivers it to Opik.

On the Air host, the policy must use the canonical Air-local loopback Opik
endpoint and keep `allow_remote` absent or false. On every non-Air host, use
only the approved tailnet-only HTTPS endpoint and set `allow_remote` to true.
Never use a cleartext tailnet IP endpoint. Put the concrete endpoint, output
path, endpoint credential, and extra `redact_values` only in the mode-`0600`
private policy, never in Git.

## Verify a real request

Start or reinstall the service from the tracing-enabled environment (for a
foreground run, use `uv run --extra runtime-tracing riji-agent`). Then send an
allowlisted private-chat request that causes the model to call a journal tool.
In the Opik project `riji-agent`, verify that the resulting execution Trace has:

- non-empty root Input and Output;
- one LLM span for every actual `provider.complete` call;
- one tool span for every actual `ToolRegistry.invoke` call;
- tool success/failure, bounded arguments/results, and duration;
- round parent IDs that contain the corresponding LLM and tool spans.

The service log reports only the opaque local/runtime and Opik Trace IDs plus
delivery status. It never logs the policy path or captured content.
