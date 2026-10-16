"""Explicit plugin registry for future trusted modules (disabled by default)."""
import importlib


class Registry:
    def __init__(self):
        self._handlers = {}

    def add(self, action, handler, role='viewer'):
        if not action.startswith('plugin_') or not callable(handler):
            raise ValueError('Plugin action must start with plugin_ and have a callable handler')
        if role not in ('viewer', 'analyst', 'admin') or action in self._handlers:
            raise ValueError('Invalid or duplicate plugin registration')
        self._handlers[action] = (handler, role)

    def handle(self, action, context, packet):
        if action not in self._handlers:
            return None
        handler, role = self._handlers[action]
        order = {'viewer': 0, 'analyst': 1, 'admin': 2}
        if order[context['role']] < order[role]:
            raise PermissionError('Insufficient permissions for plugin')
        return handler(context, packet)

    def load(self, module_names):
        # Only trusted local Python modules explicitly named by the administrator.
        for name in module_names:
            if not isinstance(name, str) or not name.startswith('plugins.'):
                raise ValueError('Plugins must be trusted local plugins.* modules')
            module = importlib.import_module(name)
            module.register(self)
