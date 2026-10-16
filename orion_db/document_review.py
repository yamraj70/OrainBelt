"""Authenticated administrator review of document classification and access.

The ACL registry is the *only* authorization source. Classifier output is
advisory and never grants permissions. Review decisions are SHA256-bound.
"""
import hashlib
from datetime import datetime, timezone

from .access_control import DOCUMENT_ROLES


class DocumentReview:
    def __init__(self, knowledge, access, classifier, audit=None):
        self.knowledge, self.access, self.classifier, self.audit = knowledge, access, classifier, audit

    def _require_admin(self, role):
        # Server-side permission check: client controls are not authorization.
        settings = self.access._settings()
        reviewers = settings.get('reviewer_roles', ['admin'])
        if (not isinstance(reviewers, list) or not reviewers or
                any(r not in ('admin', 'manager') for r in reviewers)):
            raise PermissionError('Invalid document reviewer configuration')
        if role not in reviewers:
            raise PermissionError('Only authorized reviewers can review document access')
        if not self.access._settings().get('enabled', True):
            raise PermissionError('Document access enforcement must be enabled')
        if not self.access._settings().get('approval_required', True):
            raise PermissionError('Document approval workflow is disabled')

    def _snapshot(self):
        return self.access._index_snapshot(self.knowledge)

    def _current_file(self, source, digest):
        """Check that the file is still the exact synchronized revision."""
        root = self.knowledge._folder().resolve()
        original = root / source
        path = original.resolve()
        if (original.is_symlink() or not path.is_relative_to(root) or not path.is_file()):
            raise ValueError('Document is missing or is not a safe regular file; synchronize first')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Document changed since synchronization; synchronize and review again')

    def queue(self, role, status='pending', page=1, page_size=50):
        self._require_admin(role)
        if status not in ('pending', 'approved', 'rejected', 'all'):
            raise ValueError('Invalid review status')
        if type(page) is not int or type(page_size) is not int or not 1 <= page <= 100000 or not 1 <= page_size <= 100:
            raise ValueError('Invalid review pagination')
        with self.knowledge._lock:
            snapshot = self._snapshot()
            with self.access._lock:
                grants = self.access._read()
                try:
                    with self.classifier._lock:
                        classifications = self.classifier._read()
                except (ValueError, OSError):
                    classifications = {}
                entries = []
                for source, digest in sorted(snapshot.items()):
                    acl = grants.get(source, {})
                    current = acl if isinstance(acl, dict) and acl.get('sha256') == digest else {}
                    state = current.get('status') if current.get('status') in ('approved', 'rejected') else 'pending'
                    if status != 'all' and status != state:
                        continue
                    cls = classifications.get(source, {})
                    if not isinstance(cls, dict) or cls.get('sha256') != digest:
                        cls = {}
                    entries.append({
                        'source': source, 'sha256': digest, 'status': state,
                        'department': cls.get('department', 'unknown'),
                        'document_type': cls.get('document_type', 'other'),
                        'sensitivity': cls.get('sensitivity', 'unknown'),
                        'classification_status': cls.get('classification_status', 'pending'),
                        'suggested_roles': cls.get('suggested_roles', []),
                        'approved_roles': current.get('roles', []) if state == 'approved' else [],
                        'reviewed_by': current.get('set_by', ''),
                        'reviewed_at': current.get('updated_at', ''),
                    })
        start = (page - 1) * page_size
        return {'type': 'document_review_queue', 'status': status, 'total': len(entries),
                'page': page, 'page_size': page_size, 'items': entries[start:start + page_size]}

    def preview(self, role, source, expected_sha256, max_chars=3500):
        self._require_admin(role)
        if not isinstance(source, str) or not isinstance(expected_sha256, str):
            raise ValueError('Invalid document selection')
        if type(max_chars) is not int or not 100 <= max_chars <= 5000:
            raise ValueError('Invalid preview size')
        with self.knowledge._lock:
            digest = self._snapshot().get(source)
            if not digest or digest != expected_sha256:
                raise ValueError('Document revision changed; refresh the review queue')
            self._current_file(source, digest)
            text = '\n'.join(x['text'] for x in self.knowledge._index.get('chunks', []) if x['source'] == source)
            return {'type': 'document_review_preview', 'source': source, 'sha256': digest,
                    'text': text[:max_chars], 'truncated': len(text) > max_chars,
                    'note': 'Preview uses indexed/redacted text; it may be incomplete.'}

    def decide(self, role, reviewer, source, expected_sha256, approve, roles=None,
               expected_status=None, expected_roles=None):
        self._require_admin(role)
        if not isinstance(source, str) or not source or not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            raise ValueError('Invalid document selection')
        if type(approve) is not bool:
            raise ValueError('approve must be true or false')
        if approve:
            if (not isinstance(roles, list) or not roles or len(roles) > len(DOCUMENT_ROLES) or
                    any(not isinstance(r, str) or r not in DOCUMENT_ROLES or r == 'admin' for r in roles) or
                    len(set(roles)) != len(roles)):
                raise ValueError('Select one or more valid non-admin roles')
        elif roles not in (None, []):
            raise ValueError('Rejected documents cannot have access roles')
        # Lock ordering matches read paths: knowledge -> access. The source is
        # checked against both the index and live bytes before any ACL write.
        with self.knowledge._lock:
            digest = self._snapshot().get(source)
            if digest is None or digest != expected_sha256:
                raise ValueError('Document revision changed; synchronize and review again')
            self._current_file(source, digest)
            with self.access._lock:
                records = self.access._read()
                previous = records.get(source, {})
                if isinstance(previous, dict) and previous.get('sha256') == digest and previous.get('status') in ('approved', 'rejected'):
                    # Updating an existing decision requires the state displayed
                    # to the reviewer. Reject stale tabs / concurrent changes.
                    if (expected_status != previous['status'] or
                            not isinstance(expected_roles, list) or
                            len(expected_roles) != len(set(expected_roles)) or
                            sorted(expected_roles) != sorted(previous.get('roles', []))):
                        raise ValueError('Document already reviewed or permissions changed; refresh the queue')
                elif expected_status not in (None, 'pending'):
                    raise ValueError('Document review status changed; refresh the queue')
                status = 'approved' if approve else 'rejected'
                records[source] = {'sha256': digest, 'roles': sorted(roles) if approve else [],
                                   'status': status, 'set_by': reviewer,
                                   'updated_at': datetime.now(timezone.utc).isoformat()}
                self.access._write(records)
        if self.audit:
            self.audit.event('document_review_decision', source=source, status=status,
                             roles=sorted(roles) if approve else [], reviewer=reviewer)
        return {'type': 'document_review_result', 'source': source, 'status': status,
                'roles': sorted(roles) if approve else []}
