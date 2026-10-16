import json
import pytest
from orion_db.agent import validate_plan
from orion_db.database import LiveDB
from orion_db.response import format_chat
from test_application import create_demo_db


def test_reject_unsafe_plans():
    for raw in [
        {'tool': 'run_shell', 'arguments': {'command': 'echo hi'}},
        {'tool': 'preview_table', 'arguments': {'table': 'assets', 'sql': 'DELETE FROM assets'}},
        {'tool': 'count_documents', 'arguments': {'document_type': 'unrestricted'}},
        {'tool': 'preview_table', 'arguments': {'limit': 1000000}},
        {'tool': 'search_knowledge', 'arguments': {'query': ''}},
        {'tool': 'list_documents', 'arguments': {'extension': '.exe'}},
    ]:
        with pytest.raises(ValueError):
            validate_plan(raw)


def test_count_documents_through_agent(identity, monkeypatch):
    app, auth, root = identity
    (root / 'knowledge' / 'Candidate_Resume.txt').write_text(
        'Resume\nProfessional experience\nEducation\nSkills: Python', encoding='utf-8')
    app.discovery.refresh(documents=True, schemas=False)
    settings = app.cfg.get()
    settings['model']['enabled'] = True
    settings['features']['local_model'] = True
    monkeypatch.setattr(app.model, 'plan_action', lambda *args: {
        'tool': 'count_documents', 'arguments': {'document_type': 'resume'}})
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    answer = app.process({**auth, 'type': 'chat', 'thread_id': tid,
                          'message': 'How many resumes do we have?'})
    assert answer['document_count'] == 1
    assert answer['response_json']['document_count'] == 1
    assert answer['response_json']['tool'] == 'count_documents'
    assert answer['response_json']['grounding'] == 'file_inventory'


def test_agent_document_answer_is_grounded(identity, monkeypatch):
    app, auth, root = identity
    settings = app.cfg.get()
    settings['model']['enabled'] = True
    settings['features']['local_model'] = True
    monkeypatch.setattr(app.model, 'plan_action', lambda *args: {
        'tool': 'search_knowledge', 'arguments': {'query': 'monthly finance reporting'}})
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    answer = app.process({**auth, 'type': 'chat', 'thread_id': tid,
                          'message': 'What do our finance documents say?'})
    assert answer['response_json']['tool'] == 'search_knowledge'
    assert 'catalog.txt' in answer['sources']


def test_agent_database_inventory(identity, monkeypatch):
    app, auth, root = identity
    settings = app.cfg.get()
    settings['model']['enabled'] = True
    settings['features']['local_model'] = True
    monkeypatch.setattr(app.model, 'plan_action', lambda *args: {
        'tool': 'list_databases', 'arguments': {}})
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    answer = app.process({**auth, 'type': 'chat', 'thread_id': tid,
                          'message': 'List databases'})
    assert answer['database_count'] == 1
    assert 'finance_reporting' in answer['databases']


def test_agent_live_preview_verified_and_redacted(identity, monkeypatch):
    app, auth, root = identity
    path = create_demo_db(root / 'demo.sqlite')
    app.process({**auth, 'type': 'db_connect', 'backend': 'sqlite',
                 'database': str(path), 'confirmed': True})
    settings = app.cfg.get()
    settings['model']['enabled'] = True
    settings['features']['local_model'] = True
    monkeypatch.setattr(app.model, 'plan_action', lambda *args: {
        'tool': 'preview_table', 'arguments': {'table': 'assets', 'schema': 'main', 'limit': 2}})
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    answer = app.process({**auth, 'type': 'chat', 'thread_id': tid,
                          'message': 'Show the first two assets'})
    assert answer['query_result']['count'] == 2
    assert answer['response_json']['query_result']['sql'].startswith('SELECT')
    assert 'DROP' not in answer['response_json']['query_result']['sql']


def test_agent_invalid_plan_falls_back_without_execution(identity, monkeypatch):
    app, auth, root = identity
    settings = app.cfg.get()
    settings['model']['enabled'] = True
    settings['features']['local_model'] = True
    monkeypatch.setattr(app.model, 'plan_action', lambda *args: {
        'tool': 'execute_sql', 'arguments': {'sql': 'DROP TABLE assets'}})
    monkeypatch.setattr(app.model, 'answer_general', lambda *args: 'A database stores structured information.')
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    answer = app.process({**auth, 'type': 'chat', 'thread_id': tid,
                          'message': 'What is a database?'})
    assert answer['type'] == 'answer'
    assert answer['answer'].startswith('A database stores')


def test_json_response_format(identity, monkeypatch):
    app, auth, root = identity
    settings = app.cfg.get()
    settings['model']['enabled'] = True
    settings['features']['local_model'] = True
    monkeypatch.setattr(app.model, 'plan_action', lambda *args: {
        'tool': 'list_databases', 'arguments': {}})
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    answer = app.process({**auth, 'type': 'chat', 'thread_id': tid,
                          'message': 'List databases as JSON'})
    assert answer['presentation'] == 'json'
    json.dumps(answer['response_json'])


def test_semantic_fallback_lexical(identity, monkeypatch):
    app, auth, root = identity
    app.cfg.get()['rag']['retrieval'] = 'hybrid'
    monkeypatch.setattr(app.knowledge._semantic, 'search', lambda *args: (_ for _ in ()).throw(FileNotFoundError()))
    hits = app.knowledge.search('monthly finance reporting')
    assert hits and hits[0]['source'] == 'catalog.txt'


def test_preview_omits_password_columns(identity):
    app, auth, root = identity
    import sqlite3
    path = root / 'sensitive.sqlite'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE customers (name TEXT, passwordhash TEXT)')
        conn.execute('INSERT INTO customers VALUES (?,?)', ('Customer', 'TOP_SECRET'))
    app.process({**auth, 'type': 'db_connect', 'backend': 'sqlite',
                 'database': str(path), 'confirmed': True})
    rows = app.process({**auth, 'type': 'db_preview', 'schema': 'main', 'table': 'customers', 'limit': 2})
    assert rows['columns'] == ['name']
    assert 'TOP_SECRET' not in json.dumps(rows)


def test_agent_connect_only_approved_profile(identity, monkeypatch):
    app, auth, root = identity
    path = create_demo_db(root / 'approved.sqlite')
    settings = app.cfg.get()
    settings['database']['profiles'] = [{'alias': 'approved_local', 'database': str(path), 'backend': 'sqlite'}]
    settings['model']['enabled'] = True
    settings['features']['local_model'] = True
    monkeypatch.setattr(app.model, 'plan_action', lambda *args: {
        'tool': 'connect_database', 'arguments': {'alias': 'approved_local'}})
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    answer = app.process({**auth, 'type': 'chat', 'thread_id': tid,
                          'message': 'Connect to approved_local'})
    assert 'Connected to' in answer['answer']
    assert app.live.status(auth['token'])['connected'] is True
    app.live.close(auth['token'])
    monkeypatch.setattr(app.model, 'plan_action', lambda *args: {
        'tool': 'connect_database', 'arguments': {'alias': 'not_approved'}})
    answer = app.process({**auth, 'type': 'chat', 'thread_id': tid,
                          'message': 'Connect to not_approved'})
    assert answer['type'] == 'db_connect_required'
    assert app.live.status(auth['token'])['connected'] is False


def test_agent_viewer_cannot_count_resumes(sandbox, monkeypatch):
    app, cfg, root = sandbox
    (root / 'knowledge' / 'Candidate_Resume.txt').write_text('Resume\nEducation\nExperience', encoding='utf-8')
    app.discovery.refresh(documents=True, schemas=False)
    app.auth.create_user('viewer_agent', 'CorrectHorseBattery42', 'viewer')
    login = app.process({'type': 'login', 'client_id': 'viewer_agent',
                         'username': 'viewer_agent', 'password': 'CorrectHorseBattery42'})
    settings = app.cfg.get()
    settings['model']['enabled'] = True
    settings['features']['local_model'] = True
    monkeypatch.setattr(app.model, 'plan_action', lambda *args: {
        'tool': 'count_documents', 'arguments': {'document_type': 'resume'}})
    tid = app.process({'type': 'new_chat', 'client_id': 'viewer_agent', 'token': login['token']})['thread_id']
    result = app.process({'type': 'chat', 'client_id': 'viewer_agent', 'token': login['token'],
                          'thread_id': tid, 'message': 'How many resumes?'})
    assert result['document_count'] == 0
