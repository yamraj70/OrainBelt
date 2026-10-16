"""JSON-lines TCP server, preserving v26 message/thread design with database-only routing."""
import json
import re
import socketserver
import ssl
import threading
import time
import uuid

from .auth import Auth, authentication_mode, validate_demo_configuration
from .config import Config, resolve_path
from .database import LiveDB
from .discovery import Discovery
from .reports import generate_table_report, generate_document_report
from .features import Registry
from .history import History
from .knowledge import Knowledge
from .logging_utils import Audit, redact
from .model import Model
from .classifier import DocumentClassifier
from .access_control import DocumentAccess
from .rag_security import PermissionAwareRAG
from .document_review import DocumentReview
from .routing import route_question, GENERAL_OPENERS
from .response import concise, format_chat, expanded_requested
from .learning import Learning
from .agent import Agent, validate_plan, PLANNER_INSTRUCTIONS
from .conversation_context import planner_instructions


RESUME_QUERY = re.compile(r'\b(resumes?|resumen|résumés?|curriculum vitae|cvs?)\b', re.I)
RESUME_COUNT = re.compile(r'\b(how many|number of|count|total)\b', re.I)
RESUME_LIST = re.compile(r'\b(list|show|which|what|names?|files?)\b', re.I)
TODAY_DOCUMENT_QUERY = re.compile(r'\btoday\b', re.I)
DOCUMENT_NOUN = re.compile(r'\b(resumes?|resumen|résumés?|cvs?|candidates?|documents?|files?|pdfs?|docxs?|knowledge)\b', re.I)
DOCUMENT_CHANGED = re.compile(r'\b(new|added|uploaded|received|created|modified|updated|changed|arrived|submitted|today)\b', re.I)
DOCUMENT_MODIFIED = re.compile(r'\b(modified|updated|changed|edited)\b', re.I)
DOCUMENT_CREATED = re.compile(r'\b(new|added|uploaded|received|created|arrived|submitted)\b', re.I)


def recent_document_plan(question):
    """Deterministic today-inventory routing, including if Qwen3 is unavailable."""
    if not (TODAY_DOCUMENT_QUERY.search(question) and DOCUMENT_NOUN.search(question)
            and DOCUMENT_CHANGED.search(question)):
        return None
    has_resume = bool(RESUME_QUERY.search(question) or re.search(r'\bcandidates?\b', question, re.I))
    has_other = bool(re.search(r'\b(documents?|files?|pdfs?|docxs?|knowledge)\b', question, re.I))
    dtype = 'all' if has_other or not has_resume else 'resume'
    if DOCUMENT_MODIFIED.search(question) and DOCUMENT_CREATED.search(question):
        activity = 'all'
    elif DOCUMENT_MODIFIED.search(question):
        activity = 'modified'
    else:
        activity = 'created'
    return {'tool': 'recent_documents', 'arguments': {
        'document_type': dtype, 'activity': activity}}



CANDIDATE_FOLLOWUP = re.compile(
    r"^\s*(?:(?:what|who)\s+(?:are|is)\s+(?:the\s+)?|"
    r"(?:show|list|give\s+me|tell\s+me)\s+(?:the\s+)?)?"
    r"(?:(?:candidate|candidates|applicant|applicants)(?:\s+\w+){0,2}\s+)?"
    r"(?:names?|who\s+are\s+they|their\s+names?)\s*[?.!]*\s*$", re.I)


def resume_followup_plan(question, prior_messages):
    """Reuse only an explicit resume inventory question in this same thread."""
    if not CANDIDATE_FOLLOWUP.fullmatch(question):
        return None
    previous = next((m['content'] for m in reversed(prior_messages)
                     if m['role'] == 'user'), '')
    if not previous:
        return None
    prior_recent = recent_document_plan(previous)
    if prior_recent and prior_recent['arguments']['document_type'] == 'resume':
        return prior_recent
    if RESUME_QUERY.search(previous) and (RESUME_COUNT.search(previous) or RESUME_LIST.search(previous)):
        return {'tool': 'list_documents', 'arguments': {'document_type': 'resume'}}
    return None



RESUME_DETAIL_FOLLOWUP = re.compile(
    r"\b(skill\s*sets?|skills?|technologies|tech\s*stack|experience|"
    r"qualifications?|education|degrees?|certifications?|projects?|"
    r"expertise|background)\b", re.I)
RESUME_REFERENT = re.compile(
    r"\b(candidates?|applicants?|resumes?|cvs?|their|they|them|his|her|"
    r"he|she|those|these)\b", re.I)


def resume_detail_followup_plan(question, prior_messages):
    """Resolve resume content follow-ups, never mistake content for filenames.

    Only reuse resume context from this thread. Do not use an earlier topic
    if the most recent user message was unrelated to resumes.
    """
    if not (RESUME_DETAIL_FOLLOWUP.search(question) and
            RESUME_REFERENT.search(question)):
        return None
    previous = next((m['content'] for m in reversed(prior_messages)
                     if m.get('role') == 'user'), '')
    if not previous:
        return None
    is_resume_context = (bool(RESUME_QUERY.search(previous)) or
                         bool(re.search(r'\b(candidates?|applicants?)\b', previous, re.I)))
    if not is_resume_context and CANDIDATE_FOLLOWUP.fullmatch(previous):
        is_resume_context = any(
            RESUME_QUERY.search(m.get('content', ''))
            for m in prior_messages if m.get('role') == 'user')
    if not is_resume_context:
        return None
    # Include resume terminology for lexical retrieval. The RAG gateway still
    # checks approved sources and returns only indexed evidence.
    return {'tool': 'search_knowledge', 'arguments': {
        'query': 'resume candidate ' + question.strip()}}

def wants_connect(text):
    return bool(re.search(r'\b(connect|connection|login|log in|sign in)\b', text, re.I) and
                re.search(r'\b(db|database|sql server|postgres(?:ql)?|sqlite)\b', text, re.I))


def wants_catalog(text):
    return bool(re.search(
        r'\b(?:list|show|display|available|which|how many|what are)\b.{0,65}\b(?:databases?|dbs)\b'
        r'|\b(?:databases?|dbs)\b.{0,35}\b(?:we have|available|list)\b', text, re.I))


def wants_live_tables(text):
    return bool(re.search(r'\b(show|list|browse|view)\b.*\b(tables|schemas)\b', text, re.I) and
                re.search(r'\b(live|connected|database|db)\b', text, re.I))


def wants_live_rows(text):
    return bool(re.search(r'\b(live|connected|actual|real|query|preview|records|rows)\b', text, re.I) and
                re.search(r'\b(table|database|db|records|rows)\b', text, re.I))


class App:
    def __init__(self, config=None, startup_sync=True):
        self.cfg = config or Config()
        validate_demo_configuration(self.cfg)
        self.audit = Audit(self.cfg)
        self.auth = Auth(self.cfg)
        self.history = History(self.cfg)
        self.knowledge = Knowledge(self.cfg, self.audit)
        self.document_access = DocumentAccess(self.cfg, self.audit)
        self.knowledge.access_control = self.document_access
        self.rag = PermissionAwareRAG(self.knowledge, self.document_access, self.audit)
        self.model = Model(self.cfg, self.audit)
        self.knowledge.classifier = DocumentClassifier(self.cfg, self.model, self.audit)
        self.document_review = DocumentReview(self.knowledge, self.document_access, self.knowledge.classifier, self.audit)
        self.live = LiveDB(self.cfg, self.audit)
        self.discovery = Discovery(self.cfg, self.audit, self.knowledge, self.live)
        self.learning = Learning(self.cfg, self.audit, self.knowledge, self.discovery)
        self.agent = Agent(self)
        self.registry = Registry()
        self.registry.load(self.cfg.get().get('plugins', {}).get('modules', []))
        if startup_sync and self.cfg.get()['knowledge'].get('auto_sync_on_start', True):
            result = self.knowledge.sync_once()
            if result == 'error':
                raise RuntimeError('Startup knowledge synchronization failed: ' + self.knowledge.last_error)
        # Build catalog on startup; no network scan until the scheduler starts.
        if startup_sync:
            self.discovery.catalog.update_documents(self.knowledge.source_texts())

    def _short(self, answer, question):
        policy = self.cfg.get().get('response', {})
        return concise(answer, question, policy.get('default_max_words', 60),
                       policy.get('default_max_sentences', 2))

    def _intent(self, question, role="admin"):
        """General by default; route short document topics only on strong evidence."""
        intent = route_question(question, self.cfg.get())
        if intent == 'company' or GENERAL_OPENERS.search(question):
            return intent
        entries = self.discovery.catalog.entries() if role == 'admin' else []
        if any(re.search(r'(?<!\w)' + re.escape(e['name']) + r'(?!\w)', question, re.I)
               for e in entries if e['name']):
            return 'company'
        hits = self.rag.search(question, role, limit=1)
        if hits and hits[0]['score'] >= 9:
            return 'company'
        return 'general'

    def _demo_chat(self, user, token, role, packet):
        """Safe fallback for demo users: only general Qwen3 or approved RAG.

        The normal legacy router can connect to DBs or export reports, so
        demo traffic must not reach it, even if a future YAML grants employee
        roles more database privileges.
        """
        question = packet['message']
        thread_id = packet.get('thread_id')
        settings = self.cfg.get()
        if (wants_connect(question) or wants_live_tables(question) or
                wants_live_rows(question) or wants_catalog(question) or
                re.search(r'\b(generate|create|export|download)\b.*\b(report|excel|xlsx)\b', question, re.I)):
            answer = 'Live database and report actions are unavailable in local demo mode.'
            self.history.add(user, thread_id, question, answer)
            return {'type': 'answer', 'answer': answer, 'sources': [],
                    'thread_id': thread_id, 'threads': self.history.threads(user),
                    'intent': 'restricted'}
        if RESUME_QUERY.search(question) and (RESUME_COUNT.search(question) or RESUME_LIST.search(question)):
            listing = bool(RESUME_LIST.search(question) and not RESUME_COUNT.search(question))
            return self.agent.handle(user, token, role, packet, plan={
                'tool': 'list_documents' if listing else 'count_documents',
                'arguments': {'document_type': 'resume'}})
        if self._intent(question, role) == 'general':
            if settings['features'].get('local_model') and settings['model'].get('enabled'):
                try:
                    answer = self.model.answer_general(question)
                except Exception as exc:
                    self.audit.event('demo_general_model_failed', error_type=type(exc).__name__)
                    answer = 'Qwen3 is unavailable. Check the local model installation.'
            else:
                answer = 'General questions require the local Qwen3 model.'
            sources = []
            intent = 'general'
        else:
            evidence = self.rag.answer(question, role)
            answer, sources = evidence['answer'], evidence['sources']
            intent = 'company'
        answer = self._short(answer, question)
        self.history.add(user, thread_id, question, answer)
        return {'type': 'answer', 'answer': answer, 'sources': sources,
                'thread_id': thread_id, 'threads': self.history.threads(user),
                'intent': intent}

    def _chat(self, user, token, role, packet):
        question = packet.get('message', '')
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 10000:
            raise ValueError('Chat message must be 1–10,000 characters')
        thread_id = packet.get('thread_id')
        self.history.history(user, thread_id, 1)
        options = self.cfg.get()
        # Document inventory is deterministic; do not ask Qwen3 to count files.
        if RESUME_QUERY.search(question) and (RESUME_COUNT.search(question) or RESUME_LIST.search(question)):
            allowed = self.document_access.allowed_sources(role, self.knowledge)
            listing = bool(RESUME_LIST.search(question) and not RESUME_COUNT.search(question))
            inventory = self.knowledge.resume_inventory(include_names=listing, allowed_sources=allowed)
            count = inventory['count']
            answer = f'I found {count} resume file' + ('' if count == 1 else 's') + ' in synchronized knowledge.'
            if listing and count:
                visible = inventory['files'][:10]
                answer += '\n' + '\n'.join('• ' + name for name in visible)
                if count > len(visible):
                    answer += f'\n... and {count - len(visible)} more.'
            if not count:
                answer += ' Check the knowledge folder, supported formats, and synchronization status.'
            self.history.add(user, thread_id, question, answer)
            return {'type': 'answer', 'answer': answer, 'sources': [], 'thread_id': thread_id,
                    'threads': self.history.threads(user), 'document_count': count,
                    'inventory_last_sync': inventory['last_sync']}
        # Reports use only verified live data, never schema documentation as rows.
        if re.search(r'\b(generate|create|export|download)\b.*\b(report|excel|xlsx)\b|\b(report|excel|xlsx)\b.*\b(generate|create|export)\b', question, re.I):
            if role not in options['database'].get('allow_live_connection_roles', []):
                return {'type': 'answer', 'answer': 'Your role cannot export live database reports.', 'thread_id': thread_id}
            document_matches = [source for source in self.document_access.allowed_sources(role, self.knowledge)
                                if re.search(r'(?<![\w])' + re.escape(source.split('/')[-1]) + r'(?![\w])', question, re.I)]
            if len(document_matches) == 1:
                try:
                    return generate_document_report(self.cfg, self.audit, self.knowledge, document_matches[0], role=role)
                except ValueError as exc:
                    return {'type': 'answer', 'answer': str(exc), 'thread_id': thread_id}
            if not self.live.status(token)['connected']:
                return {'type': 'db_connect_required', 'message': 'Connect to the authorized database before exporting actual rows.', 'thread_id': thread_id}
            tables = self.live.tables(token)
            matches = [t for t in tables if re.search(r'(?<!\w)' + re.escape(t['table']) + r'(?!\w)', question, re.I)]
            if len(matches) == 1:
                return generate_table_report(self.cfg, self.audit, self.live, token, matches[0]['schema'], matches[0]['table'])
            return {'type': 'report_table_selection_required', 'message': 'Select a verified live table to export.', 'tables': tables, 'thread_id': thread_id}
        # Documented table listings require no connection; status is explicit.
        table_query = re.search(r'\b(?:list|show|which|what)\b.*\b(?:tables|columns)\b', question, re.I)
        if table_query and not re.search(r'\b(live|connected|actual|real)\b', question, re.I):
            if role != 'admin':
                raise PermissionError('Document-derived schema catalog requires administrator access')
            entries = self.discovery.catalog.entries()
            named = [e for e in entries if e['name'].casefold() in question.casefold()]
            if len(named) == 1:
                e = named[0]
                names = [t['name'] for t in e['tables']]
                answer = f'{e["name"]}: {len(names)} documented/verified tables: ' + ', '.join(names[:100 if expanded_requested(question) else 10])
                if len(names) > (100 if expanded_requested(question) else 10):
                    answer += f' (and {len(names)-(100 if expanded_requested(question) else 10)} more; use Schema Catalog for full list)'
                answer = self._short(answer, question)
                self.history.add(user, thread_id, question, answer)
                return {'type': 'answer', 'answer': answer, 'thread_id': thread_id, 'threads': self.history.threads(user), 'sources': e['sources']}
        known_database_names = [x['name'] for x in self.discovery.catalog.entries()] if role == 'admin' else []
        known_database_names.extend(str(p.get('alias', '')) for p in options['database'].get('profiles', []))
        connecting_known = bool(re.search(r'\bconnect(?:ion)?\s+(?:to\s+)?', question, re.I) and any(
            name and re.search(r'(?<![\w])' + re.escape(name) + r'(?![\w])', question, re.I)
            for name in known_database_names))
        if wants_connect(question) or connecting_known:
            if not options['features'].get('live_database', False):
                answer = 'Live database connections are disabled in YAML.'
                answer = self._short(answer, question)
                self.history.add(user, thread_id, question, answer)
                return {'type': 'answer', 'answer': answer, 'thread_id': thread_id, 'threads': self.history.threads(user)}
            if role not in options['database'].get('allow_live_connection_roles', []):
                answer = 'Your employee role is not permitted to open live database connections.'
                answer = self._short(answer, question)
                self.history.add(user, thread_id, question, answer)
                return {'type': 'answer', 'answer': answer, 'thread_id': thread_id, 'threads': self.history.threads(user)}
            # A named, administrator-approved YAML profile may be connected
            # without an extra confirmation for a read-only session.
            profiles = options['database'].get('profiles', [])
            named = [p for p in profiles if any(
                re.search(r'(?<![\w])' + re.escape(str(name)) + r'(?![\w])', question, re.I)
                for name in (p.get('alias'), p.get('database')) if name)]
            if len(named) == 1:
                try:
                    selected = named[0]
                    params = dict(selected)
                    if params.get('backend') != 'sqlite' and params.get('auth_mode') != 'windows':
                        import keyring
                        password = keyring.get_password('ORION_DB', str(params.get('credential_ref') or params.get('alias')))
                        if not password:
                            raise ValueError('No saved password for this approved profile')
                        params['password'] = password
                    params['confirmed'] = True
                    info = self.live.connect(token, user, role, params)
                    answer = 'Connected to ' + str(info['database']) + ' (verified, read-only).'
                    answer = self._short(answer, question)
                    self.history.add(user, thread_id, question, answer)
                    return {'type': 'answer', 'answer': answer, 'thread_id': thread_id,
                            'threads': self.history.threads(user), 'database': self.live.status(token)}
                except Exception as exc:
                    self.audit.event('db_saved_connect_failed', error_type=type(exc).__name__)
            answer = 'Select an approved database profile or enter connection details. A schema TXT/MD file alone is not a verified connection.'
            answer = self._short(answer, question)
            self.history.add(user, thread_id, question, answer)
            return {'type': 'db_connect_required', 'message': answer, 'thread_id': thread_id, 'threads': self.history.threads(user)}
        if wants_live_tables(question):
            if not self.live.status(token)['connected']:
                return {'type': 'db_connect_required', 'message': 'Connect to a live database before browsing its tables.', 'thread_id': thread_id}
            return {'type': 'db_table_selection_required', 'message': 'Select Browse Tables to view verified live metadata.', 'thread_id': thread_id}
        if wants_live_rows(question):
            if not self.live.status(token)['connected']:
                return {'type': 'db_connect_required', 'message': 'Live records require a verified connection. Connect Database first.', 'thread_id': thread_id}
            return {'type': 'db_table_selection_required', 'message': 'Choose a live table and preview row limit; ORION will not invent records.', 'thread_id': thread_id}
        if wants_catalog(question):
            if role != 'admin':
                raise PermissionError('Document-derived database catalog requires administrator access')
            catalog = self.discovery.catalog.entries()
            if catalog:
                answer = f'{len(catalog)} databases discovered (documented does not mean connected):\n' + '\n'.join(
                    f'• {item["name"]} — {len(item["tables"])} tables — ' +
                    ('live verified' if item['verified'] else 'documentation only / not live') +
                    ((' — ' + item['server']) if item.get('server') else '') for item in catalog[:100 if expanded_requested(question) else 10])
                if len(catalog) > (100 if expanded_requested(question) else 10):
                    answer += f'\n... and {len(catalog)-(100 if expanded_requested(question) else 10)} more; use the paginated schema catalog.'
            else:
                answer = 'No database names discovered yet. Add TXT/MD schema documents and wait for sync.'
            sources = list(dict.fromkeys(src for item in catalog for src in item['sources']))
        elif self._intent(question, role) == 'general':
            sources = []
            if not (options['features'].get('local_model', False) and options['model'].get('enabled', False)):
                answer = 'General questions require Qwen3. Enable the local model in YAML.'
            else:
                try:
                    answer = self.model.answer_general(question)
                except Exception as exc:
                    self.audit.error('model.general_inference', exc)
                    self.audit.event('model_error', error=str(exc))
                    answer = 'Qwen3 is unavailable. Check the GGUF model and server installation.'
        elif not options['features'].get('knowledge', True):
            answer, sources = 'Company knowledge is disabled in YAML.', []
        else:
            evidence = self.rag.answer(question, role)
            answer, sources = evidence['answer'], evidence['sources']
            hits = evidence['hits']
            if (hits and options['knowledge'].get('answer_mode', 'extractive') == 'model' and
                options['features'].get('local_model', False) and options['model'].get('enabled', False)):
                # Revocation or a file change between retrieval and model use
                # invalidates evidence before any document text reaches Qwen3.
                hits = self.rag.verify_hits(role, hits)
                if not hits:
                    answer, sources = 'No currently authorized document evidence is available.', []
                else:
                    sources = list(dict.fromkeys(h['source'] for h in hits))
                    try:
                        answer = self.model.summarize(question, hits)
                    except Exception as exc:
                        self.audit.error('model.inference', exc)
                        self.audit.event('model_error', error_type=type(exc).__name__)
                        if not options['model'].get('fallback_to_documents_on_error', True):
                            raise RuntimeError('Local model unavailable. See logs/errors.log for details.') from None
                        answer = 'Local model unavailable; ' + self.knowledge.answer_from_docs(question, hits)
        answer = self._short(answer, question)
        self.history.add(user, thread_id, question, answer)
        return {'type': 'answer', 'answer': answer, 'thread_id': thread_id, 'threads': self.history.threads(user), 'sources': sources}

    def process(self, packet, ip='127.0.0.1'):
        if not isinstance(packet, dict):
            raise ValueError('Request must be a JSON object')
        action = str(packet.get('type', 'chat'))
        client_id = packet.get('client_id')
        if not isinstance(client_id, str) or not 1 <= len(client_id) <= 128:
            raise ValueError('Invalid client ID')
        mode = authentication_mode(self.cfg)
        if action == 'demo_login':
            token, role, username = self.auth.demo_login(client_id, ip)
            self.audit.event('demo_session_started', username=username, role=role)
            return {'type': 'login_ok', 'token': token, 'username': username,
                    'role': role, 'demo': True}
        if action == 'login':
            if mode != 'required':
                raise PermissionError('Password login is disabled in local demo mode')
            if self.auth.user_count() == 0:
                raise PermissionError('No employee accounts. Run: python -m orion_db.cli create-user admin --role admin')
            token, role = self.auth.login(packet.get('username', ''), packet.get('password', ''), client_id, ip)
            return {'type': 'login_ok', 'token': token, 'username': str(packet.get('username', '')).lower(), 'role': role}
        token = packet.get('token')
        identity = self.auth.identity(token, client_id, ip)
        user, role = identity['username'], identity['role']
        demo_session = bool(identity.get('demo'))
        if demo_session and action not in (
                'hello', 'new_chat', 'open_chat', 'chat_history_page', 'list_chats', 'delete_chat',
                'chat', 'logout'):
            raise PermissionError('This action is unavailable in local demo mode')
        if action == 'hello':
            return {'type': 'profile', 'assistant_name': self.cfg.get()['app'].get('assistant_name', 'Orianbelt'),
                    'username': user, 'role': role, 'threads': self.history.threads(user), 'database': self.live.status(token)}
        if action == 'new_chat':
            tid = self.history.new(user)
            return {'type': 'chat_opened', 'thread_id': tid, 'history': [], 'threads': self.history.threads(user)}
        if action == 'open_chat':
            tid = packet.get('thread_id')
            return {'type': 'chat_opened', 'thread_id': tid, 'history': self.history.history(user, tid, limit=5000), 'threads': self.history.threads(user)}
        if action == 'chat_history_page':
            tid = packet.get('thread_id')
            page = self.history.history_page(user, tid, packet.get('before_id'), packet.get('limit', 100))
            return {'type': 'chat_history_page', 'thread_id': tid, **page}
        if action == 'list_chats':
            return {'type': 'chats', 'threads': self.history.threads(user)}
        if action == 'delete_chat':
            tid = packet.get('thread_id')
            self.history.delete(user, tid)
            return {'type': 'chat_deleted', 'thread_id': tid, 'threads': self.history.threads(user)}
        if action == 'chat':
            question = packet.get('message', '')
            if not isinstance(question, str) or not 1 <= len(question.strip()) <= 10000:
                raise ValueError('Chat message must be 1–10,000 characters')
            self.history.history(user, packet.get('thread_id'), 1)
            options = self.cfg.get()
            # Date-based inventory must use synchronized file metadata, never
            # an LLM's guess or content-search relevance scores.
            prior_messages = self.history.history(user, packet.get('thread_id'), 12)
            use_agent = (options.get('agent', {}).get('enabled', False) and
                         options['features'].get('local_model', False) and
                         options['model'].get('enabled', False))
            # Preserve exact, auditable file-metadata operations. With Qwen3
            # enabled, let the capability planner handle open-ended document
            # content follow-ups instead of forcing a keyword search string.
            deterministic_plan = (resume_followup_plan(question, prior_messages)
                                  or recent_document_plan(question))
            if not use_agent:
                deterministic_plan = (deterministic_plan or
                                      resume_detail_followup_plan(question, prior_messages))
            if deterministic_plan is not None:
                result = self.agent.handle(user, token, role, packet, plan=deterministic_plan)
                return format_chat(result, question, options, result.get('intent', 'document_inventory'))
            plan = None
            if use_agent:
                try:
                    plan = validate_plan(self.model.plan_action(question, planner_instructions(PLANNER_INSTRUCTIONS, prior_messages),
                                                                 options.get('agent', {})))
                except (ValueError, RuntimeError, FileNotFoundError, ImportError) as exc:
                    self.audit.event('agent_plan_failed', error_type=type(exc).__name__)
                    # No model-generated tool runs when the plan is invalid.
                    if options.get('agent', {}).get('fallback', 'legacy_safe_router') == 'error':
                        result = {'type': 'answer', 'answer': 'I could not safely interpret that request. Please rephrase.',
                                  'sources': [], 'thread_id': packet.get('thread_id')}
                        return format_chat(result, question, options, 'unknown')
            if plan is not None:
                if demo_session and plan['tool'] not in (
                        'general_answer', 'search_knowledge', 'analyze_documents', 'count_documents',
                        'list_documents', 'recent_documents'):
                    raise PermissionError('This tool is unavailable in local demo mode')
                result = self.agent.handle(user, token, role, packet, plan=plan)
                intent = result.get('intent', 'company')
            else:
                result = (self._demo_chat(user, token, role, packet) if demo_session else
                          self._chat(user, token, role, packet))
                intent = ('report'  if re.search(r'\b(report|excel|xlsx)\b', question, re.I) else
                          'database' if (wants_catalog(question) or wants_connect(question) or
                                         wants_live_tables(question) or wants_live_rows(question)) else
                          self._intent(question, role))
            return format_chat(result, question, options, intent)
        if action == 'document_review_queue':
            return self.document_review.queue(role, packet.get('status', 'pending'),
                                              packet.get('page', 1), packet.get('page_size', 50))
        if action == 'document_review_preview':
            return self.document_review.preview(role, packet.get('source'), packet.get('sha256'))
        if action == 'document_review_decide':
            return self.document_review.decide(role, user, packet.get('source'),
                                               packet.get('sha256'), packet.get('approve'),
                                               packet.get('roles'), packet.get('expected_status'),
                                               packet.get('expected_roles'))
        if action == 'learning_feedback_submit':
            return self.learning.submit(user, packet.get('question'), packet.get('answer'))
        if action == 'learning_feedback_pending':
            return self.learning.pending(role, packet.get('limit', 50))
        if action == 'learning_feedback_review':
            if not isinstance(packet.get('approve'), bool):
                raise ValueError('approve must be true or false')
            return self.learning.review(role, user, packet.get('id'), packet['approve'])

        if action == 'knowledge_status':
            if role == 'admin':
                return {'type': 'knowledge_status', **self.knowledge.status()}
            allowed = self.document_access.allowed_sources(role, self.knowledge)
            return {'type': 'knowledge_status', 'status': self.knowledge.last_status,
                    'last_sync': self.knowledge.last_sync, 'files': len(allowed),
                    'resume_files': self.knowledge.resume_inventory(allowed_sources=allowed)['count'],
                    'background_monitoring': bool(self.cfg.get()['knowledge'].get('sync_interval_seconds', 0))}
        if action == 'sync_knowledge':
            if role != 'admin':
                raise PermissionError('Only administrators can synchronize knowledge')
            result = self.discovery.refresh(documents=True, schemas=False)
            return {'type': 'knowledge_status', **self.knowledge.status(), 'sync_result': result['status']}
        if action == 'model_status':
            return {'type': 'model_status', **self.model.status()}
        if action == 'discovery_status':
            if role != 'admin':
                raise PermissionError('Discovery metadata requires administrator access')
            return {'type': 'discovery_status', **self.discovery.status()}
        if action == 'schema_catalog':
            if role != 'admin':
                raise PermissionError('Document-derived schema catalog requires administrator access')
            page = max(1, int(packet.get('page', 1)))
            page_size = max(1, min(100, int(packet.get('page_size', 30))))
            database_name = str(packet.get('database', '')).casefold().strip()
            all_entries = self.discovery.catalog.entries()
            if database_name:
                all_entries = [x for x in all_entries if x['name'].casefold() == database_name]
            subset = all_entries[(page - 1) * page_size:page * page_size]
            if not packet.get('include_columns', False):
                subset = [{**x, 'tables': [{'name': t['name'], 'schema': t.get('schema', ''),
                                          'verified': t.get('verified', False)} for t in x['tables']]}
                          for x in subset]
            return {'type': 'schema_catalog', 'databases': subset, 'total': len(all_entries),
                    'page': page, 'page_size': page_size, 'status': self.discovery.catalog.status()}
        if action == 'refresh_discovery':
            if role != 'admin':
                raise PermissionError('Only administrators can refresh all schema metadata')
            return {'type': 'discovery_status', **self.discovery.refresh(documents=True, schemas=True)}
        if action == 'report_generate_document':
            if role not in self.cfg.get().get('reports', {}).get('allow_roles', ['admin', 'analyst']):
                raise PermissionError('Your role cannot export reports')
            if not self.cfg.get().get('reports', {}).get('enabled', False):
                raise PermissionError('Excel reports are disabled in YAML')
            return generate_document_report(self.cfg, self.audit, self.knowledge, packet.get('source'), role=role)
        if action == 'report_generate':
            if role not in self.cfg.get()['database'].get('allow_live_connection_roles', []):
                raise PermissionError('Your role cannot export database reports')
            if not self.cfg.get().get('reports', {}).get('enabled', False):
                raise PermissionError('Excel reports are disabled in YAML')
            return generate_table_report(self.cfg, self.audit, self.live, token,
                                         packet.get('schema'), packet.get('table'))
        if action in ('db_profiles', 'db_connect', 'db_status', 'db_tables', 'db_preview') and not self.cfg.get()['features'].get('live_database', False):
            raise PermissionError('Live database tools are disabled in YAML')
        if action == 'db_profiles':
            return {'type': 'db_profiles', 'profiles': self.cfg.get()['database'].get('profiles', []),
                    'allowed_backends': self.cfg.get()['database'].get('allowed_backends', []),
                    'sqlserver_auth_modes': self.cfg.get()['database'].get('sqlserver_auth_modes', ['sql']),
                    'status': self.live.status(token)}
        if action == 'db_connect':
            if not self.cfg.get()['features'].get('live_database', False):
                raise PermissionError('Live database access is disabled')
            info = self.live.connect(token, user, role, packet)
            return {'type': 'db_connected', **info, 'verified': True}
        if action == 'db_connect_saved':
            if not self.cfg.get()['features'].get('live_database', False):
                raise PermissionError('Live database access is disabled')
            alias = str(packet.get('alias', ''))
            profile = next((p for p in self.cfg.get()['database'].get('profiles', [])
                            if p.get('alias') == alias), None)
            if not profile:
                raise ValueError('Unknown approved database profile')
            params = dict(profile)
            if params.get('backend') != 'sqlite' and params.get('auth_mode') != 'windows':
                try:
                    import keyring
                except ImportError as exc:
                    raise RuntimeError('Saved credentials require: pip install keyring') from exc
                password = keyring.get_password('ORION_DB', str(params.get('credential_ref') or alias))
                if not password:
                    return {'type': 'db_password_required', 'alias': alias,
                            'message': 'No saved password for this database. Enter it in Connect Database.'}
                params['password'] = password
            params['confirmed'] = True  # Selecting the approved profile authorizes read-only connection.
            info = self.live.connect(token, user, role, params)
            return {'type': 'db_connected', **info, 'verified': True}
        if action == 'db_status':
            return {'type': 'db_status', **self.live.status(token)}
        if action == 'db_tables':
            return {'type': 'db_tables', 'tables': self.live.tables(token), 'max_preview_rows': int(self.cfg.get()['database'].get('max_preview_rows', 100)), **self.live.status(token)}
        if action == 'db_preview':
            result = self.live.preview(token, packet.get('schema'), packet.get('table'), packet.get('limit', 20))
            return {'type': 'db_rows', **result}
        if action == 'db_disconnect':
            self.live.close(token)
            return {'type': 'db_disconnected'}
        if action == 'logout':
            self.live.close(token)
            self.auth.logout(token)
            return {'type': 'logged_out'}
        if action.startswith('plugin_'):
            reply = self.registry.handle(action, {'username': user, 'role': role, 'config': self.cfg}, packet)
            if reply is not None:
                return reply
        raise ValueError('Unknown or disabled action: ' + action)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        app = self.server.app
        max_bytes = int(app.cfg.get()['server'].get('max_request_bytes', 131072))
        self.request.settimeout(float(app.cfg.get()['server'].get('socket_idle_seconds', 900)))
        token = None
        while True:
            try:
                raw = self.rfile.readline(max_bytes + 1)
            except (OSError, TimeoutError):
                break
            if not raw:
                break
            request_id = uuid.uuid4().hex[:12]
            start = time.monotonic()
            action = 'unknown'
            try:
                if len(raw) > max_bytes or not raw.endswith(b'\n'):
                    raise ValueError('Request too large or missing newline')
                packet = json.loads(raw)
                action = packet.get('type', 'chat') if isinstance(packet, dict) else 'unknown'
                if action in ('login', 'demo_login'):
                    if token:
                        app.live.close(token)
                        app.auth.logout(token)
                    token = None
                request_text = packet.get('message') if isinstance(packet, dict) and action == 'chat' else None
                app.audit.event('request', request_id=request_id, action=action, request_text=request_text, peer=self.client_address[0])
                reply = app.process(packet, ip=self.client_address[0])
                if action in ('login', 'demo_login'):
                    token = reply.get('token')
                duration = round(time.monotonic() - start, 3)
                app.audit.event('response', request_id=request_id, action=action, response_type=reply.get('type'),
                                answer_text=reply.get('answer') if action == 'chat' else None, duration_seconds=duration)
            except (ValueError, PermissionError, KeyError, TypeError, json.JSONDecodeError) as exc:
                reply = {'type': 'error', 'message': redact(str(exc))}
                app.audit.event('request_error', request_id=request_id, action=action, error=redact(str(exc)))
            except Exception as exc:
                app.audit.error('tcp.' + str(action), exc)
                app.audit.event('request_error', request_id=request_id, action=action, error_type=type(exc).__name__)
                reply = {'type': 'error', 'message': 'Request failed. Check logs/errors.log. ' + redact(str(exc))[:240] if action == 'db_connect' else 'Request failed; check server error log.'}
            try:
                self.wfile.write((json.dumps(reply, ensure_ascii=False, default=str) + '\n').encode('utf-8'))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                break
        if token:
            app.live.close(token)
            app.auth.logout(token)


class TCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(config=None):
    app = App(config)
    validate_demo_configuration(app.cfg)
    section = app.cfg.get()['server']
    host, port = str(section['host']), int(section['port'])
    tls = section.get('tls', {})
    if host not in ('127.0.0.1', '::1', 'localhost') and not (tls.get('cert') and tls.get('key')):
        raise RuntimeError('Non-loopback TCP server requires a TLS certificate and key in YAML')
    with TCPServer((host, port), Handler) as server:
        server.app = app
        if tls.get('cert') and tls.get('key'):
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(resolve_path(tls['cert'])), str(resolve_path(tls['key'])))
            server.socket = context.wrap_socket(server.socket, server_side=True)
        print(f'ORION Database Enterprise listening on {host}:{port} (TXT sync + live DB)')
        app.audit.event('server_started', host=host, port=port)
        app.discovery.start()
        try:
            server.serve_forever()
        finally:
            app.discovery.stop()
