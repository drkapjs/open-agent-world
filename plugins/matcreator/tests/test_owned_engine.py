"""Pack ownership and actual engine behavior, without an external MatCreator."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from plugins.matcreator.oaw_matcreator import native_environment

PACK = Path(__file__).resolve().parents[1]
ENGINE = PACK / 'oaw_matcreator_engine'


def test_engine_is_packaged_and_has_no_host_or_external_engine_imports():
    project = tomllib.loads((PACK / 'pyproject.toml').read_text(encoding='utf-8'))
    assert 'oaw_matcreator_engine' in project['tool']['hatch']['build']['targets']['wheel']['packages']
    forbidden = {'matcreator', 'backend', 'open_agent_world', 'know_do_graph', 'a2a'}
    for path in ENGINE.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import):
                assert not forbidden.intersection(alias.name.split('.')[0] for alias in node.names), path
            elif isinstance(node, ast.ImportFrom) and not node.level:
                assert (node.module or '').split('.')[0] not in forbidden, path
    assert not (PACK / 'oaw_matcreator/native_lock.json').exists()


@pytest.mark.skipif(not os.environ.get('OAW_MATCREATOR_MANAGED'), reason='requires Pack orchestration dependencies')
def test_isolated_engine_dependency_recovery_and_resource_boundaries(tmp_path):
    executable, dependencies = native_environment.read_environment()
    script = r'''
import asyncio, importlib.abc, json, os, sys
from pathlib import Path
root = Path(sys.argv[3])
os.environ.update(MATCREATOR_HOME=str(root / 'home'), MATCLAW_WORKSPACE=str(root / 'workspace'),
    LLM_MODEL='openai/test', LLM_API_KEY='private-test-key', LLM_BASE_URL='http://127.0.0.1:1')
sys.path.insert(0, sys.argv[1])
from native_environment import activate, activate_engine
activate(Path(sys.argv[2]))
activate_engine()

class RejectExternal(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'matcreator', 'backend', 'open_agent_world', 'know_do_graph', 'a2a'}:
            raise AssertionError('Forbidden import: ' + fullname)
sys.meta_path.insert(0, RejectExternal())

from native_adapter import install
available = True
async def rpc(method, **args):
    if method == 'list_tools':
        return [{'name': 'inspect_artifacts', 'capability_id': 'read'}] if available else []
    return {'ok': True, 'result': [{'type': 'inputText', 'text': json.dumps({'state': 'ready'})}]}
app = install(rpc, lambda *args, **kwargs: None)
from oaw_matcreator_engine.agents.thinking_agent.planning import validate_graph, get_ready_nodes, set_node_status
from oaw_matcreator_engine.agents.execution_graph_state import get_execution_graph
from oaw_matcreator_engine.agents.execution_agent.recovery import start_node_attempt, finish_node_attempt, reconcile_recovery_state
from oaw_matcreator_engine.agents.session_log import normalize_artifact_paths
from oaw_matcreator_engine.resources import validate_artifacts
from oaw_matcreator_engine.workspace import get_session_workdir
from oaw_matcreator_engine.agents.thinking_agent.session_summary import write_session_summary
from types import SimpleNamespace

context = SimpleNamespace(state={'session_id': 'native'})
nodes = {key: {'node_id': key, 'label': key, 'action': 'Verify evidence', 'suggested_skills': []}
         for key in ('a', 'b', 'join')}
assert validate_graph({'nodes': nodes, 'edges': [['a','join'], ['b','join']]}, context)['status'] == 'ok'
graph = get_execution_graph(context.state)
assert {n['node_id'] for n in get_ready_nodes(context)['ready_nodes']} == {'a','b'}
set_node_status('a', 'success', tool_context=context)
assert [n['node_id'] for n in get_ready_nodes(context)['ready_nodes']] == ['b']
attempt = start_node_attempt(workspace_dir=root/'workspace', session_id='native', graph_id=graph['graph_id'],
    node_id='b', step_id='execution_0__node_b', step_number=2, action='Verify evidence', suggested_skills=[], prior_context=None)
finish_node_attempt(attempt, status='success', result={'concise_summary': 'Verified'})
assert reconcile_recovery_state(context.state, root/'workspace')
assert [n['node_id'] for n in get_ready_nodes(context)['ready_nodes']] == ['join']
# A new graph reusing IDs must not inherit an earlier graph's completed attempt.
assert validate_graph({'nodes': nodes, 'edges': []}, context)['status'] == 'ok'
assert not reconcile_recovery_state(context.state, root/'workspace')
assert get_execution_graph(context.state)['nodes']['b']['status'] == 'pending'
assert normalize_artifact_paths(['oaw:artifact:c:v', '/workspace/result.txt']) == ['oaw:artifact:c:v']
assert get_session_workdir('native', '/outside') == (root/'workspace').resolve()
assert asyncio.run(validate_artifacts(['/workspace/result.txt']))
assert asyncio.run(validate_artifacts([123]))
assert asyncio.run(validate_artifacts(['oaw:artifact:c:v'])) is None
available = False
assert 'revoked' in asyncio.run(validate_artifacts(['oaw:artifact:c:v']))
assert 'private-test-key' not in json.dumps(attempt)
context._invocation_context = SimpleNamespace(session=SimpleNamespace(id='native'))
summary = write_session_summary({'goal': '铜结构', 'approach': 'Use connected tools', 'outcome': 'Verified'}, context)
assert summary['status'] == 'ok'
assert json.loads(Path(summary['path']).read_text(encoding='utf-8'))['goal'] == '铜结构'
print(json.dumps({'engine': app.name, 'source': str(Path(sys.modules['oaw_matcreator_engine'].__file__).resolve())}))
'''
    result = subprocess.run([executable, '-I', '-S', '-X', 'utf8', '-c', script,
        str(native_environment.PACKAGE), str(dependencies), str(tmp_path)],
        text=True, capture_output=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['engine'] == 'MatCreator'
    assert Path(value['source']) == ENGINE / '__init__.py'
