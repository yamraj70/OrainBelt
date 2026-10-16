"""Employee sign-in; scrypt password hashes, in-memory expiring tokens."""
import hashlib
import ipaddress
import hmac
import secrets
import sqlite3
import threading
import time
from .config import resolve_path

ROLES = {'viewer', 'employee', 'analyst', 'hr', 'manager', 'finance', 'admin'}
DEMO_ALLOWED_ROLES = frozenset({'viewer', 'employee'})


def is_loopback(address):
    """Check the actual TCP peer IP; never trust a client-supplied header."""
    try:
        return ipaddress.ip_address(str(address)).is_loopback
    except ValueError:
        return False


def authentication_mode(cfg):
    mode = cfg.get().get('security', {}).get('authentication', {}).get('mode', 'required')
    if mode not in ('required', 'local_demo'):
        raise ValueError('security.authentication.mode must be required or local_demo')
    return mode


def validate_demo_configuration(cfg):
    """Fail closed on insecure demo settings, even if server code is called directly."""
    mode = authentication_mode(cfg)
    if mode != 'local_demo':
        return False
    settings = cfg.get()
    demo = settings['security'].get('authentication', {}).get('local_demo', {})
    host = str(settings['server'].get('host', '')).strip().lower()
    if host not in ('127.0.0.1', '::1', 'localhost'):
        raise PermissionError('Local demo requires server.host to be loopback only')
    if not demo.get('loopback_only', True):
        raise PermissionError('Local demo cannot disable loopback_only')
    if demo.get('role', 'employee') not in DEMO_ALLOWED_ROLES:
        raise PermissionError('Local demo role must be employee or viewer')
    for option in ('allow_admin_actions', 'allow_live_database', 'allow_document_exports'):
        if demo.get(option, False) is not False:
            raise PermissionError('Local demo cannot enable ' + option)
    username = demo.get('username', 'demo')
    if not isinstance(username, str) or not (1 <= len(username) <= 32) or not all(
            c.isalnum() or c in '_.-' for c in username):
        raise ValueError('Invalid local demo display username')
    return True


def password_hash(password, salt):
    return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2**14, r=8, p=1, dklen=32)


class Auth:
    def __init__(self, cfg):
        self.cfg = cfg
        self.path = resolve_path(cfg.get()['security']['users_file'])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._sessions = {}
        self._attempts = {}
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, salt BLOB NOT NULL, digest BLOB NOT NULL, role TEXT NOT NULL)')

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def user_count(self):
        with self._connect() as db:
            return db.execute('SELECT COUNT(*) FROM users').fetchone()[0]

    def create_user(self, username, password, role='analyst'):
        if not isinstance(username, str) or not (3 <= len(username) <= 64) or not all(x.isalnum() or x in '_.-' for x in username):
            raise ValueError('Username must be 3–64 letters, digits, dots, underscores or hyphens')
        if role not in ROLES:
            raise ValueError('Invalid role')
        if len(password) < 10:
            raise ValueError('Password must be at least 10 characters')
        salt = secrets.token_bytes(16)
        with self._connect() as db:
            db.execute('INSERT INTO users(username,salt,digest,role) VALUES (?,?,?,?)', (username.lower(), salt, password_hash(password, salt), role))

    def set_role(self, username, role):
        """Trusted local CLI only; existing sessions are invalidated."""
        if role not in ROLES:
            raise ValueError('Invalid role')
        name = str(username).lower()
        with self._lock:
            with self._connect() as db:
                existing = db.execute('SELECT role FROM users WHERE username=?', (name,)).fetchone()
                if existing is None:
                    raise ValueError('User not found')
                if existing[0] == 'admin' and role != 'admin':
                    admins = db.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
                    if admins <= 1:
                        raise PermissionError('Cannot remove the last administrator')
                db.execute('UPDATE users SET role=? WHERE username=?', (role, name))
            for token, session in list(self._sessions.items()):
                if session['username'] == name:
                    self._sessions.pop(token, None)
        return {'username': name, 'role': role}

    def login(self, username, password, client_id, ip='local'):
        now = time.monotonic()
        key = (ip, str(username).lower())
        sec = self.cfg.get()['security']
        with self._lock:
            failures = [t for t in self._attempts.get(key, []) if now - t < int(sec.get('login_window_seconds', 300))]
            if len(failures) >= int(sec.get('max_login_attempts', 5)):
                raise PermissionError('Too many login attempts; try again later')
            with self._connect() as db:
                row = db.execute('SELECT salt,digest,role FROM users WHERE username=?', (str(username).lower(),)).fetchone()
            # Constant-cost verification even for unknown users.
            salt, digest, role = row if row else (b'\x00' * 16, b'\x00' * 32, 'viewer')
            valid = hmac.compare_digest(password_hash(str(password), salt), digest) and row is not None
            if not valid:
                self._attempts[key] = failures + [now]
                raise PermissionError('Invalid username or password')
            self._attempts.pop(key, None)
            token = secrets.token_urlsafe(32)
            self._sessions[token] = {'username': username.lower(), 'role': role, 'client_id': client_id, 'expires': now + int(sec.get('session_minutes', 240)) * 60}
            return token, role

    def demo_login(self, client_id, ip):
        if not validate_demo_configuration(self.cfg):
            raise PermissionError('Local demo mode is disabled')
        if not is_loopback(ip):
            raise PermissionError('Local demo requires a loopback connection')
        if not isinstance(client_id, str) or not 1 <= len(client_id) <= 128:
            raise ValueError('Invalid client ID')
        sec = self.cfg.get()['security']
        demo = sec['authentication'].get('local_demo', {})
        role = demo.get('role', 'employee')
        now = time.monotonic()
        token = secrets.token_urlsafe(32)
        # Distinct internal identity prevents mixing with a real account named demo.
        session = {'username': '__orion_local_demo__', 'display_username': demo.get('username', 'demo'),
                   'role': role, 'client_id': client_id,
                   'expires': now + int(sec.get('session_minutes', 240)) * 60,
                   'demo': True}
        with self._lock:
            self._sessions[token] = session
        return token, role, session['display_username']

    def is_demo_token(self, token):
        with self._lock:
            return bool(self._sessions.get(token, {}).get('demo'))

    def identity(self, token, client_id, ip='127.0.0.1'):
        with self._lock:
            session = self._sessions.get(token)
            if not session or session['client_id'] != client_id or session['expires'] < time.monotonic():
                self._sessions.pop(token, None)
                raise PermissionError('Sign in required or session expired')
            if session.get('demo'):
                if not validate_demo_configuration(self.cfg) or not is_loopback(ip):
                    self._sessions.pop(token, None)
                    raise PermissionError('Local demo session is not allowed here')
                expected_role = self.cfg.get()['security']['authentication'].get('local_demo', {}).get('role', 'employee')
                if session['role'] != expected_role:
                    self._sessions.pop(token, None)
                    raise PermissionError('Demo role changed; reconnect')
                return dict(session)
            if authentication_mode(self.cfg) != 'required':
                self._sessions.pop(token, None)
                raise PermissionError('Password login is disabled in demo mode')
            # Role changes from the local maintenance CLI run in another
            # process; verify against SQLite on every request to revoke old
            # privileges immediately, even for an existing server session.
            with self._connect() as db:
                row = db.execute('SELECT role FROM users WHERE username=?', (session['username'],)).fetchone()
            if row is None or row[0] != session['role'] or row[0] not in ROLES:
                self._sessions.pop(token, None)
                raise PermissionError('Account role changed; sign in again')
            return dict(session)

    def logout(self, token):
        with self._lock:
            self._sessions.pop(token, None)
