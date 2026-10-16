"""Step 3 approval workflow: auth, optimistic revision checks and fail-closed grants."""
import hashlib
import pytest


def _user(app, role):
    name = 'review_' + role
    app.auth.create_user(name, 'CorrectHorseBattery42', role)
    client = 'review-client-' + role
    login = app.process({'type': 'login', 'client_id': client, 'username': name,
                         'password': 'CorrectHorseBattery42'})
    return {'client_id': client, 'token': login['token']}


def _add(app, root, name='candidate_resume.txt'):
    (root / 'knowledge' / name).write_text('Resume: Python engineer; private candidate details.', encoding='utf-8')
    assert app.knowledge.sync_once() == 'updated'
    return name


def test_admin_review_queue_and_approve(identity):
    app, admin, root = identity
    name = _add(app, root)
    hr = _user(app, 'hr')
    queue = app.process({**admin, 'type': 'document_review_queue'})
    item = next(i for i in queue['items'] if i['source'] == name)
    assert item['status'] == 'pending'
    assert item['approved_roles'] == []
    assert name not in app.document_access.allowed_sources('hr', app.knowledge)
    result = app.process({**admin, 'type': 'document_review_decide', 'source': name,
                          'sha256': item['sha256'], 'approve': True, 'roles': ['hr']})
    assert result['status'] == 'approved'
    assert name in app.document_access.allowed_sources('hr', app.knowledge)
    assert name not in app.document_access.allowed_sources('viewer', app.knowledge)
    assert all(x['source'] != name for x in app.process({**admin, 'type': 'document_review_queue'})['items'])
    approved = app.process({**admin, 'type': 'document_review_queue', 'status': 'approved'})
    assert any(x['source'] == name and x['approved_roles'] == ['hr'] for x in approved['items'])
    assert app.process({**hr, 'type': 'knowledge_status'})['files'] == 1


def test_non_admin_cannot_list_preview_or_decide(identity):
    app, admin, root = identity
    name = _add(app, root)
    viewer = _user(app, 'viewer')
    digest = app.document_access._index_snapshot(app.knowledge)[name]
    for action in ({'type': 'document_review_queue'},
                   {'type': 'document_review_preview', 'source': name, 'sha256': digest},
                   {'type': 'document_review_decide', 'source': name, 'sha256': digest,
                    'approve': True, 'roles': ['viewer']}):
        with pytest.raises(PermissionError):
            app.process({**viewer, **action})
    assert name not in app.document_access.allowed_sources('viewer', app.knowledge)


def test_reject_is_explicit_and_not_visible(identity):
    app, admin, root = identity
    name = _add(app, root)
    item = next(x for x in app.process({**admin, 'type': 'document_review_queue'})['items'] if x['source'] == name)
    app.process({**admin, 'type': 'document_review_decide', 'source': name,
                 'sha256': item['sha256'], 'approve': False, 'roles': []})
    rejected = app.process({**admin, 'type': 'document_review_queue', 'status': 'rejected'})
    assert any(x['source'] == name for x in rejected['items'])
    assert name not in app.document_access.allowed_sources('hr', app.knowledge)
    with pytest.raises(ValueError, match='already reviewed'):
        app.process({**admin, 'type': 'document_review_decide', 'source': name,
                     'sha256': item['sha256'], 'approve': True, 'roles': ['hr']})


def test_no_automatic_approval_from_qwen_suggestions(identity):
    app, admin, root = identity
    name = _add(app, root)
    digest = app.document_access._index_snapshot(app.knowledge)[name]
    app.knowledge.classifier._save({name: {
        'source': name, 'sha256': digest, 'department': 'hr', 'document_type': 'resume',
        'sensitivity': 'confidential', 'classification_status': 'classified',
        'suggested_roles': ['hr', 'manager']}})
    item = next(x for x in app.process({**admin, 'type': 'document_review_queue'})['items'] if x['source'] == name)
    assert item['suggested_roles'] == ['hr', 'manager']
    assert item['status'] == 'pending'
    assert app.document_access.allowed_sources('hr', app.knowledge) == set()
    with pytest.raises(ValueError, match='one or more'):
        app.process({**admin, 'type': 'document_review_decide', 'source': name,
                     'sha256': digest, 'approve': True, 'roles': []})


def test_stale_revision_or_live_edit_cannot_be_approved(identity):
    app, admin, root = identity
    name = _add(app, root)
    digest = app.document_access._index_snapshot(app.knowledge)[name]
    (root / 'knowledge' / name).write_text('changed after sync', encoding='utf-8')
    with pytest.raises(ValueError, match='changed'):
        app.process({**admin, 'type': 'document_review_decide', 'source': name,
                     'sha256': digest, 'approve': True, 'roles': ['hr']})
    assert name not in app.document_access.allowed_sources('hr', app.knowledge)
    assert app.knowledge.sync_once() == 'updated'
    with pytest.raises(ValueError, match='revision changed'):
        app.process({**admin, 'type': 'document_review_decide', 'source': name,
                     'sha256': digest, 'approve': True, 'roles': ['hr']})
    refreshed = app.process({**admin, 'type': 'document_review_queue'})
    new = next(x for x in refreshed['items'] if x['source'] == name)
    assert new['sha256'] != digest
    app.process({**admin, 'type': 'document_review_decide', 'source': name,
                 'sha256': new['sha256'], 'approve': True, 'roles': ['hr']})
    assert name in app.document_access.allowed_sources('hr', app.knowledge)
    (root / 'knowledge' / name).write_text('another edit', encoding='utf-8')
    app.knowledge.sync_once()
    assert name not in app.document_access.allowed_sources('hr', app.knowledge)
    assert any(x['source'] == name for x in app.process({**admin, 'type': 'document_review_queue'})['items'])


def test_preview_is_admin_only_and_indexed(identity):
    app, admin, root = identity
    name = _add(app, root)
    digest = app.document_access._index_snapshot(app.knowledge)[name]
    result = app.process({**admin, 'type': 'document_review_preview',
                          'source': name, 'sha256': digest})
    assert 'Python engineer' in result['text']
    assert result['source'] == name
    with pytest.raises(ValueError):
        app.process({**admin, 'type': 'document_review_preview',
                     'source': name, 'sha256': '0' * 64})


def test_malformed_roles_and_pagination_rejected(identity):
    app, admin, root = identity
    name = _add(app, root)
    digest = app.document_access._index_snapshot(app.knowledge)[name]
    for roles in (['admin'], ['viewer', 'viewer'], ['unknown'], 'hr', None):
        with pytest.raises(ValueError):
            app.process({**admin, 'type': 'document_review_decide', 'source': name,
                         'sha256': digest, 'approve': True, 'roles': roles})
    for page in (0, -1, '1', True):
        with pytest.raises(ValueError):
            app.process({**admin, 'type': 'document_review_queue', 'page': page})
