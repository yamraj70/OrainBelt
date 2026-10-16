"""Follow-up routing must retain the previous thread's resume context."""
from orion_db.server import resume_followup_plan


def test_resume_count_followed_by_names():
    prior = [{'role': 'user', 'content': 'How many new resumes do we have?'},
             {'role': 'assistant', 'content': 'We have new resumes.'}]
    assert resume_followup_plan('What are the candidate names?', prior) == {
        'tool': 'list_documents', 'arguments': {'document_type': 'resume'}}


def test_today_resumes_followed_by_names():
    prior = [{'role': 'user', 'content': 'How many new resumes today?'}]
    assert resume_followup_plan('candidate names?', prior) == {
        'tool': 'recent_documents', 'arguments': {'document_type': 'resume', 'activity': 'created'}}


def test_no_context_or_other_topic():
    assert resume_followup_plan('What are the candidate names?', []) is None
    assert resume_followup_plan('What are the candidate names?', [
        {'role': 'user', 'content': 'Tell me about elections'}]) is None
