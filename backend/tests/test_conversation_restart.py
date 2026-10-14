from backend.agents.context import Checkpoint
from backend.tests.test_conversations import _create


def test_restart_copies_complete_prefix_with_fresh_context_and_scoped_replay(client):
    services = client.app.state.services
    room = _create(client, 'conversation', 'Room')
    base = f"/api/conversations/{room['id']}"
    session = client.post(base + '/sessions', json={'title': 'Original'}).json()
    store = services.conversations
    for number in range(505):
        store.add_message(room['id'], session['id'], sender_kind='user', sender_id=None,
                          sender_name='You', content=f'prefix {number}')
    source = store.add_message(room['id'], session['id'], sender_kind='user', sender_id=None,
                               sender_name='You', content='replace me')
    later = store.add_message(room['id'], session['id'], sender_kind='agent', sender_id=None,
                              sender_name='Agent', content='future answer')
    payload = {'title': 'Branch', 'group_id': session['group_id'], 'restart_before_message_id': source.id}
    headers = {'Idempotency-Key': 'restart-test'}
    response = client.post(base + '/sessions', json=payload, headers=headers)
    assert response.status_code == 201, response.text
    branch = response.json()
    assert branch['id'] != session['id']
    assert branch['restart_before_message_id'] == source.id
    assert client.post(base + '/sessions', json=payload, headers=headers).json()['id'] == branch['id']
    messages = store.context_messages(room['id'], branch['id'])
    assert len(messages) == 505
    assert messages[0].content == 'prefix 0' and messages[-1].content == 'prefix 504'
    assert all(message.run_id is None for message in messages)
    checkpoint = services.contexts.load('agent', branch['id'])
    assert checkpoint == Checkpoint()
    delta = str(services.contexts.conversation_delta(checkpoint, 'agent', branch['id']))
    assert 'prefix 0' in delta and 'prefix 504' in delta
    assert 'replace me' not in delta and 'future answer' not in delta
    result = client.post(base + f"/sessions/{branch['id']}/messages", json={'content': 'edited'})
    assert result.status_code == 202
    assert store.get_message(source.id).content == 'replace me'
    assert store.get_message(later.id).content == 'future answer'
    other = _create(client, 'conversation', 'Other')
    assert client.post(f"/api/conversations/{other['id']}/sessions", json={
        'restart_before_message_id': source.id}).status_code == 404
    before = len(store.list_sessions(room['id']))
    assert client.post(base + '/sessions', json={'restart_before_message_id': later.id}).status_code == 422
    assert len(store.list_sessions(room['id'])) == before


def test_restart_keeps_attachment_authorization_in_new_session(client):
    from backend.tests.test_conversation_attachments import room, upload, ref
    node, session, base = room(client)
    file = upload(client, base)
    client.post(base + '/messages', json={'content': 'file context', 'attachments': [ref(file)]})
    source = client.post(base + '/messages', json={'content': 'edit this'}).json()['message']
    branch = client.post(f"/api/conversations/{node['id']}/sessions", json={
        'restart_before_message_id': source['id']}).json()
    branch_base = f"/api/conversations/{node['id']}/sessions/{branch['id']}"
    copied = client.get(branch_base + '/timeline').json()['items']
    assert copied[0]['attachments'][0]['version_id'] == file['version_id']
    response = client.get(branch_base + '/attachments/' + file['version_id'], params={'path': file['path']})
    assert response.status_code == 200 and response.content == b'hello'
    # The selected message's attachments may be edited/resubmitted, but cannot
    # leak to the Agent before submission. Later files are never granted.
    draft_file = upload(client, base, 'draft.txt', b'draft')
    selected = client.post(base + '/messages', json={'attachments': [ref(draft_file)]}).json()['message']
    future_file = upload(client, base, 'future.txt', b'future')
    client.post(base + '/messages', json={'attachments': [ref(future_file)]})
    branch = client.post(f"/api/conversations/{node['id']}/sessions", json={
        'restart_before_message_id': selected['id']}).json()
    branch_base = f"/api/conversations/{node['id']}/sessions/{branch['id']}"
    from backend.conversations.attachments import visible
    artifacts = client.app.state.services.resources.artifacts
    assert not visible(artifacts, artifacts.get(draft_file['version_id']), branch['id'], 'agent')
    assert client.post(branch_base + '/messages', json={'attachments': [ref(draft_file)]}).status_code == 202
    assert visible(artifacts, artifacts.get(draft_file['version_id']), branch['id'], 'agent')
    assert client.post(branch_base + '/messages', json={'attachments': [ref(future_file)]}).status_code == 403
