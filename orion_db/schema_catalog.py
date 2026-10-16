"""Source-traceable catalog built from TXT/MD schemas and verified live metadata.

A document describes a schema; it never proves that a server is reachable or that
any rows exist. Secrets and connection passwords are deliberately not parsed.
"""
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import resolve_path

DB_HEADER = re.compile(r'(?im)^\s*(?:\*\*)?(?:database\s*name|database)(?:\*\*)?\s*:\s*(?:\*\*)?\s*([^\r\n*|]+)')
ENGINE_HEADER = re.compile(r'(?im)^\s*(?:\*\*)?(?:engine|database\s*type)(?:\*\*)?\s*:\s*(?:\*\*)?\s*([^\r\n*|]+)')
TABLE_HEADER = re.compile(r'(?im)^\s*(?:#{2,5}\s+(?:\?+\s*)?|table\s*:\s*)([`\w.\-\[\] ]{1,160})\s*$')
COL_START = re.compile(r'^\s*\|\s*`?([^|`]+)`?\s*\|\s*`?([^|`]+)`?\s*\|')
NAME = re.compile(r'^[\w.\- ]{1,128}$', re.UNICODE)


def parse_schema(text, source):
    """Parse multiple database blocks, including the supplied Markdown-in-TXT style."""
    entries = {}
    matches = list(DB_HEADER.finditer(text))
    for index, match in enumerate(matches):
        name = match.group(1).strip().strip('` ').strip()
        if not NAME.fullmatch(name):
            continue
        section = text[match.end(): matches[index + 1].start() if index + 1 < len(matches) else len(text)]
        engine_match = ENGINE_HEADER.search(section)
        engine = engine_match.group(1).strip() if engine_match else ''
        item = entries.setdefault(name.casefold(), {'name': name, 'engine': engine, 'sources': [], 'tables': {}})
        if source not in item['sources']:
            item['sources'].append(source)
        if engine and not item['engine']:
            item['engine'] = engine
        table_matches = list(TABLE_HEADER.finditer(section))
        for n, table_match in enumerate(table_matches):
            table_name = table_match.group(1).strip().strip('`[] ')
            if table_name.lower().startswith(('column ', 'database ', 'schema ')) or not NAME.fullmatch(table_name):
                continue
            body = section[table_match.end(): table_matches[n + 1].start() if n + 1 < len(table_matches) else len(section)]
            columns = []
            for raw in body.splitlines():
                if not raw.lstrip().startswith('|'):
                    continue
                fields = [cell.strip().strip('`') for cell in raw.strip().strip('|').split('|')]
                if len(fields) < 2 or fields[0].casefold() in ('column', '---', ':---') or re.fullmatch(r'[:\- ]+', fields[0]):
                    continue
                if not NAME.fullmatch(fields[0]) or len(fields[1]) > 100:
                    continue
                if not re.match(r'(?i)^(?:n?varchar|n?char|text|int|bigint|smallint|tinyint|bit|float|real|numeric|decimal|money|datetime|date|time|timestamp|uniqueidentifier|uuid|boolean|blob|json|bytea|double|serial|integer)', fields[1]):
                    continue
                columns.append({'name': fields[0], 'type': fields[1],
                                'nullable': fields[2] if len(fields) > 2 else '',
                                'constraints': re.sub(r'[*`]', '', fields[4]).strip() if len(fields) > 4 else ''})
            if not columns and table_match.group(0).lstrip().startswith('#'):
                continue  # Generic Markdown headings are not necessarily tables.
            item['tables'][table_name.casefold()] = {'name': table_name, 'columns': columns, 'source': source, 'verified': False}
    return entries


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class SchemaCatalog:
    def __init__(self, cfg, audit):
        self.cfg, self.audit = cfg, audit
        self._lock = threading.RLock()
        self._docs = {}
        self._live = {}
        self.last_scan = ''
        self.scan_errors = []
        self._load()

    def _path(self):
        return resolve_path(self.cfg.get().get('knowledge', {}).get('schema_catalog_file', 'data/discovered_schema_catalog.json'))

    def _load(self):
        try:
            state = json.loads(self._path().read_text(encoding='utf-8'))
            self._docs = state.get('documented', {})
            self._live = state.get('verified_live', {})
            self.last_scan = state.get('last_scan', '')
        except (FileNotFoundError, ValueError, OSError):
            pass

    def _save(self):
        atomic_json(self._path(), {'documented': self._docs, 'verified_live': self._live,
                                   'last_scan': self.last_scan, 'version': 1})

    def update_documents(self, files):
        """files maps relative filename to redacted UTF-8 text."""
        parsed = {}
        for source, text in files.items():
            for key, entry in parse_schema(text, source).items():
                item = parsed.setdefault(key, {'name': entry['name'], 'engine': entry['engine'], 'sources': [], 'tables': {}})
                item['sources'].extend(x for x in entry['sources'] if x not in item['sources'])
                if not item['engine']:
                    item['engine'] = entry['engine']
                for table_key, table in entry['tables'].items():
                    previous = item['tables'].get(table_key)
                    if previous and previous.get('columns') != table.get('columns'):
                        table = {**table, 'conflict': True,
                                 'sources': sorted({previous['source'], table['source']})}
                    item['tables'][table_key] = table
        with self._lock:
            self._docs = parsed
            self._save()
        return len(parsed)

    def update_live(self, verified, errors=None):
        """Preserve snapshots for unreachable servers; mark their status stale."""
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            for key, entry in verified.items():
                self._live[key] = {**entry, 'verified_at': now, 'stale': False}
            for key, entry in self._live.items():
                if key not in verified:
                    entry['stale'] = True
            self.last_scan = now
            self.scan_errors = list(errors or [])[:30]
            self._save()

    def entries(self):
        with self._lock:
            result = []
            live_by_name = {}
            for key, live in self._live.items():
                live_by_name.setdefault(live['name'].casefold(), []).append((key, live))
            matched_live = set()
            for key, doc in self._docs.items():
                matching = live_by_name.get(doc['name'].casefold(), [])
                if not matching:
                    result.append({'name': doc['name'], 'engine': doc.get('engine', ''),
                                   'sources': list(doc.get('sources', [])),
                                   'tables': list(doc.get('tables', {}).values()),
                                   'verified': False, 'stale': False})
                    continue
                for live_key, live in matching:
                    matched_live.add(live_key)
                    tables = {x['name'].casefold(): x for x in doc.get('tables', {}).values()}
                    for table in live.get('tables', []):
                        tables[table['name'].casefold()] = {**table, 'verified': not live.get('stale', False)}
                    result.append({'name': live['name'], 'engine': live.get('backend', ''),
                                   'sources': list(doc.get('sources', [])), 'tables': list(tables.values()),
                                   'verified': not live.get('stale', False), 'stale': live.get('stale', False),
                                   'server': live.get('host', ''), 'alias': live.get('alias', ''),
                                   'verified_at': live.get('verified_at', '')})
            for live_key, live in self._live.items():
                if live_key not in matched_live:
                    result.append({'name': live['name'], 'engine': live.get('backend', ''),
                                   'sources': [], 'tables': [{**t, 'verified': not live.get('stale', False)}
                                                           for t in live.get('tables', [])],
                                   'verified': not live.get('stale', False), 'stale': live.get('stale', False),
                                   'server': live.get('host', ''), 'alias': live.get('alias', ''),
                                   'verified_at': live.get('verified_at', '')})
            return sorted(result, key=lambda x: (x['name'].casefold(), x.get('server', '')))

    def status(self):
        entries = self.entries()
        return {'databases': len(entries), 'documented_databases': len(self._docs),
                'live_snapshots': len(self._live), 'last_schema_scan': self.last_scan,
                'scan_errors': list(self.scan_errors), 'table_definitions': sum(len(x['tables']) for x in entries)}
