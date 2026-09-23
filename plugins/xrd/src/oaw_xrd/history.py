"""Read-only, owner-scoped history of durable XRD run snapshots.

Large scientific files stay in the run store, never in the bounded card-state JSON.
Opening history cannot mutate a live workflow or resume a worker.
"""
import base64
import hashlib
import json
from pathlib import Path
import shutil

from open_agent_world.plugin_api import NodeResourceAction
from .pipeline import run_directory

ACTIVE_RUNS = set()

MAX_FILE_BYTES = 30 * 1024 * 1024


def archive_review(directory, state):
    """Keep each review attempt before its live files can be reused on retry."""
    started = state.get('review_started_at_ns')
    if not isinstance(started, int) or started <= 0:
        return
    parent = directory / 'review-history'
    target = parent / str(started)
    if target.exists():
        return
    temporary = parent / f'{started}.tmp'
    temporary.mkdir(parents=True, exist_ok=True)
    (temporary / 'state.json').write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    for name in ('oaw.json', 'input.json', 'result.json', 'console.log'):
        source = directory / name
        if source.is_file():
            shutil.copy2(source, temporary / name)
    source = directory / 'pywpem-review'
    if source.is_dir():
        shutil.copytree(source, temporary / 'outputs', dirs_exist_ok=True)
    temporary.rename(target)


def _root():
    from .multiphase_object import _root as roots
    return roots()[1]


def _json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _owned(root, node_id, run_id):
    directory = run_directory(root, run_id)
    manifest_path = directory / 'oaw.json'
    if not manifest_path.resolve().is_relative_to(directory):
        raise ValueError('无效的运行记录路径')
    manifest = _json(manifest_path)
    owner = manifest.get('source_owner_node_id') or manifest.get('agent_id')
    if owner != node_id or manifest.get('run_id') != run_id:
        raise ValueError('不能读取其他工作台的运行记录')
    return directory, manifest


def _summary(manifest):
    from .multiphase_object import _processes, _review_tasks
    if manifest.get('status') == 'running' and manifest.get('run_id') not in ACTIVE_RUNS | set(_processes) | set(_review_tasks):
        manifest = {**manifest, 'status': 'interrupted'}
    return {key: manifest.get(key) for key in (
        'run_id', 'workflow_stage', 'status', 'created_at_ns', 'finished_at_ns',
        'workflow_match_run_id', 'workflow_preopt_run_id', 'error')}


def list_runs(context, arguments):
    offset = arguments.get('offset', 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError('无效的历史分页')
    root = _root()
    items = []
    for path in root.glob('oaw-*/oaw.json'):
        if context.cancelled.is_set():
            break
        try:
            _, manifest = _owned(root, context.node_id, path.parent.name.removeprefix('oaw-'))
            items.append(_summary(manifest))
        except (OSError, ValueError, TypeError):
            continue
    items.sort(key=lambda item: (item.get('created_at_ns') or 0, item['run_id']), reverse=True)
    return {'items': items[offset:offset + 50], 'total': len(items), 'offset': offset}


def inspect_run(context, arguments):
    directory, manifest = _owned(_root(), context.node_id, arguments.get('run_id'))
    files = []
    for path in sorted(directory.rglob('*')):
        if path.is_file() and path.resolve().is_relative_to(directory) and not path.name.endswith('.tmp'):
            files.append({'name': path.relative_to(directory).as_posix(), 'size_bytes': path.stat().st_size})
    # Configuration is kept separately from result snapshots; do not duplicate
    # potentially multi-megabyte experimental CSV or CIF strings in the overview.
    parameters = {}
    if (directory / 'input.json').is_file() and (directory / 'input.json').resolve().is_relative_to(directory):
        value = _json(directory / 'input.json')
        parameters = {k: v for k, v in value.items() if k not in {'intensity_csv', 'cif'}}
    return {'manifest': {**manifest, **_summary(manifest)}, 'parameters': parameters, 'files': files}


def read_file(context, arguments):
    directory, _ = _owned(_root(), context.node_id, arguments.get('run_id'))
    name = arguments.get('name')
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('无效的归档文件名')
    path = (directory / name).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise ValueError('找不到归档文件')
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('单个文件超过 30 MiB，请从本地运行目录读取')
    raw = path.read_bytes()
    return {'name': name, 'data': base64.b64encode(raw).decode(),
            'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}


def actions():
    return {'history_list': NodeResourceAction(list_runs),
            'history_inspect': NodeResourceAction(inspect_run),
            'history_file': NodeResourceAction(read_file)}
