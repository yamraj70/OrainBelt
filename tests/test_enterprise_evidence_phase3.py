import pytest
from orion_db.agent import validate_plan


def test_phase3_plan_validation():
    assert validate_plan({'tool': 'analyze_documents', 'arguments': {
        'query': 'compare technical skills', 'document_type': 'resume'}})['tool'] == 'analyze_documents'
    for args in ({'query': ''}, {'query': 'skills', 'document_type': 'invalid'},
                 {'query': 'skills', 'source': '../../secret'}):
        with pytest.raises(ValueError):
            validate_plan({'tool': 'analyze_documents', 'arguments': args})


def test_phase3_authorized_cross_document_evidence(identity):
    app, auth, root = identity
    folder = root / 'knowledge'
    (folder / 'Candidate_Resume_A.txt').write_text(
        'Resume\nSkills: React, TypeScript, CSS\nExperience: Web development', encoding='utf-8')
    (folder / 'Candidate_Resume_B.txt').write_text(
        'Resume\nSkills: C#, WPF, PowerShell\nExperience: Windows applications', encoding='utf-8')
    app.knowledge.sync_once()
    role = 'admin'
    # Grant setup is fixture-dependent: use the same role ACL as production.
    allowed = app.rag.allowed_sources(role)
    output = app.rag.analyze_documents('skills and technologies', role, 'resume')
    assert set(output['sources']).issubset(allowed)
    assert all('Source:' in output['answer'] for _ in output['sources'])
    assert 'invented' not in output['answer'].lower()


def test_phase3_empty_acl_fails_closed(identity, monkeypatch):
    app, _, _ = identity
    monkeypatch.setattr(app.rag, 'allowed_sources', lambda role: set())
    result = app.rag.analyze_documents('skills', 'employee', 'resume')
    assert result['sources'] == []
    assert result['evidence'] == []


def test_phase3_no_unrestricted_knowledge_scan(identity):
    app, _, _ = identity
    with pytest.raises(ValueError):
        app.knowledge.document_evidence('skills', None)
