"""Explicit, verified live connections; read-only metadata and bounded previews."""
import re

SENSITIVE_COLUMN = re.compile(r'(?i)(password|passwd|secret|api[_-]?key|access[_-]?token|passwordhash|aadhaar|ssn|private[_-]?key)')
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import quote

from .config import resolve_path

IDENT = re.compile(r'^[^\W\d][\w .-]{0,127}$', re.UNICODE)


def ident(value, backend):
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise ValueError('Unsafe SQL identifier')
    return '[' + value + ']' if backend == 'sqlserver' else '"' + value + '"'


def safe_connection_part(value):
    text = str(value or '').strip()
    if any(ch in text for ch in (';', '\r', '\n', '{', '}')):
        raise ValueError('Invalid database connection field')
    return text


class LiveDB:
    def __init__(self, cfg, audit):
        self.cfg, self.audit = cfg, audit
        self._lock = threading.RLock()
        self._sessions = {}

    def _connect(self, params):
        options = self.cfg.get()['database']
        backend = str(params.get('backend', '')).lower()
        if backend not in options.get('allowed_backends', []):
            raise ValueError('Database backend disabled by YAML configuration')
        timeout = max(1, min(60, int(options.get('connect_timeout_seconds', 10))))
        if backend == 'sqlite':
            value = str(params.get('database', '')).strip()
            if not value:
                raise ValueError('Select an existing SQLite database file')
            path = resolve_path(value)
            if not path.is_file():
                raise FileNotFoundError('SQLite database does not exist: ' + str(path))
            uri = 'file:' + quote(str(path).replace('\\', '/'), safe='/:') + '?mode=ro'
            conn = sqlite3.connect(uri, uri=True, timeout=timeout, check_same_thread=False)
            conn.execute('PRAGMA query_only=ON')
            conn.execute('SELECT 1').fetchone()
            return conn, {'backend': backend, 'database': str(path), 'host': ''}
        host = safe_connection_part(params.get('host'))
        database = safe_connection_part(params.get('database'))
        username = safe_connection_part(params.get('username'))
        password = str(params.get('password') or '')
        auth_mode = str(params.get('auth_mode', 'sql')).lower()
        if not host or not database:
            raise ValueError('Host and database are required')
        if backend == 'postgresql' and not (username and password):
            raise ValueError('PostgreSQL username and password are required')
        if backend == 'sqlserver':
            if auth_mode not in options.get('sqlserver_auth_modes', ['sql']):
                raise PermissionError('SQL Server authentication mode disabled by YAML')
            if auth_mode == 'sql' and not (username and password):
                raise ValueError('SQL Server username and password are required')
        port = int(params.get('port') or (5432 if backend == 'postgresql' else 1433))
        if not 1 <= port <= 65535:
            raise ValueError('Invalid database port')
        if backend == 'postgresql':
            try:
                import psycopg
            except ImportError as exc:
                raise RuntimeError('PostgreSQL driver missing: pip install "psycopg[binary]"') from exc
            conn = psycopg.connect(host=host, port=port, dbname=database, user=username, password=password,
                                   connect_timeout=timeout, sslmode='require' if params.get('tls', True) else 'disable', autocommit=True)
            conn.execute('SET default_transaction_read_only = on')
            conn.execute('SELECT 1').fetchone()
        elif backend == 'sqlserver':
            try:
                import pyodbc
            except ImportError as exc:
                raise RuntimeError('SQL Server driver missing: pip install pyodbc and install Microsoft ODBC Driver 18') from exc
            driver = safe_connection_part(options.get('odbc_driver', 'ODBC Driver 18 for SQL Server'))
            # User/password are sent to the driver only, never stored or logged.
            pwd = '{' + password.replace('}', '}}') + '}'
            credentials = 'Trusted_Connection=yes;' if auth_mode == 'windows' else f'UID={username};PWD={pwd};'
            connection_string = (f'DRIVER={{{driver}}};SERVER={host},{port};DATABASE={database};{credentials}'
                                 f'Encrypt={"yes" if params.get("tls", True) else "no"};'
                                 f'TrustServerCertificate={"yes" if params.get("trust_server_certificate", False) else "no"};'
                                 'ApplicationIntent=ReadOnly;')
            conn = pyodbc.connect(connection_string, timeout=timeout, autocommit=True)
            conn.cursor().execute('SELECT 1').fetchone()
        else:
            raise ValueError('Unsupported database backend')
        return conn, {'backend': backend, 'database': database, 'host': host, 'port': port}

    def connect(self, token, username, role, params):
        if role not in self.cfg.get()['database'].get('allow_live_connection_roles', []):
            raise PermissionError('Your ORION role cannot open live database connections')
        if params.get('confirmed') is not True:
            raise PermissionError('Explicit connection approval is required')
        conn, info = self._connect(params)
        with self._lock:
            self.close(token)
            self._sessions[token] = {'conn': conn, 'info': info, 'lock': threading.RLock(), 'user': username, 'time': time.monotonic()}
        self.audit.event('db_connected', username=username, backend=info['backend'], database=info['database'], host=info.get('host', ''))
        return dict(info)

    def _session(self, token):
        with self._lock:
            session = self._sessions.get(token)
            if not session:
                raise ValueError('No live database connection. Choose Connect Database first.')
            return session

    def status(self, token):
        with self._lock:
            session = self._sessions.get(token)
            return {'connected': bool(session), **(dict(session['info']) if session else {})}

    def tables(self, token):
        session = self._session(token)
        backend = session['info']['backend']
        with session['lock']:
            conn = session['conn']
            if backend == 'sqlite':
                rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
                result = [{'schema': 'main', 'table': x[0]} for x in rows]
            elif backend == 'postgresql':
                rows = conn.execute("SELECT table_schema,table_name FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY table_schema,table_name").fetchall()
                result = [{'schema': x[0], 'table': x[1]} for x in rows]
            else:
                cursor = conn.cursor()
                rows = cursor.execute("SELECT TABLE_SCHEMA,TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE IN ('BASE TABLE','VIEW') ORDER BY TABLE_SCHEMA,TABLE_NAME").fetchall()
                result = [{'schema': x[0], 'table': x[1]} for x in rows]
            return result[:max(1, int(self.cfg.get()['database'].get('max_tables', 300)))]

    def preview(self, token, schema, table, limit=20):
        session = self._session(token)
        backend = session['info']['backend']
        maximum = max(1, int(self.cfg.get()['database'].get('max_preview_rows', 100)))
        limit = max(1, min(int(limit), maximum))
        with session['lock']:
            allowed = self.tables(token)
            if {'schema': schema, 'table': table} not in allowed:
                raise PermissionError('Table not present in approved live metadata list')
            table_sql = ident(table, backend)
            if backend != 'sqlite':
                table_sql = ident(schema, backend) + '.' + table_sql
            cursor = session['conn'].cursor()
            cursor.execute(f'SELECT TOP 0 * FROM {table_sql}' if backend == 'sqlserver'
                           else f'SELECT * FROM {table_sql} LIMIT 0')
            available_columns = [c[0] for c in cursor.description]
            columns = [c for c in available_columns if not SENSITIVE_COLUMN.search(c)]
            if not columns:
                raise PermissionError('No previewable columns: all columns are restricted')
            selected = ', '.join(ident(c, backend) for c in columns)
            if backend == 'sqlserver':
                sql = f'SELECT TOP {limit} {selected} FROM {table_sql}'
            else:
                sql = f'SELECT {selected} FROM {table_sql} LIMIT {limit}'
            cursor.execute(sql)
            rows = [[None if cell is None else (cell if isinstance(cell, (int, float, bool)) else str(cell)[:2000]) for cell in row] for row in cursor.fetchmany(limit)]
            self.audit.event('db_preview', username=session['user'], database=session['info']['database'], schema=schema, table=table, rows_count=len(rows))
            return {'columns': columns, 'rows': rows, 'database': session['info']['database'], 'schema': schema, 'table': table, 'count': len(rows), 'sql': sql}

    def close(self, token):
        with self._lock:
            session = self._sessions.pop(token, None)
        if session:
            try:
                with session['lock']:
                    session['conn'].close()
            except Exception:
                pass
            self.audit.event('db_disconnected', username=session['user'], database=session['info']['database'])
