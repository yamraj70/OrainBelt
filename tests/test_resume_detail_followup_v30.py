"""Resume detail questions must search content, not return filenames."""
from orion_db.server import resume_detail_followup_plan


def test_skills_followup_uses_rag():
    history = [{'role': 'user', 'content': 'How many new resumes do we have?'},
               {'role': 'assistant', 'content': 'There are 3 resume files.'}]
    plan = resume_detail_followup_plan('What is the skillset of candidates?', history)
    assert plan['tool'] == 'search_knowledge'
    assert 'skillset' in plan['arguments']['query']


def test_names_followup_then_skills():
    history = [{'role': 'user', 'content': 'How many resumes?'},
               {'role': 'assistant', 'content': '3'},
               {'role': 'user', 'content': 'What are candidate names?'},
               {'role': 'assistant', 'content': '3 files'}]
    assert resume_detail_followup_plan('What are their skills?', history)['tool'] == 'search_knowledge'


def test_unrelated_topic_does_not_reuse_resumes():
    history = [{'role': 'user', 'content': 'How many resumes?'},
               {'role': 'user', 'content': 'Tell me about politics'}]
    assert resume_detail_followup_plan('What are their skills?', history) is None


def test_no_context():
    assert resume_detail_followup_plan('What are candidate skills?', []) is None
