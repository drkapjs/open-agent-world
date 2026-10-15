# Context compaction for runtime plugins

Plugin API 1.32 provides **opt-in** context budgeting and summarization. It does
not wrap plugin execution, create cards, or replace a plugin's conversation
store. Providers with native context management can leave it unused.

## Budget policy

Import `ContextBudget`, `CompactionBudget`, `estimate` and `history_cut` from
`open_agent_world.context` (also available through `plugin_api.context`). This
package imports no backend services or model SDKs. `ContextBudget.for_model()`
is an optional LiteLLM metadata fallback; configured model limits take priority.
`AgentCapabilityProvider.resolve_model()` returns optional `context_window` and
`max_output_tokens` on `RuntimeModelConnection`. They belong to the selected
connection, not a fuzzy match on a model name.

The policy reserves output and safety headroom, subtracts fixed instructions,
the current task and tool schemas, then triggers at 85% of the remaining history
budget. A successful compaction targets 50%, with up to 20% for the checkpoint
and 30% for recent history. These percentages are **not** fractions of the raw
model context window. The UTF-8 estimate is conservative, not an exact tokenizer
or a claim about an unknown model's capacity.

## Optional ADK adapter

`open_agent_world.context.adk.AdkRequestCompactor` prepares one assembled model
request. It uses the same bounded summary executor as OAW's managed ADK Agent.
The summarizer receives no tools. It chunks oversized history, bounds retries,
rejects empty/truncated/oversized summaries, and validates the entire rendered
continuation before returning a candidate. Failure and cancellation leave the
input request and previous checkpoint unchanged.

```python
from google.genai import types
from open_agent_world.context import ContextBudget
from open_agent_world.context.adk import AdkRequestCompactor

# Inside the plugin's before-model callback, after assembling current inputs:
async with context_lock(context_key):
    saved = load_checkpoint(context_key)
    prepared = await AdkRequestCompactor().prepare(
        llm_request,
        model=agent.canonical_model,
        budget=ContextBudget(window, output, max_output=output),
        checkpoint=saved,
        protected_contents=[current_task],  # Serialized ADK Content dictionaries.
    )
    save_checkpoint_atomically(context_key, prepared.checkpoint)
    llm_request.contents = [types.Content.model_validate(c) for c in prepared.contents]
    llm_request.config.max_output_tokens = prepared.output_tokens
```

`context_lock`, persistence and context identity above are supplied by the
plugin. Serialize prepare/save/apply for each context, or use revision-checked
commits in a multi-process store. Protect new user constraints and the current
task; complete call/result groups remain together, and unfinished exchanges are
never folded. Do not run independent summarization policies over the same view.

The returned checkpoint contains a summary and fingerprints of its source
prefix. On subsequent calls the adapter substitutes it only if the assembled
history still has that exact prefix. Changed or filtered histories invalidate
the derived checkpoint and use the original request. Persist canonical events
separately: compression must not delete or rewrite them. Large event logs still
need their own storage/retention policy; this helper bounds model context, not
archive size. This adapter currently estimates request size rather than using
provider-reported token calibration.

## Isolation and completion

Use the actual context instance as the key: OAW Agent ID, conversation/private
session ID, and (for internal agents) role, branch and child-session identity.
An Agent name alone does not identify parallel or recursive instances. Never
share a mutable checkpoint across those instances. Explicit input/output
handoffs remain the only mechanism for sharing task information.

MatCreator 0.8 uses this policy for the planner, execution orchestrator and
recursive executors. Each worker is already scoped to an OAW Agent and session;
its private `contexts/` files additionally separate native sessions, branches
and roles. No post-turn, every-three-invocations summary runs. Existing native
events and earlier summaries remain readable without migration. Only reaching
the request budget triggers a new summary before the next model call.

A terminal Run event records a result. Agent capacity remains occupied until
the provider stream closes. Cleanup failure blocks replacement execution until
cleanup is resolved; `stop()` must be idempotent. Host scheduling uses
`wait_settled()` when it needs both a terminal result and provider exit, then
rechecks admission. UI text delivery and stream closure need not coincide.
