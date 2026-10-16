"""Threaded conversation history isolated by authenticated username."""
import sqlite3
import uuid
from .config import resolve_path


class History:
    def __init__(self, cfg):
        self.cfg = cfg
        self.path = resolve_path(cfg.get()['security']['history_file'])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, username TEXT NOT NULL, title TEXT NOT NULL, updated TEXT DEFAULT CURRENT_TIMESTAMP)')
            db.execute('CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL, username TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP)')
            db.execute('CREATE INDEX IF NOT EXISTS ix_threads_user ON threads(username,updated)')
            db.execute('CREATE INDEX IF NOT EXISTS ix_messages_thread ON messages(thread_id,id)')

    def _db(self):
        return sqlite3.connect(self.path, timeout=15)

    def threads(self, user):
        with self._db() as db:
            return [{'id': a, 'title': b} for a, b in db.execute('SELECT id,title FROM threads WHERE username=? ORDER BY updated DESC LIMIT 100', (user,))]

    def new(self, user):
        thread_id = str(uuid.uuid4())
        with self._db() as db:
            db.execute('INSERT INTO threads(id,username,title) VALUES (?,?,?)', (thread_id, user, 'New chat'))
        return thread_id

    def history(self, user, thread_id, limit=80):
        with self._db() as db:
            if not db.execute('SELECT 1 FROM threads WHERE username=? AND id=?', (user, thread_id)).fetchone():
                raise PermissionError('Conversation not found')
            rows = db.execute('SELECT role,content FROM (SELECT id,role,content FROM messages WHERE username=? AND thread_id=? ORDER BY id DESC LIMIT ?) ORDER BY id', (user, thread_id, limit)).fetchall()
            return [{'role': role, 'content': content} for role, content in rows]

    def history_page(self, user, thread_id, before_id=None, limit=100):
        """Return newest page in chronological order; cursor fetches older entries."""
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('Invalid history page size')
        if before_id is not None and (type(before_id) is not int or before_id < 1):
            raise ValueError('Invalid history cursor')
        with self._db() as db:
            if not db.execute('SELECT 1 FROM threads WHERE username=? AND id=?',
                              (user, thread_id)).fetchone():
                raise PermissionError('Conversation not found')
            clause = 'AND id < ?' if before_id is not None else ''
            params = (user, thread_id, before_id, limit + 1) if before_id is not None else (user, thread_id, limit + 1)
            rows = db.execute(
                'SELECT id,role,content FROM messages WHERE username=? AND thread_id=? '
                + clause + ' ORDER BY id DESC LIMIT ?', params).fetchall()
            more = len(rows) > limit
            page = rows[:limit]
            return {'messages': [{'id': mid, 'role': role, 'content': content}
                                 for mid, role, content in reversed(page)],
                    'has_more': more,
                    'next_before_id': page[-1][0] if more and page else None}

    def add(self, user, thread_id, question, answer):
        with self._db() as db:
            if not db.execute('SELECT 1 FROM threads WHERE username=? AND id=?', (user, thread_id)).fetchone():
                raise PermissionError('Conversation not found')
            db.execute('INSERT INTO messages(thread_id,username,role,content) VALUES (?,?,?,?)', (thread_id, user, 'user', question))
            db.execute('INSERT INTO messages(thread_id,username,role,content) VALUES (?,?,?,?)', (thread_id, user, 'assistant', answer))
            db.execute('UPDATE threads SET title=CASE WHEN title="New chat" THEN ? ELSE title END, updated=CURRENT_TIMESTAMP WHERE username=? AND id=?', (question[:60], user, thread_id))

    def delete(self, user, thread_id):
        with self._db() as db:
            if not db.execute('SELECT 1 FROM threads WHERE username=? AND id=?', (user, thread_id)).fetchone():
                raise PermissionError('Conversation not found')
            db.execute('DELETE FROM messages WHERE username=? AND thread_id=?', (user, thread_id))
            db.execute('DELETE FROM threads WHERE username=? AND id=?', (user, thread_id))
