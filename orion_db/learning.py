"""Audited, human-approved knowledge corrections. No autonomous weight updates."""
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from .config import resolve_path
from .knowledge import redact_secrets

SECRET_HINTS = re.compile(r'(?i)\b(password|passwd|pwd|secret|api[_ -]?key|access[_ -]?token|connection string|private key|passwordhash)\b')
CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


class Learning:
    def __init__(self, cfg, audit, knowledge, discovery):
        self.cfg, self.audit, self.knowledge, self.discovery = cfg, audit, knowledge, discovery
        self._lock = threading.RLock()
        self.path = resolve_path(cfg.get().get('learning', {}).get('feedback_file', 'data/pending_knowledge_feedback.sqlite')).with_suffix('.sqlite')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS feedback
                (id TEXT PRIMARY KEY, question TEXT NOT NULL, answer TEXT NOT NULL,
                 submitted_by TEXT NOT NULL, submitted_at TEXT NOT NULL,
                 status TEXT NOT NULL, reviewed_by TEXT, reviewed_at TEXT, source_file TEXT)''')

    def _db(self):
        return sqlite3.connect(self.path, timeout=20)

    def _clean(self, text, limit):
        if not isinstance(text, str):
            raise ValueError('Feedback must be text')
        value = CONTROL.sub('', text.strip())
        if not 4 <= len(value) <= limit:
            raise ValueError('Feedback text length is invalid')
        if SECRET_HINTS.search(value) or redact_secrets(value) != value:
            raise ValueError('Feedback cannot contain credentials or secret-related fields')
        return value

    def submit(self, username, question, answer):
        if not self.cfg.get().get('learning', {}).get('enabled', True):
            raise PermissionError('Learning feedback is disabled')
        question = self._clean(question, 500)
        answer = self._clean(answer, 1500)
        feedback_id = uuid.uuid4().hex
        with self._lock, self._db() as db:
            db.execute('INSERT INTO feedback(id,question,answer,submitted_by,submitted_at,status) VALUES (?,?,?,?,?,?)',
                       (feedback_id, question, answer, username, datetime.now(timezone.utc).isoformat(), 'pending'))
        self.audit.event('learning_feedback_submitted', feedback_id=feedback_id, submitted_by=username)
        return {'type': 'learning_feedback_submitted', 'id': feedback_id,
                'status': 'pending_review', 'message': 'Correction submitted for administrator review.'}

    def pending(self, role, limit=50):
        if role != 'admin':
            raise PermissionError('Only administrators may review corrections')
        with self._db() as db:
            rows = db.execute('SELECT id,question,answer,submitted_by,submitted_at FROM feedback WHERE status=? ORDER BY submitted_at LIMIT ?',
                              ('pending', min(100, max(1, int(limit))))).fetchall()
        return {'type': 'learning_feedback_pending',
                'items': [dict(zip(('id', 'question', 'answer', 'submitted_by', 'submitted_at'), r)) for r in rows]}

    def review(self, role, username, feedback_id, approve):
        if role != 'admin':
            raise PermissionError('Only administrators may approve corrections')
        if not isinstance(feedback_id, str) or not re.fullmatch(r'[a-f0-9]{32}', feedback_id):
            raise ValueError('Invalid feedback ID')
        with self._lock:
            with self._db() as db:
                row = db.execute('SELECT question,answer,status FROM feedback WHERE id=?', (feedback_id,)).fetchone()
                if not row or row[2] != 'pending':
                    raise ValueError('Feedback not found or already reviewed')
                source_file = ''
                if approve:
                    folder = self.knowledge._folder() / 'Approved_Feedback'
                    folder.mkdir(parents=True, exist_ok=True)
                    target = folder / (feedback_id + '.md')
                    content = '# Approved internal knowledge correction\n\n' + \
                        'Question: ' + row[0].replace('\n', ' ') + '\n\n' + \
                        'Answer: ' + row[1].replace('\n', ' ') + '\n'
                    with target.open('x', encoding='utf-8') as f:
                        f.write(content)
                    source_file = 'Approved_Feedback/' + target.name
                db.execute('UPDATE feedback SET status=?, reviewed_by=?, reviewed_at=?, source_file=? WHERE id=?',
                           ('approved' if approve else 'rejected', username,
                            datetime.now(timezone.utc).isoformat(), source_file, feedback_id))
            if approve:
                status = self.discovery.refresh(documents=True, schemas=False)
                if status.get('status') == 'error':
                    self.audit.event('learning_feedback_index_delayed', feedback_id=feedback_id)
            self.audit.event('learning_feedback_reviewed', feedback_id=feedback_id,
                             reviewed_by=username, approved=bool(approve))
            return {'type': 'learning_feedback_reviewed', 'id': feedback_id,
                    'status': 'approved' if approve else 'rejected', 'source': source_file}
