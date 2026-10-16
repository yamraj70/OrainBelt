"""Phase 1: keep planner context bounded, isolated, and non-authoritative."""
from orion_db.conversation_context import planner_context, planner_instructions


def test_only_recent_user_requests_are_sent_to_planner():
    history = [{'role': 'user', 'content': f'question {i}'} for i in range(8)]
    history.append({'role': 'assistant', 'content': 'private answer'})
    assert planner_context(history) == [f'question {i}' for i in range(4, 8)]
    assert 'private answer' not in planner_instructions('base', history)


def test_topic_change_remains_visible():
    history = [{'role': 'user', 'content': 'List new resumes'},
               {'role': 'assistant', 'content': '3'},
               {'role': 'user', 'content': 'Now discuss IT policies'}]
    instructions = planner_instructions('base', history)
    assert instructions.index('List new resumes') < instructions.index('Now discuss IT policies')
    assert 'topic change' in instructions.lower()


def test_empty_context_preserves_original_instructions():
    assert planner_instructions('base', []) == 'base'


def test_context_does_not_include_assistant_response():
    history = [{'role': 'user', 'content': 'How many invoices?'},
               {'role': 'assistant', 'content': 'Sensitive internal invoice detail'}]
    assert 'Sensitive internal' not in planner_instructions('base', history)


def test_context_bounded():
    history = [{'role': 'user', 'content': 'x' * 9000}]
    assert len(planner_context(history)[0]) == 1200
