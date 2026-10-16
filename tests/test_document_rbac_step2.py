"""Step 2: real server-side RBAC and fail-closed document inventories/retrieval."""
import json
import pytest

from orion_db.agent import validate_plan
from orion_db.reports import generate_document_report


def _user(app, role):
    name = 'user_' + role
    app.auth.create_user(name, 'CorrectHorseBattery42', role)
    client_id = 'test-' + role
    response = app.process({'type': 'login', 'client_id': client_id,
                            'username': name, 'password': 'CorrectHorseBattery42'})
    return {'client_id': client_id, 'token': response['token']}


def _chat(app, auth, question):
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    return app.process({**auth, 'type': 'chat', 'thread_id': tid, 'message': question})


def _add_docs(app, root):
    (root / 'knowledge' / 'candidate_resume.txt').write_text(
        'Resume\nProfessional Experience\nPython programming 5 years\nEducation: BTech', encoding='utf-8')
    (root / 'knowledge' / 'leave_policy.md').write_text(
        'Leave Policy\nEmployees receive 12 annual leave days.\n', encoding='utf-8')
    assert app.knowledge.sync_once() == 'updated'


def test_new_documents_are_admin_only_until_explicit_grant(sandbox):
    app, cfg, root = sandbox
    _add_docs(app, root)
    viewer = _user(app, 'viewer')
    hr = _user(app, 'hr')
    for auth in (viewer, hr):
        assert app.process({**auth, 'type': 'knowledge_status'})['files'] == 0
        assert _chat(app, auth, 'how many resumes?')['document_count'] == 0
        assert _chat(app, auth, 'any new candidate resumes today?')['document_count'] == 0
        assert app.document_access.allowed_sources('hr', app.knowledge) == set()
    assert 'candidate_resume.txt' in app.document_access.allowed_sources('admin', app.knowledge)


def test_grant_per_role_does_not_use_qwen_suggestions(sandbox):
    app, cfg, root = sandbox
    _add_docs(app, root)
    # Qwen's classification suggestion is NOT an authorization grant.
    records = {'version': 1, 'documents': {'candidate_resume.txt': {
        'source': 'candidate_resume.txt', 'sha256': 'bogus', 'classification_status': 'classified',
        'suggested_roles': ['viewer', 'employee', 'hr'], 'approval_status': 'approved'}}}
    path = app.knowledge.classifier._path()
    path.write_text(json.dumps(records), encoding='utf-8')
    assert app.document_access.allowed_sources('viewer', app.knowledge) == set()
    app.document_access.grant_local(app.knowledge, 'candidate_resume.txt', ['hr'])
    assert app.document_access.allowed_sources('hr', app.knowledge) == {'candidate_resume.txt'}
    assert app.document_access.allowed_sources('viewer', app.knowledge) == set()
    hr = _user(app, 'hr')
    viewer = _user(app, 'viewer')
    assert _chat(app, hr, 'count resumes')['document_count'] == 1
    assert _chat(app, viewer, 'count resumes')['document_count'] == 0
    assert _chat(app, hr, 'any new candidate resumes today?')['document_count'] == 1
    assert _chat(app, viewer, 'any new candidate resumes today?')['document_count'] == 0


def test_agent_search_cannot_read_ungranted_docs(sandbox):
    app, cfg, root = sandbox
    _add_docs(app, root)
    hr = _user(app, 'hr')
    app.document_access.grant_local(app.knowledge, 'candidate_resume.txt', ['hr'])
    result = _chat(app, hr, 'What is the company leave policy?')
    assert '12 annual leave' not in result['answer']
    assert 'leave_policy.md' not in result.get('sources', [])
    app.document_access.grant_local(app.knowledge, 'leave_policy.md', ['employee'])
    employee = _user(app, 'employee')
    # Direct agent executor: no keyword routing assumptions.
    result = app.agent.execute('search_knowledge', {'query': 'annual leave days'},
                               'annual leave days', employee['token'], 'employee')
    assert result['sources'] == ['leave_policy.md']
    assert '12 annual leave' in result['answer']
    assert app.agent.execute('count_documents', {'document_type': 'all'}, 'count',
                             employee['token'], 'employee')['document_count'] == 1


def test_changed_file_revokes_grant_until_reapproved(sandbox):
    app, cfg, root = sandbox
    _add_docs(app, root)
    app.document_access.grant_local(app.knowledge, 'candidate_resume.txt', ['hr'])
    assert app.document_access.allowed_sources('hr', app.knowledge) == {'candidate_resume.txt'}
    (root / 'knowledge' / 'candidate_resume.txt').write_text('Resume\nNew sensitive details', encoding='utf-8')
    assert app.knowledge.sync_once() == 'updated'
    assert app.document_access.allowed_sources('hr', app.knowledge) == set()
    app.document_access.grant_local(app.knowledge, 'candidate_resume.txt', ['hr'])
    assert app.document_access.allowed_sources('hr', app.knowledge) == {'candidate_resume.txt'}


def test_deleted_document_is_removed_from_permissions(sandbox):
    app, cfg, root = sandbox
    _add_docs(app, root)
    app.document_access.grant_local(app.knowledge, 'candidate_resume.txt', ['hr'])
    (root / 'knowledge' / 'candidate_resume.txt').unlink()
    assert app.knowledge.sync_once() == 'updated'
    assert app.document_access.allowed_sources('hr', app.knowledge) == set()


def test_corrupt_acl_fails_closed(sandbox):
    app, cfg, root = sandbox
    _add_docs(app, root)
    app.document_access.grant_local(app.knowledge, 'candidate_resume.txt', ['hr'])
    app.document_access._path().write_text('{corrupt', encoding='utf-8')
    assert app.document_access.allowed_sources('hr', app.knowledge) == set()
    assert 'candidate_resume.txt' in app.document_access.allowed_sources('admin', app.knowledge)


def test_no_role_escalation_or_unknown_grants(sandbox):
    app, cfg, root = sandbox
    _add_docs(app, root)
    for roles in (['admin'], ['superuser'], ['hr', 'superuser'], []):
        with pytest.raises(ValueError):
            app.document_access.grant_local(app.knowledge, 'candidate_resume.txt', roles)
    with pytest.raises(ValueError):
        app.document_access.grant_local(app.knowledge, '../config/orion.yaml', ['hr'])
    assert app.document_access.allowed_sources('superuser', app.knowledge) == set()


def test_document_reports_require_per_document_access(sandbox):
    app, cfg, root = sandbox
    path = root / 'knowledge' / 'finance.csv.txt'
    path.write_text('item,amount\nLaptop,150\nMonitor,200\n', encoding='utf-8')
    assert app.knowledge.sync_once() == 'updated'
    analyst = _user(app, 'analyst')
    with pytest.raises(PermissionError):
        app.process({**analyst, 'type': 'report_generate_document', 'source': 'finance.csv.txt'})
    app.document_access.grant_local(app.knowledge, 'finance.csv.txt', ['analyst'])
    result = app.process({**analyst, 'type': 'report_generate_document', 'source': 'finance.csv.txt'})
    assert result['row_count'] == 2
    path.write_text('item,amount\nSecret,999999\n', encoding='utf-8')
    with pytest.raises(PermissionError):
        app.process({**analyst, 'type': 'report_generate_document', 'source': 'finance.csv.txt'})


def test_document_catalog_and_status_do_not_leak(sandbox):
    app, cfg, root = sandbox
    _add_docs(app, root)
    viewer = _user(app, 'viewer')
    status = app.process({**viewer, 'type': 'knowledge_status'})
    assert status['files'] == 0
    assert 'folder' not in status and 'classification' not in status
    for action in ('schema_catalog', 'discovery_status'):
        with pytest.raises(PermissionError):
            app.process({**viewer, 'type': action})
    with pytest.raises(PermissionError):
        app.agent.execute('list_databases', {}, 'list databases', viewer['token'], 'viewer')
    with pytest.raises(PermissionError):
        app.agent.execute('list_tables', {}, 'list tables', viewer['token'], 'viewer')


def test_revoke_removes_permissions_immediately(sandbox):
    app, cfg, root = sandbox
    _add_docs(app, root)
    app.document_access.grant_local(app.knowledge, 'leave_policy.md', ['employee'])
    assert app.document_access.allowed_sources('employee', app.knowledge) == {'leave_policy.md'}
    app.document_access.revoke_local('leave_policy.md')
    assert app.document_access.allowed_sources('employee', app.knowledge) == set()


def test_new_user_roles_supported(sandbox):
    app, cfg, root = sandbox
    for role in ('hr', 'manager', 'finance', 'employee'):
        auth = _user(app, role)
        assert app.process({**auth, 'type': 'hello'})['role'] == role


def test_existing_user_role_change_invalidates_sessions(sandbox):
    app, cfg, root = sandbox
    auth = _user(app, 'viewer')
    assert app.auth.set_role('user_viewer', 'hr')['role'] == 'hr'
    with pytest.raises(PermissionError):
        app.process({**auth, 'type': 'hello'})
    login = app.process({'type': 'login', 'client_id': 'test-viewer',
                         'username': 'user_viewer', 'password': 'CorrectHorseBattery42'})
    assert login['role'] == 'hr'
    with pytest.raises(PermissionError):
        app.auth.set_role('tester', 'employee')
