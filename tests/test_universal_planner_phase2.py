"""Phase 2: capability planner, contextual routing and safety boundaries."""
import pytest
from orion_db.agent import PLANNER_INSTRUCTIONS, TOOL_CAPABILITIES, TOOLS, validate_plan


def test_capabilities_match_executable_allowlist():
    assert set(TOOL_CAPABILITIES) == set(TOOLS)
    for tool in TOOLS:
        assert tool in PLANNER_INSTRUCTIONS


def test_internal_document_content_has_distinct_tool():
    assert 'document CONTENT' in PLANNER_INSTRUCTIONS
    assert 'search_knowledge' in PLANNER_INSTRUCTIONS
    assert 'not list_documents' in PLANNER_INSTRUCTIONS


def test_planner_cannot_expand_tool_permissions():
    with pytest.raises(ValueError):
        validate_plan({'tool': 'search_knowledge', 'arguments': {'query': 'skills', 'role': 'admin'}})
    with pytest.raises(ValueError):
        validate_plan({'tool': 'execute_sql', 'arguments': {}})


def test_resume_content_followup_uses_planner_when_enabled(identity, monkeypatch):
    app, auth, root = identity
    opts = app.cfg.get()
    opts['agent']['enabled'] = True
    opts['features']['local_model'] = True
    opts['model']['enabled'] = True
    calls = []

    def plan(question, instructions, options):
        calls.append((question, instructions))
        return {'tool': 'search_knowledge', 'arguments': {'query': 'resume technical skills'}}

    monkeypatch.setattr(app.model, 'plan_action', plan)
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    app.process({**auth, 'type': 'chat', 'thread_id': tid, 'message': 'How many resumes do we have?'})
    result = app.process({**auth, 'type': 'chat', 'thread_id': tid,
                          'message': 'What tech stacks do these candidates have?'})
    assert calls and 'How many resumes' in calls[-1][1]
    assert result['response_json']['tool'] == 'search_knowledge'


def test_unauthorized_plan_still_denied_in_demo(identity, monkeypatch):
    # Validation does not create new authority even for a syntactically valid plan.
    assert validate_plan({'tool': 'preview_table', 'arguments': {'table': 'assets'}})['tool'] == 'preview_table'
