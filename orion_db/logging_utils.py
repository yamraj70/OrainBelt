"""Request/response event logs and separate redacted diagnostic errors."""
import json
import logging
import re
import threading
import traceback
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from .config import resolve_path

EVENT_LOCK = threading.RLock()


def redact(value):
    text = str(value)
    # Keep passwords and tokens out of logs, including accidental connection strings.
    patterns = [
        (r'(?i)(password|passwd|pwd|token|secret|api[_-]?key)\s*[:=]\s*[^\s,;\}\]]+', r'\1=[REDACTED]'),
        (r'(?i)("(?:password|passwd|pwd|token|secret|api_key)"\s*:\s*)"[^"]*"', r'\1"[REDACTED]"'),
        (r'(?i)(PWD\s*=\s*)\{[^}]*\}', r'\1{[REDACTED]}'),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


class Audit:
    def __init__(self, cfg):
        self.cfg = cfg
        section = cfg.get()['logging']
        self.directory = resolve_path(section['directory'])
        self.directory.mkdir(parents=True, exist_ok=True)
        self.events_path = self.directory / section.get('events_file', 'events.jsonl')
        self.errors_path = self.directory / section.get('errors_file', 'errors.log')
        self.logger = logging.getLogger('orion_db.' + str(id(self)))
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        handler = RotatingFileHandler(self.errors_path, maxBytes=int(section.get('max_bytes', 5000000)), backupCount=int(section.get('backup_count', 5)), encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        self.logger.addHandler(handler)

    def event(self, name, **fields):
        opts = self.cfg.get()['logging']
        record = {'time': datetime.now(timezone.utc).isoformat(), 'event': name}
        for key, value in fields.items():
            if key in ('password', 'token', 'credentials', 'rows', 'connection_string'):
                continue
            if key == 'request_text' and not opts.get('log_chat_text', False):
                continue
            if key == 'answer_text' and not opts.get('log_answer_text', False):
                continue
            record[key] = redact(value) if isinstance(value, str) else value
        with EVENT_LOCK:
            if self.events_path.exists() and self.events_path.stat().st_size > int(opts.get('max_bytes', 5000000)):
                self.events_path.replace(self.events_path.with_suffix('.jsonl.1'))
            with self.events_path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')

    def error(self, where, exc):
        # Sanitize the traceback and driver exception before persisting it.
        trace = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.logger.error('%s: %s', where, redact(trace[:20000]))
