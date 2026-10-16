"""Server-enforced, content-hash-bound document role permissions.

Qwen3 classification suggestions are never permission grants. Only a trusted
local administrator may write the ACL; the network API is read-only for ACLs.
Missing, invalid, stale or unapproved grants deny non-admin access.
"""
import hashlib
import json
import os
import threading
from datetime import datetime, timezone

from .config import resolve_path

DOCUMENT_ROLES = frozenset({'admin', 'hr', 'manager', 'finance', 'analyst', 'employee', 'viewer'})


class DocumentAccess:
    def __init__(self, cfg, audit=None):
        self.cfg, self.audit = cfg, audit
        self._lock = threading.RLock()

    def _settings(self):
        return self.cfg.get().get('document_access', {})

    def _path(self):
        return resolve_path(self._settings().get('registry_file', 'data/document_access.json'))

    def _read(self):
        path = self._path()
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('documents'), dict):
            raise ValueError('Invalid document access registry')
        return data['documents']

    def _write(self, records):
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + '.tmp')
        try:
            temp.write_text(json.dumps({'version': 1, 'documents': records}, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _index_snapshot(knowledge):
        with knowledge._lock:
            return {x['source']: x['sha256'] for x in knowledge._index.get('snapshot', [])}

    def allowed_sources(self, role, knowledge):
        """Compute a per-request allowlist; invalid ACL denies all non-admin.

        Do not expose whether hidden documents exist in counts or results.
        """
        if role not in DOCUMENT_ROLES:
            return set()
        snapshot = self._index_snapshot(knowledge)
        if role == 'admin':
            return set(snapshot)
        if not self._settings().get('enabled', True):
            # A disabled ACL must never silently grant access.
            return set()
        try:
            with self._lock:
                grants = self._read()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            if self.audit:
                self.audit.event('document_acl_unavailable')
            return set()
        allowed = set()
        for source, digest in snapshot.items():
            grant = grants.get(source)
            if not isinstance(grant, dict) or grant.get('sha256') != digest:
                continue
            roles = grant.get('roles', [])
            if (grant.get('status') == 'approved' and isinstance(roles, list) and
                    all(isinstance(r, str) and r in DOCUMENT_ROLES for r in roles) and role in roles):
                # A grant is valid only for the current on-disk revision. A
                # document changed after the last hourly sync must not expose
                # its previously indexed text, filename or embedding to users.
                root = knowledge._folder().resolve()
                original = root / source
                try:
                    path = original.resolve()
                    if (original.is_symlink() or not path.is_relative_to(root) or
                            not path.is_file() or
                            hashlib.sha256(path.read_bytes()).hexdigest() != digest):
                        continue
                except (OSError, ValueError):
                    continue
                allowed.add(source)
        return allowed

    def require(self, role, knowledge, source):
        if not isinstance(source, str) or source not in self.allowed_sources(role, knowledge):
            raise PermissionError('Document is unavailable or your role is not authorized')
        # Report exports read the live file, not the indexed chunks: reject
        # modifications before the next sync for non-admin roles as well.
        if role != 'admin':
            import hashlib
            snapshot = self._index_snapshot(knowledge)
            root = knowledge._folder().resolve()
            original = root / source
            path = original.resolve()
            if (original.is_symlink() or not path.is_relative_to(root) or not path.is_file() or
                    hashlib.sha256(path.read_bytes()).hexdigest() != snapshot.get(source)):
                raise PermissionError('Document has changed since approval; synchronize and review')

    def grant_local(self, knowledge, source, roles, operator='local_administrator'):
        """Trusted local maintenance only. Not callable via the network server.

        Bind grants to the current content digest. Editing a document invalidates
        its non-admin grant until an operator reviews and grants again.
        """
        if not isinstance(source, str) or not source or source.startswith('/') or '\\' in source or '..' in source.split('/'):
            raise ValueError('Invalid document source')
        role_set = set(roles)
        if not role_set or not role_set <= DOCUMENT_ROLES or 'admin' in role_set:
            raise ValueError('Specify valid non-admin roles (admin already has access)')
        snapshot = self._index_snapshot(knowledge)
        if source not in snapshot:
            raise ValueError('Document is not synchronized; run sync first')
        # Avoid granting a changed file based on a stale index.
        import hashlib
        root = knowledge._folder().resolve()
        path = (root / source).resolve()
        if not path.is_file() or not path.is_relative_to(root) or path.is_symlink():
            raise PermissionError('Invalid document path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != snapshot[source]:
            raise ValueError('Document changed since last sync; synchronize again')
        with self._lock:
            records = self._read()
            records[source] = {'sha256': snapshot[source], 'roles': sorted(role_set),
                               'status': 'approved', 'set_by': operator,
                               'updated_at': datetime.now(timezone.utc).isoformat()}
            self._write(records)
        if self.audit:
            self.audit.event('document_acl_granted', source=source, roles=sorted(role_set), operator=operator)
        return {'source': source, 'roles': sorted(role_set), 'sha256': snapshot[source]}

    def revoke_local(self, source, operator='local_administrator'):
        with self._lock:
            records = self._read()
            records.pop(source, None)
            self._write(records)
        if self.audit:
            self.audit.event('document_acl_revoked', source=source, operator=operator)
