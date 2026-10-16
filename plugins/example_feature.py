"""Example: add 'plugins.example_feature' to plugins.modules in YAML and restart."""

def register(registry):
    def handler(context, packet):
        return {'type': 'plugin_example', 'message': 'Hello ' + context['username']}
    registry.add('plugin_example', handler, role='viewer')
