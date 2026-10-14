"""Opt-in public policy, source binding and independently owned contexts."""
import asyncio
import copy
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from open_agent_world.context import ContextBudget, estimate, text_content, history_cut
from open_agent_world.context.adk import AdkRequestCompactor
from backend.tests.test_context_compaction import ScriptedModel
from plugins.matcreator.oaw_matcreator.native_context import NativeContexts


def request(history):
    return LlmRequest(model='openai/context-test', contents=[types.Content.model_validate(item) for item in history],
                      config=types.GenerateContentConfig(system_instruction='Keep the user constraints.'))


def large_history(label='cobalt-731'):
    return [text_content(f'{label} old observation {i}: ' + 'evidence ' * 100) for i in range(35)]


def test_public_budget_import_does_not_load_host_or_sdk():
    script = (f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r}); "
              "from open_agent_world.context import ContextBudget; "
              "assert ContextBudget(8192, 1024).input > 0; "
              "assert not any(k.startswith(('backend', 'google', 'litellm')) for k in sys.modules)")
    subprocess.run([sys.executable, '-I', '-S', '-c', script], check=True, capture_output=True)


@pytest.mark.asyncio
async def test_small_context_never_compacts_by_turn_count():
    model = ScriptedModel()
    history = []
    for i in range(9):
        history.append(text_content(f'User turn {i}'))
        prepared = await AdkRequestCompactor().prepare(request(history), model=model, budget=ContextBudget(8192, 1024))
        assert not prepared.compacted and prepared.contents == history
    assert model._summaries == []


@pytest.mark.asyncio
async def test_checkpoint_reuses_only_exact_prefix_and_keeps_original_history():
    model = ScriptedModel()
    history = large_history()
    current = text_content('Continue; do not run external jobs without approval.')
    wire = request(history + [current])
    original = wire.model_dump()
    compactor = AdkRequestCompactor()
    first = await compactor.prepare(wire, model=model, budget=ContextBudget(8192, 1024), protected_contents=[current])
    assert first.compacted and first.checkpoint['prefix_count'] > 0
    assert current in first.contents
    assert wire.model_dump() == original
    count = len(model._summaries)
    second = await compactor.prepare(request(history + [current, text_content('next')]), model=model,
        budget=ContextBudget(8192, 1024), checkpoint=first.checkpoint, protected_contents=[current])
    assert not second.compacted and len(model._summaries) == count
    # A different branch/session cannot inherit a summary for unrelated events.
    other = [text_content('different branch')]
    third = await compactor.prepare(request(other), model=model, budget=ContextBudget(8192, 1024), checkpoint=first.checkpoint)
    assert third.contents == other and third.checkpoint == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_failed_or_cancelled_summary_does_not_mutate_request_or_checkpoint(cancel):
    model = ScriptedModel()
    model._failure = not cancel
    if cancel:
        model._block = asyncio.Event()
    wire = request(large_history())
    saved = {}
    before_request, before_saved = wire.model_dump(), copy.deepcopy(saved)
    entered = asyncio.Event()
    async def compacting():
        entered.set()
    task = asyncio.create_task(AdkRequestCompactor().prepare(wire, model=model,
        budget=ContextBudget(8192, 1024), checkpoint=saved, on_compacting=compacting))
    await entered.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await task
    assert wire.model_dump() == before_request and saved == before_saved


def test_history_cut_preserves_unfinished_exchange_and_completed_pairs():
    call = {'role': 'model', 'parts': [{'function_call': {'name': 'inspect', 'id': 'a', 'args': {}}}]}
    response = {'role': 'user', 'parts': [{'function_response': {'name': 'inspect', 'id': 'a', 'response': {'ok': True}}}]}
    history = [text_content('old'), call]
    assert history_cut(history, 0) == 1
    assert history_cut(history + [response], estimate(response)) == 3


def callback(model, session='native', agent='thinking_agent'):
    return SimpleNamespace(_invocation_context=SimpleNamespace(
        session=SimpleNamespace(app_name='MatCreator', user_id='oaw', id=session), branch=None,
        agent=SimpleNamespace(name=agent, canonical_model=model),
        user_content=types.Content.model_validate(text_content('Continue'))))


@pytest.mark.asyncio
async def test_native_contexts_are_isolated_and_checkpoint_survives_worker_restart(tmp_path):
    connection = dict(model='context-test', adapter='openai', context_window=8192, max_output_tokens=1024)
    contexts = NativeContexts(tmp_path, connection, lambda _: None)
    slow, quick = ScriptedModel(), ScriptedModel()
    slow._block = asyncio.Event()
    emitted = asyncio.Event()
    contexts.emit = lambda _: emitted.set()
    blocked = asyncio.create_task(contexts.before_model(callback(slow, 'child-a', 'step_executor'), request(large_history())))
    try:
        await emitted.wait()
        # Same agent name, different child context: it must not wait for child-a.
        small = request([text_content('Only child-b facts')])
        await asyncio.wait_for(contexts.before_model(callback(quick, 'child-b', 'step_executor'), small), 2)
        assert not quick._summaries
        assert 'cobalt-731' not in small.model_dump_json()
        assert not blocked.done()
        slow._block.set()
        await blocked
        files = list((tmp_path / 'contexts').glob('*.json'))
        assert len(files) == 1
        assert json.loads(files[0].read_text(encoding='utf8'))['compaction_count'] == 1
        restarted = NativeContexts(tmp_path, connection, lambda _: None)
        count = len(quick._summaries)
        replay = request(large_history())
        await restarted.before_model(callback(quick, 'child-a', 'step_executor'), replay)
        assert len(quick._summaries) == count
        assert 'Prior retained context' in replay.model_dump_json()
        # A different role in the same ADK session must not reuse that checkpoint.
        planner = request([text_content('Planner only')])
        await restarted.before_model(callback(quick, 'child-a', 'thinking_agent'), planner)
        assert 'Prior retained context' not in planner.model_dump_json()
    finally:
        slow._block.set()
        await asyncio.gather(blocked, return_exceptions=True)
