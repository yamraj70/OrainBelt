"""Deterministic resume inventory, dynamic discovery and privacy boundaries."""
import pytest


def _chat(app, auth, message):
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    return app.process({**auth, 'type': 'chat', 'thread_id': tid, 'message': message})


def test_resume_root_txt_and_nested_md(identity):
    app, auth, root = identity
    folder = root / 'knowledge'
    (folder / 'candidate_a.txt').write_text('Resume\nWork Experience\nEducation\n', encoding='utf-8')
    (folder / 'Resumes').mkdir()
    (folder / 'Resumes' / 'candidate_b.md').write_text('Candidate summary', encoding='utf-8')
    assert app.knowledge.sync_once() == 'updated'
    answer = _chat(app, auth, 'how many resumes do we have?')
    assert answer['document_count'] == 2
    assert answer['response_json']['document_count'] == 2
    assert answer['response_json']['grounding'] == 'file_inventory'
    assert '2 resume files' in answer['answer']
    names = _chat(app, auth, 'list resumes')
    assert 'candidate_a.txt' in names['answer']
    assert 'candidate_b.md' in names['answer']
    (folder / 'candidate_a.txt').unlink()
    assert app.knowledge.sync_once() == 'updated'
    assert _chat(app, auth, 'count resumes')['document_count'] == 1


def test_resume_document_access_restricted(sandbox):
    app, _, root = sandbox
    folder = root / 'knowledge' / 'Resumes'
    folder.mkdir()
    (folder / 'secret.txt').write_text('Resume\nExperience: private candidate history', encoding='utf-8')
    app.knowledge.sync_once()
    app.auth.create_user('viewer2', 'CorrectHorseBattery42', 'viewer')
    login = app.process({'type': 'login', 'client_id': 'viewer2', 'username': 'viewer2', 'password': 'CorrectHorseBattery42'})
    auth = {'client_id': 'viewer2', 'token': login['token']}
    answer = _chat(app, auth, 'how many resumes do we have?')
    assert answer['document_count'] == 0
    result = _chat(app, auth, 'private candidate history')
    assert 'private candidate history' not in result['answer']


def test_pdf_and_docx_resumes(identity, tmp_path):
    docx = pytest.importorskip('docx')
    pytest.importorskip('pypdf')
    reportlab = pytest.importorskip('reportlab')
    from reportlab.pdfgen import canvas
    app, auth, root = identity
    folder = root / 'knowledge'
    pdf_path = folder / 'cv_person.pdf'
    c = canvas.Canvas(str(pdf_path))
    c.drawString(70, 720, 'Resume')
    c.drawString(70, 700, 'Work Experience')
    c.save()
    d = docx.Document()
    d.add_heading('Curriculum Vitae')
    d.add_paragraph('Education and Work Experience')
    d.save(folder / 'person.docx')
    assert app.knowledge.sync_once() == 'updated'
    assert _chat(app, auth, 'how many resumes do we have?')['document_count'] == 2
