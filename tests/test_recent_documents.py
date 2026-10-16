"""Recent-file discovery is grounded in synced timestamps, not Qwen3."""
import os
import time
from datetime import datetime

import pytest

from orion_db.agent import validate_plan
from orion_db.server import recent_document_plan


def test_today_root_resume_and_document_without_qwen(identity, monkeypatch):
    app, auth, root = identity
    knowledge = root / 'knowledge'
    (knowledge / 'Candidate_Resume.txt').write_text(
        'Resume\nProfessional Experience\nEducation\nSkills', encoding='utf-8')
    (knowledge / 'policy.md').write_text('Company handbook.', encoding='utf-8')
    app.discovery.refresh(documents=True, schemas=False)
    # Planner must not be called for a date-based inventory question.
    monkeypatch.setattr(app.model, 'plan_action', lambda *a: (_ for _ in ()).throw(AssertionError('Qwen3 used')))
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    result = app.process({**auth, 'type': 'chat', 'thread_id': tid,
                          'message': 'Do we have any new candidate resume or document today?'})
    assert result['document_count'] == 3  # includes existing catalog.txt
    assert 'Candidate_Resume.txt' in result['files']
    assert 'policy.md' in result['files']
    assert result['date'] == datetime.now().astimezone().date().isoformat()
    assert result['response_json']['tool'] == 'recent_documents'
    assert result['response_json']['date'] == result['date']
    assert 'Candidate_Resume.txt' in result['response_json']['files']


def test_today_resume_only_and_modified_only(identity):
    app, auth, root = identity
    file = root / 'knowledge' / 'candidate_resume.txt'
    file.write_text('Professional experience\nEducation', encoding='utf-8')
    app.discovery.refresh(documents=True, schemas=False)
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    result = app.process({**auth, 'type': 'chat', 'thread_id': tid,
                          'message': 'Any new candidate resumes today?'})
    assert result['document_count'] == 1
    assert result['files'] == ['candidate_resume.txt']
    yesterday = time.time() - 86400 * 2
    os.utime(file, (yesterday, yesterday))
    app.discovery.refresh(documents=True, schemas=False)
    result = app.process({**auth, 'type': 'chat', 'thread_id': tid,
                          'message': 'Which candidate resumes were modified today?'})
    assert result['document_count'] == 0
    assert result['activity'] == 'modified'


def test_planner_recent_tool(identity, monkeypatch):
    app, auth, root = identity
    app.cfg.get()['features']['local_model'] = True
    app.cfg.get()['model']['enabled'] = True
    monkeypatch.setattr(app.model, 'plan_action', lambda *args: {
        'tool': 'recent_documents', 'arguments': {'document_type': 'document', 'activity': 'created'}})
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    result = app.process({**auth, 'type': 'chat', 'thread_id': tid,
                          'message': 'Give me an inventory of recent company documents'})
    assert result['response_json']['tool'] == 'recent_documents'
    assert result['document_count'] == 1


def test_recent_plan_validation_and_role(sandbox):
    app, cfg, root = sandbox
    for plan in [
        {'tool': 'recent_documents', 'arguments': {'document_type': 'all', 'activity': 'delete'}},
        {'tool': 'recent_documents', 'arguments': {'document_type': 'all', 'path': '../../'}},
        {'tool': 'recent_documents', 'arguments': {'document_type': 'secret'}},
    ]:
        with pytest.raises(ValueError):
            validate_plan(plan)
    app.auth.create_user('viewer_recent', 'CorrectHorseBattery42', 'viewer')
    login = app.process({'type': 'login', 'client_id': 'viewer_recent',
                         'username': 'viewer_recent', 'password': 'CorrectHorseBattery42'})
    auth = {'client_id': 'viewer_recent', 'token': login['token']}
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    result = app.process({**auth, 'type': 'chat', 'thread_id': tid,
                          'message': 'Any new candidate resumes today?'})
    assert result['document_count'] == 0
    assert result['files'] == []


def test_file_date_metadata_persisted_and_unchanged(identity):
    app, auth, root = identity
    index = app.knowledge._index
    assert index['version'] >= 5
    assert all('created_at' in f and 'modified_at' in f for f in index['snapshot'])
    assert app.knowledge.sync_once() == 'unchanged'
    assert recent_document_plan('What is the company policy?') is None


def test_recent_query_detects_new_file_without_manual_sync(identity):
    app, auth, root = identity
    (root / 'knowledge' / 'New_Resume.md').write_text(
        'Professional experience\nEducation\nSkills', encoding='utf-8')
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    result = app.process({**auth, 'type': 'chat', 'thread_id': tid,
                          'message': 'Do we have any new resumes today?'})
    assert result['document_count'] == 1
    assert result['files'] == ['New_Resume.md']


def test_recent_query_reports_sync_failure_not_false_zero(identity, monkeypatch):
    app, auth, root = identity
    monkeypatch.setattr(app.knowledge, 'sync_once', lambda: 'error')
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    result = app.process({**auth, 'type': 'chat', 'thread_id': tid,
                          'message': 'Any new resumes today?'})
    assert 'sync failed' in result['answer']
    assert 'document_count' not in result
