"""Role-bound RAG gateway used by every authenticated document-answer path.

The classifier and Qwen3 are never authorization authorities. Both lexical and
semantic hits are checked against the current ACL and the synchronized index
before evidence is returned to an answer generator. Empty access means empty
retrieval, not an unrestricted search.
"""


class PermissionAwareRAG:
    def __init__(self, knowledge, document_access, audit=None):
        self.knowledge = knowledge
        self.document_access = document_access
        self.audit = audit

    def allowed_sources(self, role):
        return self.document_access.allowed_sources(role, self.knowledge)

    def verify_hits(self, role, hits):
        """Recheck grants after retrieval; never trust search/embedding output."""
        allowed = self.allowed_sources(role)
        if not allowed:
            return []
        with self.knowledge._lock:
            indexed = {(c['source'], c['text']) for c in self.knowledge._index.get('chunks', [])
                       if c['source'] in allowed}
        return [hit for hit in hits if isinstance(hit, dict) and
                (hit.get('source'), hit.get('text')) in indexed]

    def search(self, question, role, limit=None):
        allowed = self.allowed_sources(role)
        if not allowed:
            return []
        hits = self.knowledge.search(question, limit=limit, allowed_sources=allowed)
        return self.verify_hits(role, hits)

    def analyze_documents(self, question, role, document_type='all'):
        """Multi-document extractive evidence, reauthorized before output."""
        allowed = self.allowed_sources(role)
        if not allowed:
            return {'answer': 'No approved document evidence is available to your account.',
                    'sources': [], 'evidence': []}
        excerpts = self.knowledge.document_evidence(
            question, allowed_sources=allowed, document_type=document_type)
        # Recheck the live grants, including grant revocation during retrieval.
        current = self.allowed_sources(role)
        excerpts = [item for item in excerpts if item['source'] in current]
        if not excerpts:
            return {'answer': 'No matching evidence was found in approved indexed documents.',
                    'sources': [], 'evidence': []}
        lines = ['Relevant excerpts from approved documents (not verified conclusions):']
        for item in excerpts:
            lines.append('Source: ' + item['source'])
            lines.extend('- ' + excerpt for excerpt in item['excerpts'])
        return {'answer': '\n'.join(lines),
                'sources': [item['source'] for item in excerpts], 'evidence': excerpts}

    def approved_answer(self, question, role):
        allowed = self.allowed_sources(role)
        if not allowed:
            return None
        approved = self.knowledge.approved_answer(question, allowed_sources=allowed)
        if approved and approved[1] in self.allowed_sources(role):
            return approved
        return None

    def answer(self, question, role):
        approved = self.approved_answer(question, role)
        if approved:
            return {'answer': approved[0], 'sources': [approved[1]], 'hits': [],
                    'approved_correction': True}
        hits = self.search(question, role)
        if not hits:
            return {'answer': 'I could not find that in the knowledge available to your account.',
                    'sources': [], 'hits': [], 'approved_correction': False}
        return {'answer': self.knowledge.answer_from_docs(question, hits),
                'sources': list(dict.fromkeys(h['source'] for h in hits)),
                'hits': hits, 'approved_correction': False}
