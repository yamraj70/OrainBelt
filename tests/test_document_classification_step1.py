"""ORION v2.7 changeset 1: metadata-only Qwen3 document classification."""
import json
from pathlib import Path

from orion_db.classifier import validate_classification


def _result(department='hr', kind='resume', sensitivity='confidential', roles=None):
    return {'department': department, 'document_type': kind, 'sensitivity': sensitivity,
            'suggested_roles': roles if roles is not None else ['admin', 'hr']}


def test_missing_local_model_keeps_all_files_pending(sandbox):
    app, cfg, root = sandbox
    status = app.knowledge.classifier.status()
    assert status['enabled'] is True
    assert status['pending'] == 1
    assert status['classified'] == 0
    assert status['access_enforcement_implemented'] is True
    registry = json.loads((root / 'classifications.json').read_text())
    assert registry['documents']['catalog.txt']['approval_status'] == 'pending'


def test_classifies_root_resume_and_reuses_cached_result(sandbox, monkeypatch):
    app, cfg, root = sandbox
    calls = []
    monkeypatch.setattr(app.model, 'status', lambda: {'enabled': True, 'exists': True, 'backend': 'llama_cpp'})
    def plan(question, instructions, agent_settings):
        calls.append(question)
        return _result()
    monkeypatch.setattr(app.model, 'plan_action', plan)
    # No PDF dependency needed: TXT file directly in root is supported.
    resume = root / 'knowledge' / 'candidate.txt'
    resume.write_text('Resume\nWork experience: Python developer\nEducation: BSc', encoding='utf-8')
    assert app.knowledge.sync_once() == 'updated'
    assert app.knowledge.classifier.status()['classified'] == 2
    assert len(calls) == 2
    assert app.knowledge.sync_once() == 'unchanged'
    assert len(calls) == 2
    assert app.knowledge.classifier.status()['approval_required'] is True


def test_modified_files_reclassified_and_deleted_removed(sandbox, monkeypatch):
    app, cfg, root = sandbox
    monkeypatch.setattr(app.model, 'status', lambda: {'enabled': True, 'exists': True, 'backend': 'llama_cpp'})
    calls = []
    def plan(question, instructions, agent_settings):
        calls.append(question)
        return _result('general', 'policy', 'internal', ['employee', 'admin'])
    monkeypatch.setattr(app.model, 'plan_action', plan)
    assert app.knowledge.sync_once() == 'unchanged'
    assert len(calls) == 1
    (root / 'knowledge' / 'catalog.txt').write_text('Updated company policy.', encoding='utf-8')
    assert app.knowledge.sync_once() == 'updated'
    assert len(calls) == 2
    (root / 'knowledge' / 'catalog.txt').unlink()
    assert app.knowledge.sync_once() == 'updated'
    assert app.knowledge.classifier.status()['files'] == 0


def test_rejects_untrusted_roles_and_conservative_resume_policy():
    from pytest import raises
    with raises(ValueError):
        validate_classification(_result(roles=['admin', 'superuser']))
    with raises(ValueError):
        validate_classification({'department': 'hr', 'document_type': 'resume',
                                 'sensitivity': 'confidential', 'suggested_roles': 'admin'})
    result = validate_classification(_result(sensitivity='public', roles=['employee', 'admin']))
    assert result['sensitivity'] == 'confidential'
    assert result['suggested_roles'] == ['admin']


def test_model_failure_does_not_break_knowledge_sync(sandbox, monkeypatch):
    app, cfg, root = sandbox
    monkeypatch.setattr(app.model, 'status', lambda: {'enabled': True, 'exists': True, 'backend': 'llama_cpp'})
    def fail(*args):
        raise RuntimeError('Model unavailable')
    monkeypatch.setattr(app.model, 'plan_action', fail)
    assert app.knowledge.sync_once() == 'unchanged'
    assert app.knowledge.classifier.status()['pending'] == 1
    assert app.knowledge.status()['status'] == 'unchanged'
