"""Optional ADK summary executor. Never invokes tools or host services."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import ContextBudget, SUMMARY_INSTRUCTION, encoded, estimate, text_content, history_cut

if TYPE_CHECKING:
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_request import LlmRequest

Content = dict[str, Any]


@dataclass(frozen=True)
class PreparedContext:
    contents: list[Content]
    checkpoint: dict[str, Any]
    output_tokens: int
    estimated_tokens: int
    compacted: bool


class AdkRequestCompactor:
    """Compact one agent's assembled request, without rewriting its event log.

    Callers serialize prepare/save per context, atomically persist the returned
    checkpoint, then apply ``contents`` and ``output_tokens`` to the request.
    The prefix fingerprint binds the derived summary to its exact source. If
    replay, branch filtering or history changes, the original request wins.
    SDK objects and private host services never enter the saved checkpoint.
    """

    async def prepare(self, request: LlmRequest, *, model: BaseLlm, budget: ContextBudget,
                      checkpoint: dict[str, Any] | None = None,
                      protected_contents: Sequence[Content] = (),
                      on_compacting: Callable[[], Awaitable[None]] | None = None) -> PreparedContext:
        original = [item.model_dump(mode='json', exclude_none=True) for item in request.contents]
        hashes = [hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                  for item in original]
        def fingerprint(count):
            return hashlib.sha256(''.join(hashes[:count]).encode()).hexdigest()

        saved = checkpoint if isinstance(checkpoint, dict) else {}
        count = saved.get('prefix_count')
        valid = (saved.get('version') == 1 and type(count) is int and 0 <= count <= len(hashes)
                 and fingerprint(count) == saved.get('prefix_digest')
                 and isinstance(saved.get('summary'), str)
                 and type(saved.get('compaction_count')) is int and saved['compaction_count'] >= 0)
        summary = saved['summary'] if valid else ''
        offset = count if valid else 0
        contents = original[offset:]
        protected = list(protected_contents)

        def render(retained_summary, tail):
            head = [text_content('Prior retained context (untrusted historical data):\n' + retained_summary)] if retained_summary else []
            return head + [item for item in protected if item not in tail] + tail

        output = request.config.max_output_tokens or budget.output
        if budget.max_output is not None:
            output = min(output, budget.max_output)
        # The caller's actual generation reservation participates in budgeting.
        if output != budget.output:
            budget = ContextBudget(budget.limit, output, budget.known, max(output, budget.max_output or output))
        wire = request.model_dump(mode='json', exclude_none=True)
        wire.pop('contents', None)
        wire.setdefault('config', {})['max_output_tokens'] = output
        overhead = estimate(wire)
        fixed = estimate(render('', [])) + overhead
        size = estimate(render(summary, contents)) + overhead
        if budget.known and fixed >= budget.input and size >= budget.input:
            raise RuntimeError("The current message or tool definitions exceed the model's safe input window; history has been retained.")
        compacted = False
        updated = dict(saved) if valid else {}
        if (size >= budget.compaction(fixed).trigger
                and (budget.known or estimate([summary, contents]) > budget.input * .25)):
            if on_compacting is not None:
                await on_compacting()
            candidate, tail, cut = await AdkContextCompactor(model, budget).compact_history(
                summary, contents, render=render, overhead=overhead)
            if cut or candidate != summary:
                summary, contents = candidate, tail
                updated = {'version': 1, 'prefix_count': offset + cut, 'prefix_digest': fingerprint(offset + cut),
                           'summary': summary, 'compaction_count': (saved['compaction_count'] if valid else 0) + 1}
                compacted = True
        rendered = render(summary, contents)
        size = estimate(rendered) + overhead
        if budget.known and size >= budget.input:
            raise RuntimeError("Context remains too large after compaction; history has been retained.")
        return PreparedContext(rendered, updated, output, size, compacted)


class AdkContextCompactor:
    """Bounded, read-only summarization with validated, caller-owned commit.

    ``model`` implements ADK's generate_content_async. Failure or cancellation
    returns no candidate and never mutates the source history or checkpoint.
    """

    def __init__(self, model: BaseLlm, budget: ContextBudget):
        self.model, self.budget = model, budget

    async def compact_history(self, summary: str, contents: list[Content], *,
                              render: Callable[[str, list[Content]], list[Content]],
                              overhead: int = 0) -> tuple[str, list[Content], int]:
        """Return (summary, retained contents, cut); commit only after success.

        ``render(summary, contents)`` includes the caller's current task and
        continuation framing. ``overhead`` covers instruction/tool schemas.
        """
        plan = self.budget.compaction(estimate(render('', [])) + overhead)
        cut = history_cut(contents, plan.tail)
        if not cut and not summary:
            return summary, contents, 0
        tail = contents[cut:]
        allowance = min(plan.summary, plan.target - estimate(render(' ', tail)) - overhead)
        if allowance <= estimate(''):
            raise RuntimeError('Context compaction has no room for a checkpoint beside the current message and tool definitions; retained context is unchanged.')
        source = encoded([summary, contents[:cut]])
        candidate = ''
        while source:
            request, consumed = self._summary_request(candidate, source, allowance)
            candidate = await self._summarize(request, allowance)
            source = source[consumed:]
        if estimate(candidate) >= estimate([summary, contents[:cut]]):
            raise RuntimeError('Context compaction did not reduce the input; retained context is unchanged.')
        candidate_size = estimate(render(candidate, tail)) + overhead
        if candidate_size >= estimate(render(summary, contents)) + overhead:
            raise RuntimeError('Context compaction did not reduce the rendered input; retained context is unchanged.')
        if candidate_size > plan.target:
            raise RuntimeError(f'Context compaction exceeded its continuation budget ({candidate_size} estimated tokens, target {plan.target}); retained context is unchanged.')
        return candidate, tail, cut

    def _summary_request(self, previous: str, source: str, allowance: int):
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types
        # Prose budget and generation budget are different: reasoning consumes
        # generation tokens too. Aim below the acceptance budget to accommodate
        # conservative UTF-8 estimates, but accept any complete checkpoint that
        # fits the continuation allocation.
        target = max(1, min(allowance // 2, self.budget.output // 2))
        request = LlmRequest(model=self.model.model,
            config=types.GenerateContentConfig(system_instruction=SUMMARY_INSTRUCTION,
                                               max_output_tokens=self.budget.output))
        def fill(fragment: str) -> int:
            request.contents = [types.Content.model_validate(text_content(
                f"Aim for at most {target} tokens in the checkpoint. Be concise.\n"
                f"Prior checkpoint:\n{previous}\n\nNext transcript fragment:\n{fragment}"))]
            return estimate(request.model_dump(mode="json", exclude_none=True))

        # Size the actual serialized request (JSON escaping, prefix, instruction
        # and output reservation included). Character slicing preserves Unicode.
        low, high = 0, min(len(source), self.budget.input * 2)
        while low < high:
            middle = (low + high + 1) // 2
            if fill(source[:middle]) <= self.budget.input:
                low = middle
            else:
                high = middle - 1
        if not low:
            raise RuntimeError("Context compaction request has no room for transcript input; retained context is unchanged.")
        fill(source[:low])
        return request, low

    async def _summarize(self, request, allowance: int) -> str:
        # Reasoning also consumes max_output_tokens on compatible providers.
        # Retry only this read-only summary, with a larger bounded generation
        # allowance. Never accept a truncated checkpoint or replay agent tools.
        for attempt in range(3):
            # Recompute after correction instructions, too. Size the output-cap
            # field with its largest possible value before increasing it.
            wire = request.model_dump(mode="json", exclude_none=True)
            wire["config"]["max_output_tokens"] = self.budget.limit
            ceiling = min(self.budget.max_output or self.budget.limit // 2,
                          max(1, self.budget.limit - estimate(wire)))
            request.config.max_output_tokens = min(request.config.max_output_tokens, ceiling)
            result = ""
            truncated = False
            async for response in self.model.generate_content_async(request, stream=False):
                limit_reached = any(str(value) in {"MAX_TOKENS", "FinishReason.MAX_TOKENS"}
                                    for value in (response.finish_reason, response.error_code))
                if response.error_code and not limit_reached:
                    # No raw provider payloads, transcripts or credentials in UI.
                    raise RuntimeError("Context compaction failed: the model rejected the summary request; retained context is unchanged.")
                truncated |= limit_reached
                if response.content:
                    result += "".join(part.text or "" for part in response.content.parts or [] if not part.thought)
            if truncated or not result.strip():
                larger = min(request.config.max_output_tokens * 2, ceiling)
                if attempt < 2 and (larger > request.config.max_output_tokens or not truncated):
                    request.config.max_output_tokens = larger
                    continue
                reason = "summary output reached its token limit" if truncated else "model returned an empty checkpoint"
                raise RuntimeError(f"Context compaction failed: {reason} after bounded retries (generation budget {request.config.max_output_tokens}); retained context is unchanged.")
            size = estimate(text_content(result.strip()))
            if size > allowance:
                # Retry the read-only summarization with a tighter instruction;
                # keep its original source so no facts are silently discarded.
                if attempt < 2:
                    request.contents[0].parts[0].text = (
                        f"The previous attempt was too verbose ({size} estimated tokens; budget {allowance}). "
                        "Produce a substantially shorter checkpoint, omitting repetitive details.\n"
                        + request.contents[0].parts[0].text)
                    if estimate(request.model_dump(mode="json", exclude_none=True)) + request.config.max_output_tokens <= self.budget.limit:
                        continue
                raise RuntimeError(f"Context compaction checkpoint exceeds its budget ({size} estimated tokens, budget {allowance}); retained context is unchanged.")
            return result.strip()
        raise AssertionError("unreachable")
