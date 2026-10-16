"""Step 4 security regression tests: both RAG backends and response paths."""
import json
import pytest


def login(app, role):
    user = 'rag_' + role
    app.auth.create_user(user, 'CorrectHorseBattery42', role)
    cid = 'cid_' + role
    response = app.process({'type': 'login', 'client_id': cid,
                            'username': user, 'password': 'CorrectHorseBattery42'})
    return {'client_id': cid, 'token': response['token']}


def chat(app, auth, question):
    thread = app.process({**auth, 'type': 'new_chat'})['thread_id']
    return app.process({**auth, 'type': 'chat', 'thread_id': thread, 'message': question})


def documents(app, root):
    (root / 'knowledge' / 'private_resume.txt').write_text(
        'Resume\nProfessional Experience\nCandidate SECRET_SALARY_8675\nEducation\n', encoding='utf-8')
    (root / 'knowledge' / 'public_policy.txt').write_text(
        'Company leave policy: Employees receive 12 annual leave days.\n', encoding='utf-8')
    assert app.knowledge.sync_once() == 'updated'
    app.document_access.grant_local(app.knowledge, 'private_resume.txt', ['hr'])
    app.document_access.grant_local(app.knowledge, 'public_policy.txt', ['employee', 'hr'])


def test_lexical_and_agent_cannot_leak_private_doc(sandbox):
    app, _, root = sandbox
    documents(app, root)
    viewer = login(app, 'employee')
    result = app.agent.execute('search_knowledge', {'query': 'SECRET_SALARY_8675'},
                               'SECRET_SALARY_8675', viewer['token'], 'employee')
    assert result['sources'] == []
    assert 'SECRET_SALARY_8675' not in json.dumps(result)
    assert app.rag.search('SECRET_SALARY_8675', 'employee') == []
    hr = login(app, 'hr')
    result = app.agent.execute('search_knowledge', {'query': 'SECRET_SALARY_8675'},
                               'SECRET_SALARY_8675', hr['token'], 'hr')
    assert result['sources'] == ['private_resume.txt']


def test_semantic_backend_cannot_inject_unapproved_hit(sandbox, monkeypatch):
    app, _, root = sandbox
    documents(app, root)
    app.cfg.get()['rag']['retrieval'] = 'hybrid'
    fake = {'source': 'private_resume.txt', 'text': 'Candidate SECRET_SALARY_8675'}
    monkeypatch.setattr(app.knowledge._semantic, 'search', lambda *args: [(0.99, fake)])
    results = app.rag.search('leave policy', 'employee')
    assert all(hit['source'] == 'public_policy.txt' for hit in results)
    assert 'SECRET_SALARY_8675' not in json.dumps(results)


def test_rogue_search_backend_cannot_inject_unapproved_hit(sandbox, monkeypatch):
    app, _, root = sandbox
    documents(app, root)
    monkeypatch.setattr(app.knowledge, 'search', lambda *args, **kwargs: [
        {'source': 'private_resume.txt', 'text': 'Candidate SECRET_SALARY_8675', 'score': 100},
        {'source': 'public_policy.txt', 'text': 'FAKE INJECTED CONTENT', 'score': 100},
    ])
    assert app.rag.search('policy', 'employee') == []


def test_revocation_applies_without_restart_or_sync(sandbox):
    app, _, root = sandbox
    documents(app, root)
    assert app.rag.search('SECRET_SALARY_8675', 'hr')
    app.document_access.revoke_local('private_resume.txt')
    assert app.rag.search('SECRET_SALARY_8675', 'hr') == []
    assert app.rag.answer('SECRET_SALARY_8675', 'hr')['sources'] == []


def test_edit_before_sync_invalidates_retrieval_and_inventory(sandbox):
    app, _, root = sandbox
    documents(app, root)
    assert app.rag.search('SECRET_SALARY_8675', 'hr')
    (root / 'knowledge' / 'private_resume.txt').write_text(
        'Resume\nCandidate changed after approval\nEducation\n', encoding='utf-8')
    assert app.rag.search('SECRET_SALARY_8675', 'hr') == []
    assert app.document_access.allowed_sources('hr', app.knowledge) == {'public_policy.txt'}
    hr = login(app, 'hr')
    result = chat(app, hr, 'How many resumes?')
    assert result['document_count'] == 0


def test_no_acl_means_no_rag_evidence(sandbox):
    app, _, root = sandbox
    documents(app, root)
    app.document_access._path().write_text('{corrupt', encoding='utf-8')
    assert app.rag.search('leave policy', 'employee') == []
    assert app.rag.approved_answer('leave policy', 'employee') is None


def test_model_receives_only_authorized_chunks(sandbox, monkeypatch):
    app, _, root = sandbox
    documents(app, root)
    employee = login(app, 'employee')
    app.cfg.get()['knowledge']['answer_mode'] = 'model'
    app.cfg.get()['features']['local_model'] = True
    app.cfg.get()['model']['enabled'] = True
    app.cfg.get()['agent']['enabled'] = False
    monkeypatch.setattr(app.model, 'summarize', lambda question, hits:
                        'Approved source: ' + ', '.join(x['source'] for x in hits))
    result = chat(app, employee, 'What is our company leave policy?')
    assert 'private_resume.txt' not in json.dumps(result)
    assert 'public_policy.txt' in result['answer']


def test_document_export_reads_only_selected_source(sandbox, monkeypatch):
    app, _, root = sandbox
    documents(app, root)
    analyst = login(app, 'analyst')
    with pytest.raises(PermissionError):
        app.knowledge.read_source_for_role('private_resume.txt', 'analyst')
    app.document_access.grant_local(app.knowledge, 'public_policy.txt', ['analyst'])
    monkeypatch.setattr(app.knowledge, 'source_texts', lambda: (_ for _ in ()).throw(
        AssertionError('Must not read all documents during export')))
    assert '12 annual leave' in app.knowledge.read_source_for_role('public_policy.txt', 'analyst')
