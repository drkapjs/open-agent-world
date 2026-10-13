"""History semantics against the Pack's actual engine and dependency runtime."""
import os
from pathlib import Path
import subprocess

import pytest

from plugins.matcreator.oaw_matcreator import native_environment


@pytest.mark.skipif(not os.environ.get("OAW_MATCREATOR_MANAGED"), reason="requires Pack orchestration dependencies")
def test_persisted_history_attempts_scopes_data_and_resource_evidence(tmp_path):
    executable, dependencies = native_environment.read_environment()
    adapter = Path(native_environment.__file__).parent
    script = r'''
import asyncio, json, os, sys
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
root = Path(sys.argv[3])
os.environ.update(MATCREATOR_HOME=str(root/'home'), MATCLAW_WORKSPACE=str(root/'workspace'),
    LLM_MODEL='openai/test', LLM_API_KEY='private-test', LLM_BASE_URL='http://127.0.0.1:1')
sys.path.insert(0, sys.argv[1])
from native_environment import activate, activate_engine
activate(Path(sys.argv[2])); activate_engine()
from oaw_matcreator_engine.resources import configure, bridge, execution_context, read_artifact_input
async def rpc(method, **arguments):
    if method == 'list_tools':
        return [{'name':'inspect_artifacts','capability_id':'read'}]
    name = 'publish_artifact' if arguments['capability_id']=='publish' else 'inspect_artifacts'
    return {'ok':True,'tool':name,'result':[{'type':'inputText','text':json.dumps({'state':'ready','version_id':'v1'})}]}
configure(rpc, lambda *args, **kwargs: None)
from oaw_matcreator_engine.agents.graph_logger import AgentGraphLogger
from oaw_matcreator_engine.agents.consumed_inputs import validate_input_references
from oaw_matcreator_engine.agents.thinking_agent.planning import validate_graph, get_ready_nodes
from oaw_matcreator_engine.agents.execution_graph_state import get_execution_graph, set_execution_graph
from oaw_matcreator_engine.agents.execution_agent.step_executor_runner import _graph_dependency_step_ids
from oaw_matcreator_engine.agents.execution_agent.recovery import start_node_attempt, finish_node_attempt, reconcile_recovery_state
from oaw_matcreator_engine.agents.thinking_agent.history_tools import read_execution_history
from native_adapter import read_agent_history, read_agent_graph

h = AgentGraphLogger('native')
h.log_node_start('orchestrator','orchestrator','Orchestrator')
for index in range(1,21):
    h.log_node_start(f'e{index}','execution',f'Execution {index}','orchestrator',plan_id=f'p{index}',execution_id=f'e{index}')
assert h._read()['edges']==[], 'history order and containment are not dependencies'
for index in (1,9,17):
    h.log_node_start(f's{index}','step','Produce',f'e{index}',plan_id=f'p{index}',execution_id=f'e{index}',logical_node_id='reused')
    h.log_result(f's{index}', {'key_results':f'value {index}'})
    h.log_node_complete(f's{index}','success',summary=f'value {index}')
h.log_node_start('target','step','Consume','e20',plan_id='p20',execution_id='e20',logical_node_id='consume')
for source in ('s1','s9','s17'):
    h.log_data_delivery(source,'target')
assert {(edge['from'],edge['to']) for edge in h._read()['edges']} == {(s,'target') for s in ('s1','s9','s17')}
assert h.find_step_node('reused','p1')=='s1'
assert h.find_step_node('reused','unrelated') is None
assert h.find_step_node('reused') is None
h.log_task_fork('orchestrator','s1')
assert any(e['kind']=='fork' and e['from']=='orchestrator' and e['to']=='s1' for e in h._read()['edges'])
assert not any(e['kind']=='merge' for e in h._read()['edges']), 'success is not acceptance'
h.log_node_start('nested','step','Child','target',plan_id='p1',execution_id='e20',logical_node_id='reused')
assert h.find_step_node('reused','p1')=='s1', 'nested logical ID cannot shadow a scheduled predecessor'

h.log_node_start('fail','step','Attempt','e20',plan_id='p20',logical_node_id='retry')
h.log_node_complete('fail','failed',summary='original failure')
h.log_node_start('fail','step','Attempt','e20')
assert h.node('fail')['summary']=='original failure'
h.log_node_start('retry','step','Attempt','e20',plan_id='p20',logical_node_id='retry',retry_of='fail')
assert h.node('retry')['attempt']==2
assert any(e['kind']=='retry' and e['from']=='fail' and e['to']=='retry' for e in h._read()['edges'])
h.log_node_start('replanning','planning','Planning','orchestrator',replan_of='e20')
assert any(e['kind']=='replan' for e in h._read()['edges'])

from oaw_matcreator_engine.agents.orchestrator.agent import _stream_planning_with_recovery
class BrokenPlanner:
    calls=0
    async def run_async(self, ctx):
        self.calls+=1
        if self.calls==1: raise json.JSONDecodeError('test invalid model arguments','{',0)
        yield 'recovered'
retryctx=SimpleNamespace(session=SimpleNamespace(id='native',state={'_graph_planning_node_id':'replanning'}))
async def planning_retry():
    return [value async for value in _stream_planning_with_recovery(BrokenPlanner(),retryctx,max_attempts=2)]
assert asyncio.run(planning_retry())==['recovered']
replacement=retryctx.session.state['_graph_planning_node_id']
assert replacement!='replanning' and h.node('replanning')['status']=='failed'
assert h.node(replacement)['retry_of']=='replanning'

# Published immutable versions have observed producers. Inspection records
# access evidence, not an unsupported claim that scientific computation used it.
async def resources():
    with execution_context('native','s1'):
        await bridge().oaw_invoke_tool('publish', {'collection':'c'})
    with execution_context('native','target'):
        assert (await read_artifact_input('oaw:artifact:c:v1'))['version_id']=='v1'
asyncio.run(resources())
edge = next(e for e in h._read()['edges'] if e.get('artifact_ref'))
assert edge['from']=='s1' and edge['to']=='target' and edge['evidence']=='artifact_inspected'
assert h.node('s1')['produced_artifacts']==['oaw:artifact:c:v1']

ctx = SimpleNamespace(state={'session_id':'native','_graph_planning_node_id':'replanning'})
nodes = {key:{'node_id':key,'label':key,'action':'verify','suggested_skills':[]} for key in ('a','b','join')}
nodes['join']['consumed_inputs']=[{'node_id':'a'},{'node_id':'b'},{'step_id':'s1'},{'artifact_ref':'oaw:artifact:c:v1'}]
plan = {'nodes':nodes,'edges':[['a','join'],['b','join']]}
assert validate_graph(plan,ctx)['status']=='ok'
committed = get_execution_graph(ctx.state)
first_id=committed['graph_id']
assert h._read()['plans'][first_id]['planning_id']=='replanning'
assert {n['node_id'] for n in get_ready_nodes(ctx)['ready_nodes']}=={'a','b'}
nodes['join']['consumed_inputs']=[{'step_id':'nonexistent'}]
assert validate_graph({'nodes':nodes,'edges':[]},ctx)['status']=='error'
nodes['join']['consumed_inputs']=[{'node_id':'a'}]
assert validate_graph({'nodes':nodes,'edges':[]},ctx)['status']=='error'
nodes['join']['consumed_inputs']=[]
assert validate_graph({'nodes':nodes,'edges':[]},ctx)['status']=='ok'
assert len(h._read()['plans'])==2, 'replanning cannot delete prior planned DAG'

h.log_node_start('actual-a','step','Actual A','e20',plan_id=first_id,logical_node_id='a')
h.log_node_complete('actual-a','success')
committed['nodes']['a']['attempt_id']='actual-a'
ctx.state['execution_graph']=[committed]
assert _graph_dependency_step_ids(h,ctx,'join')==['actual-a'], 'missing predecessor never gets fabricated ID'

# Recovery must keep reused IDs in separate plan namespaces and recursive
# local numbers must never overwrite scheduler attempt records.
for plan_id in ('old','new'):
    attempt=start_node_attempt(workspace_dir=root/'workspace',session_id='native',node_id='a',step_id=f'{plan_id}-a',
        step_number=1,action='verify',suggested_skills=[],prior_context=None,graph_id=plan_id)
    finish_node_attempt(attempt,status='success',result={'concise_summary':plan_id})
child=start_node_attempt(workspace_dir=root/'workspace',session_id='native',node_id='a',step_id='child-a',
    step_number=1,action='child',suggested_skills=[],prior_context=None,graph_id='old',recovery_scope='parent',scheduler_node=False)
finish_node_attempt(child,status='failed')
ctx.state['execution_graph']=[{'graph_id':'old','nodes':{'a':{'status':'pending'}},'edges':[]}]
assert reconcile_recovery_state(ctx.state,root/'workspace')
assert get_execution_graph(ctx.state)['nodes']['a']['result']=='old'
assert get_execution_graph(ctx.state)['nodes']['a']['attempt_id']=='old-a'

# Execute the real step runner with an in-process event stream. The model
# transport is stubbed here; input resolution, lifecycle and recovery are real.
from oaw_matcreator_engine.agents.execution_agent import step_executor_runner as runtime
from oaw_matcreator_engine.agents.execution_agent.step_executor import run_sub_agent
class State(dict):
    def to_dict(self): return dict(self)
class FakeRunner:
    def __init__(self, **kwargs): self.session_service=kwargs['session_service']
    async def close(self): pass
async def stream(runner, session, content, step_id, tool_context, graph):
    text=content.parts[0].text
    assert all(f'value {index}' in text for index in (1,9,17))
    return {'_step_result':{'status':'success','key_results':'combined','concise_summary':'combined','artifacts':[]}},[],[],{}
runtime.Runner=FakeRunner
runtime._stream_step_events=stream
execution_ctx=SimpleNamespace(state=State(session_id='native',_graph_exec_node_id='target',_graph_execution_id='e20',recursion_depth=1),
    _invocation_context=SimpleNamespace(app_name='MatCreator',user_id='oaw',credential_service=None,session=SimpleNamespace(id='native')))
async def delivered():
    return await run_sub_agent(step_number=1,action='combine',suggested_skills=[],tool_context=execution_ctx,
        consumed_inputs=[{'step_id':f's{index}'} for index in (1,9,17)])
received=asyncio.run(delivered())
assert received['status']=='success' and received['step_id']
assert h.node(received['step_id'])['actor_id']==received['step_id']
assert h.node(received['step_id'])['workstream_id']=='target:subtask:1'
assert {(edge['from'],edge['to']) for edge in h._read()['edges'] if edge['to']==received['step_id'] and edge['kind']=='data'}=={
    (f's{index}',received['step_id']) for index in (1,9,17)}
assert any(edge['from']=='target' and edge['to']==received['step_id'] and edge['kind']=='fork'
    for edge in h._read()['edges'])
assert any(edge['from']==received['step_id'] and edge['to']=='target' and edge['evidence']=='child_result_returned'
    for edge in h._read()['edges'])

# Every write remains a valid JSON snapshot, including concurrent writers and
# an independent reader. This exercises actual atomic file replacement.
def write(index):
    h.log_node_start(f'parallel-{index}','step','Concurrent','e20',plan_id='parallel')
with ThreadPoolExecutor(max_workers=8) as pool:
    futures=[pool.submit(write,index) for index in range(80)]
    while not all(f.done() for f in futures):
        snapshot=read_agent_history(h._path)
        assert snapshot is None or isinstance(snapshot['nodes'],dict)
    for f in futures: f.result()
restored=AgentGraphLogger('native')
sequences=[n['sequence'] for n in restored._read()['nodes'].values()]
assert len(sequences)==len(set(sequences))
h.log_tool_call('target',{'secret':'not-public'})
h.log_state_delta('target',{'private':'not-public'})
# Host stale-write guards compare revisions, including terminal worker phases
# and goal edits that have no accompanying node or edge mutation.
h.log_snapshot_context('running','original goal')
running_revision=h._read()['revision']
h.log_snapshot_context('running','original goal')
assert h._read()['revision']==running_revision
h.log_snapshot_context('running','updated goal')
goal_revision=h._read()['revision']
assert goal_revision>running_revision
h.log_snapshot_context('idle','updated goal')
idle_revision=h._read()['revision']
assert idle_revision>goal_revision
AgentGraphLogger('native').log_snapshot_context('running','next run goal')
assert h._read()['revision']>idle_revision, 'publication revision survives worker reload'
public=read_agent_history(h._path)
assert 'secret' not in json.dumps(public) and 'private' not in json.dumps(public)
assert len(read_agent_graph(h._path))==len(public['nodes'])
page=read_execution_history(ctx,limit=3)
assert len(page['steps'])==3 and page['next_before_sequence']

legacy=AgentGraphLogger('legacy')
legacy._path.write_text(json.dumps({'nodes':{'p':{'id':'p','type':'planning'},'s':{'id':'s','type':'step','parent_id':'p'}},
    'edges':[{'from':'p','to':'s'}]}),encoding='utf-8')
assert legacy._read()['edges']==[], 'legacy hierarchy cannot become dependency'
legacy._path.write_text('{',encoding='utf-8')
try: legacy.log_node_start('new','step','new')
except json.JSONDecodeError: pass
else: raise AssertionError('corrupt persisted history was silently overwritten')
print('history semantics verified')
'''
    result = subprocess.run([str(executable), "-I", "-S", "-X", "utf8", "-c", script,
        str(adapter), str(dependencies), str(tmp_path)], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "history semantics verified" in result.stdout
