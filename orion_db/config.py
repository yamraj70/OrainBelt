"""Single YAML configuration, runtime reload, and safe UI preference updates."""
import os
import threading
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / 'config' / 'orion.yaml'


def config_path():
    return Path(os.environ.get('ORION_CONFIG', str(DEFAULT_CONFIG))).expanduser().resolve()


def resolve_path(value):
    path = Path(os.path.expandvars(str(value))).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


class Config:
    def __init__(self, path=None):
        self.path = Path(path or config_path()).resolve()
        self._lock = threading.RLock()
        self._data = None
        self._stamp = None

    def get(self):
        with self._lock:
            stamp = (self.path.stat().st_mtime_ns, self.path.stat().st_size)
            if self._data is None or stamp != self._stamp:
                data = yaml.safe_load(self.path.read_text(encoding='utf-8'))
                if not isinstance(data, dict):
                    raise ValueError('ORION YAML configuration must be a mapping')
                for key in ('server', 'client', 'ui', 'knowledge', 'database', 'model', 'security', 'logging', 'features'):
                    if not isinstance(data.get(key), dict):
                        raise ValueError('Missing YAML section: ' + key)
                if not isinstance(data.get('plugins', {}).get('modules', []), list):
                    raise ValueError('plugins.modules must be a list')
                self._data, self._stamp = data, stamp
            return self._data

    def update_ui(self, **values):
        allowed = {'theme', 'width', 'height'}
        if set(values) - allowed:
            raise ValueError('Only UI preferences can be edited from the desktop')
        with self._lock:
            data = dict(self.get())
            data['ui'] = {**data['ui'], **values}
            temp = self.path.with_suffix(self.path.suffix + '.tmp')
            temp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding='utf-8')
            os.replace(temp, self.path)
            self._data = None
            return self.get()
