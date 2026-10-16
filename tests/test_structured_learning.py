"""v2.4: concise JSON-first response and approved continuous learning."""
import json
import yaml
import pytest


def ask(app, auth, text):
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    return app.process({**auth, 'type': 'chat', 'thread_id': tid, 'message': text}), tid


def test_short_json_document_answer(identity):
    app, auth, _ = identity
    result, tid = ask(app, auth, 'monthly finance reporting')
    payload = result['response_json']
    assert payload['schema_version'] == '1.0'
    assert payload['intent'] == 'company'
    assert payload['grounding'] == 'document'
    assert payload['sources'] == ['catalog.txt']
    assert payload['answer'] == result['answer']
    assert len(result['answer'].split()) <= 60
    assert len(result['answer'].splitlines()) <= 2
    assert json.loads(json.dumps(result))['response_json'] == payload
    assert app.history.history('tester', tid)[-1]['content'] == result['answer']


def test_json_presentation_requested(identity):
    app, auth, _ = identity
    result, _ = ask(app, auth, 'monthly finance reporting in JSON format')
    assert result['presentation'] == 'json'
    assert isinstance(result['response_json'], dict)


def test_general_question_not_searched_in_txt(identity, monkeypatch):
    app, auth, _ = identity
    def forbidden_search(*a, **kw):
        raise AssertionError('General question incorrectly queried TXT')
    monkeypatch.setattr(app.knowledge, 'search', forbidden_search)
    result, _ = ask(app, auth, 'what is a database?')
    assert result['response_json']['intent'] == 'general'
    assert result['response_json']['sources'] == []


def test_long_document_not_dumped(identity):
    app, auth, root = identity
    (root/'knowledge'/'long.md').write_text('Company escalation process: ' +
       ' '.join('Procedure' for _ in range(200)) + '\n', encoding='utf-8')
    app.discovery.refresh(documents=True, schemas=False)
    result, _ = ask(app, auth, 'our company escalation process')
    assert len(result['answer'].split()) <= 60


def test_feedback_requires_review_and_becomes_searchable(identity):
    app, auth, root = identity
    question = 'What is our approved service escalation code?'
    before, _ = ask(app, auth, question)
    assert 'Phoenix 42' not in before['answer']
    submission = app.process({**auth, 'type': 'learning_feedback_submit',
                              'question': question, 'answer': 'Phoenix 42 is the approved service escalation code.'})
    assert submission['status'] == 'pending_review'
    still, _ = ask(app, auth, question)
    assert 'Phoenix 42' not in still['answer']
    pending = app.process({**auth, 'type': 'learning_feedback_pending'})
    assert len(pending['items']) == 1
    reviewed = app.process({**auth, 'type': 'learning_feedback_review',
                            'id': submission['id'], 'approve': True})
    assert reviewed['status'] == 'approved'
    after, _ = ask(app, auth, question)
    assert 'Phoenix 42' in after['answer']
    assert after['response_json']['sources'] == [reviewed['source']]
    assert (root/'knowledge'/reviewed['source']).is_file()
    assert app.process({**auth, 'type': 'learning_feedback_pending'})['items'] == []


def test_rejected_feedback_not_learned(identity):
    app, auth, root = identity
    submission = app.process({**auth, 'type': 'learning_feedback_submit',
                              'question': 'Our office orange badge rule', 'answer': 'Always wear an orange badge.'})
    app.process({**auth, 'type': 'learning_feedback_review', 'id': submission['id'], 'approve': False})
    assert not list((root/'knowledge').rglob(submission['id'] + '.md'))


def test_feedback_rejects_credentials_and_requires_admin(sandbox):
    app, _, _ = sandbox
    app.auth.create_user('analyst1', 'CorrectHorseBattery42', 'analyst')
    login = app.process({'type': 'login', 'client_id': 'analyst-client',
                         'username': 'analyst1', 'password': 'CorrectHorseBattery42'})
    auth = {'client_id': 'analyst-client', 'token': login['token']}
    with pytest.raises(ValueError):
        app.process({**auth, 'type': 'learning_feedback_submit', 'question': 'Database password',
                     'answer': 'password=MyPrivatePassword'})
    submission = app.process({**auth, 'type': 'learning_feedback_submit',
                              'question': 'Company visitor registration policy',
                              'answer': 'Visitors sign in at reception.'})
    with pytest.raises(PermissionError):
        app.process({**auth, 'type': 'learning_feedback_pending'})
    with pytest.raises(PermissionError):
        app.process({**auth, 'type': 'learning_feedback_review', 'id': submission['id'], 'approve': True})


def test_configured_hourly_discovery(identity):
    app, auth, _ = identity
    cfg = app.cfg.get()
    assert cfg['knowledge']['sync_interval_seconds'] == 3600
    assert cfg['database']['schema_discovery']['interval_seconds'] == 3600
    assert cfg['knowledge']['answer_mode'] == 'extractive'
    assert cfg['learning']['model_weight_training'] is False
