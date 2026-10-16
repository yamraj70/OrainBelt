"""Hourly, bounded, metadata-only schema discovery on approved connections."""
import threading
from datetime import datetime, timezone

from .schema_catalog import SchemaCatalog
from .auth import authentication_mode


def _metadata(conn, backend, max_tables=300):
    """Read database metadata only; never fetch business rows."""
    cursor = conn.cursor()
    tables = []
    if backend == 'sqlite':
        rows = cursor.execute("SELECT name,type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        for name, kind in rows[:max_tables]:
            # SQLite PRAGMA does not support bound identifiers; quote safely.
            quoted = '"' + name.replace('"', '""') + '"'
            cols = conn.execute('PRAGMA table_info(' + quoted + ')').fetchall()
            columns = [{'name': row[1], 'type': row[2], 'nullable': not bool(row[3]),
                        'primary_key': bool(row[5])} for row in cols]
            foreign = conn.execute('PRAGMA foreign_key_list(' + quoted + ')').fetchall()
            tables.append({'schema': 'main', 'name': name, 'kind': kind, 'columns': columns,
                           'foreign_keys': [{'column': x[3], 'references_table': x[2], 'references_column': x[4]} for x in foreign]})
    elif backend == 'postgresql':
        rows = cursor.execute("SELECT table_schema,table_name,table_type FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY table_schema,table_name").fetchall()
        column_rows = conn.execute("SELECT table_schema,table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY ordinal_position").fetchall()
        columns_by_table = {}
        for schema, name, col, data_type, nullable in column_rows:
            columns_by_table.setdefault((schema, name), []).append({'name': col, 'type': data_type, 'nullable': nullable == 'YES'})
        for schema, name, kind in rows[:max_tables]:
            tables.append({'schema': schema, 'name': name, 'kind': kind,
                           'columns': columns_by_table.get((schema, name), [])})
    elif backend == 'sqlserver':
        rows = cursor.execute("SELECT TABLE_SCHEMA,TABLE_NAME,TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE IN ('BASE TABLE','VIEW') ORDER BY TABLE_SCHEMA,TABLE_NAME").fetchall()
        column_rows = cursor.execute("SELECT TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME,DATA_TYPE,IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS ORDER BY ORDINAL_POSITION").fetchall()
        columns_by_table = {}
        for schema, name, col, data_type, nullable in column_rows:
            columns_by_table.setdefault((schema, name), []).append({'name': col, 'type': data_type, 'nullable': nullable == 'YES'})
        for schema, name, kind in rows[:max_tables]:
            tables.append({'schema': schema, 'name': name, 'kind': kind,
                           'columns': columns_by_table.get((schema, name), [])})
    return tables


class Discovery:
    def __init__(self, cfg, audit, knowledge, live):
        self.cfg, self.audit, self.knowledge, self.live = cfg, audit, knowledge, live
        self.catalog = SchemaCatalog(cfg, audit)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._last_docs_check = 0.0
        self._last_schema_check = 0.0
        self.last_run = ''
        self.last_error = ''
        self.runs = 0

    def refresh(self, documents=True, schemas=True):
        if not self._lock.acquire(blocking=False):
            return {'status': 'busy', **self.status()}
        try:
            import time
            if documents:
                result = self.knowledge.sync_once()
                if result == 'error':
                    raise RuntimeError('Knowledge sync failed; previous index preserved')
                if result == 'updated' or not self.catalog._docs:
                    self.catalog.update_documents(self.knowledge.source_texts())
                self._last_docs_check = time.monotonic()
            if schemas:
                verified, errors = self._scan_live()
                self.catalog.update_live(verified, errors)
                self._last_schema_check = time.monotonic()
            self.last_run = datetime.now(timezone.utc).isoformat()
            self.last_error = ''
            self.runs += 1
            self.audit.event('discovery_completed', documented_databases=len(self.catalog._docs),
                             verified_live=len(self.catalog._live), errors=len(self.catalog.scan_errors))
            return {'status': 'completed', **self.status()}
        except Exception as exc:
            self.last_error = type(exc).__name__ + ': ' + str(exc)[:150]
            self.audit.error('discovery.refresh', exc)
            return {'status': 'error', **self.status()}
        finally:
            self._lock.release()

    def _scan_live(self):
        # Local demo must never contact saved database profiles, including
        # through the background discovery scheduler.
        if authentication_mode(self.cfg) == 'local_demo':
            return {}, []
        settings = self.cfg.get().get('database', {})
        maximum = max(1, min(5000, int(settings.get('max_tables', 300))))
        verified, errors = {}, []
        if not settings.get('schema_discovery', {}).get('enabled', False):
            return verified, errors
        # Only YAML-approved profiles and authenticated sessions. Never scan an
        # arbitrary server discovered in untrusted TXT/MD content.
        for profile in settings.get('profiles', []):
            alias = str(profile.get('alias', '')).strip()
            if not alias:
                continue
            params = dict(profile)
            backend = str(params.get('backend', '')).lower()
            if backend not in settings.get('allowed_backends', []):
                continue
            if backend != 'sqlite' and params.get('auth_mode') != 'windows':
                ref = params.get('credential_ref')
                if not ref:
                    errors.append({'profile': alias, 'reason': 'missing_credential_reference'})
                    continue
                try:
                    import keyring
                    password = keyring.get_password('ORION_DB', str(ref))
                except Exception:
                    password = None
                if not password:
                    errors.append({'profile': alias, 'reason': 'no_saved_credential'})
                    continue
                params['password'] = password
            if backend != 'sqlite' and not params.get('host'):
                errors.append({'profile': alias, 'reason': 'missing_host'})
                continue
            try:
                conn, info = self.live._connect(params)
                try:
                    tables = _metadata(conn, backend, maximum)
                finally:
                    conn.close()
                verified[(backend + '|' + info.get('host', '') + '|' + info['database']).casefold()] = {'name': info['database'], 'backend': backend,
                    'host': info.get('host', ''), 'alias': alias, 'tables': tables}
            except Exception as exc:
                # Driver exception text can include connection strings; do not persist it.
                errors.append({'profile': alias, 'reason': 'connection_or_metadata_failed', 'error_type': type(exc).__name__})
        # Include user-initiated connections; only read metadata with their lock.
        with self.live._lock:
            sessions = list(self.live._sessions.values())
        for session in sessions:
            info = session['info']
            try:
                with session['lock']:
                    tables = _metadata(session['conn'], info['backend'], maximum)
                verified[(info['backend'] + '|' + info.get('host', '') + '|' + info['database']).casefold()] = {'name': info['database'],
                    'backend': info['backend'], 'host': info.get('host', ''), 'tables': tables}
            except Exception as exc:
                errors.append({'profile': 'active_connection', 'reason': 'metadata_failed', 'error_type': type(exc).__name__})
        return verified, errors

    def status(self):
        return {'last_run': self.last_run, 'last_error': self.last_error, 'runs': self.runs,
                'scheduler_running': bool(self._thread and self._thread.is_alive()),
                'knowledge': self.knowledge.status(), 'catalog': self.catalog.status()}

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='ORION-Discovery', daemon=True)
        self._thread.start()

    def _loop(self):
        import time
        while not self._stop.is_set():
            config = self.cfg.get()
            doc_interval = max(60, int(config.get('knowledge', {}).get('sync_interval_seconds', 3600)))
            scan_settings = config.get('database', {}).get('schema_discovery', {})
            schema_interval = max(60, int(scan_settings.get('interval_seconds', 3600)))
            now = time.monotonic()
            doc_due = bool(config.get('features', {}).get('knowledge', True) and
                           int(config['knowledge'].get('sync_interval_seconds', 0)) > 0 and
                           now - self._last_docs_check >= doc_interval)
            schema_due = bool(authentication_mode(self.cfg) != 'local_demo' and
                              scan_settings.get('enabled', False) and
                              now - self._last_schema_check >= schema_interval)
            if doc_due or schema_due:
                self.refresh(documents=doc_due, schemas=schema_due)
            self._stop.wait(5)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
