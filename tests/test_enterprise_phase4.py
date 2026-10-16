import pytest
from test_document_review_step3 import _add, _user


def test_manager_can_review_with_configured_permission(identity):
    app, admin, root = identity
    name = _add(app, root)
    manager = _user(app, 'manager')
    item = next(x for x in app.process({**manager, 'type': 'document_review_queue'})['items'] if x['source'] == name)
    assert app.process({**manager, 'type': 'document_review_preview', 'source': name,
                        'sha256': item['sha256']})['source'] == name
    result = app.process({**manager, 'type': 'document_review_decide', 'source': name,
                          'sha256': item['sha256'], 'approve': True, 'roles': ['hr']})
    assert result['status'] == 'approved'
    assert name in app.document_access.allowed_sources('hr', app.knowledge)


def test_review_permissions_fail_closed_if_manager_not_configured(identity):
    app, admin, root = identity
    manager = _user(app, 'manager')
    app.document_access._settings = lambda: {'enabled': True, 'approval_required': True,
                                              'reviewer_roles': ['admin']}
    with pytest.raises(PermissionError):
        app.process({**manager, 'type': 'document_review_queue'})


def test_other_roles_cannot_review(identity):
    app, admin, root = identity
    for role in ('employee', 'hr', 'viewer'):
        who = _user(app, role)
        with pytest.raises(PermissionError):
            app.process({**who, 'type': 'document_review_queue'})


def test_history_pagination_and_user_isolation(identity):
    app, admin, root = identity
    thread = app.process({**admin, 'type': 'new_chat'})['thread_id']
    for i in range(7):
        app.history.add('tester', thread, f'question {i}', f'answer {i}')
    first = app.process({**admin, 'type': 'chat_history_page', 'thread_id': thread, 'limit': 4})
    assert len(first['messages']) == 4 and first['has_more']
    second = app.process({**admin, 'type': 'chat_history_page', 'thread_id': thread,
                          'before_id': first['next_before_id'], 'limit': 4})
    assert len(second['messages']) == 4
    assert max(x['id'] for x in second['messages']) < min(x['id'] for x in first['messages'])
    other = _user(app, 'employee')
    with pytest.raises(PermissionError):
        app.process({**other, 'type': 'chat_history_page', 'thread_id': thread})


def test_invalid_history_cursor_rejected(identity):
    app, admin, root = identity
    thread = app.process({**admin, 'type': 'new_chat'})['thread_id']
    with pytest.raises(ValueError):
        app.process({**admin, 'type': 'chat_history_page', 'thread_id': thread, 'before_id': '1'})
