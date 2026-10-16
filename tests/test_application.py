import json
import socket
import threading
from pathlib import Path
import pytest
import yaml

from orion_db.config import Config
from orion_db.database import ident
from orion_db.logging_utils import redact
from orion_db.server import Handler, TCPServer
from orion_db.cli import create_demo_db


def test_startup_sync(identity):
    app, auth, _ = identity
    result = app.process({**auth, 'type': 'knowledge_status'})
    assert result['files'] == 1
    assert result['background_monitoring'] is True


def test_document_catalog(identity):
    app, auth, _ = identity
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    result = app.process({**auth, 'type': 'chat', 'thread_id': tid, 'message': 'which databases do we have?'})
    assert result['type'] == 'answer'
    assert 'finance_reporting' in result['answer']
    assert 'not live' in result['answer'].lower()


def test_grounded_answer(identity):
    app, auth, _ = identity
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    result = app.process({**auth, 'type': 'chat', 'thread_id': tid, 'message': 'monthly finance reporting'})
    assert 'catalog.txt' in result['answer']
    assert result['sources'] == ['catalog.txt']


def test_unknown_question_not_invented(identity):
    app, auth, _ = identity
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    result = app.process({**auth, 'type': 'chat', 'thread_id': tid, 'message': 'quantum pineapple xylophone'})
    assert 'qwen3' in result['answer'].lower()  # Unknown general topics no longer default to TXT.


def test_connection_prompt_never_connects_automatically(identity):
    app, auth, _ = identity
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    result = app.process({**auth, 'type': 'chat', 'thread_id': tid, 'message': 'connect to finance_reporting database'})
    assert result['type'] == 'db_connect_required'
    assert not app.live.status(auth['token'])['connected']


def test_sqlite_connection_requires_approval(identity):
    app, auth, root = identity
    path = create_demo_db(root / 'demo.sqlite')
    with pytest.raises(PermissionError):
        app.process({**auth, 'type': 'db_connect', 'backend': 'sqlite', 'database': str(path)})
    result = app.process({**auth, 'type': 'db_connect', 'backend': 'sqlite', 'database': str(path), 'confirmed': True})
    assert result['verified'] is True
    assert result['backend'] == 'sqlite'
    tables = app.process({**auth, 'type': 'db_tables'})
    assert any(t['table'] == 'assets' for t in tables['tables'])
    rows = app.process({**auth, 'type': 'db_preview', 'schema': 'main', 'table': 'assets', 'limit': 2})
    assert rows['count'] == 2
    assert rows['columns'] == ['asset_id', 'asset_name', 'department', 'purchase_year']
    assert app.process({**auth, 'type': 'db_disconnect'})['type'] == 'db_disconnected'


def test_sqlite_is_read_only(identity):
    app, auth, root = identity
    path = create_demo_db(root / 'demo.sqlite')
    app.process({**auth, 'type': 'db_connect', 'backend': 'sqlite', 'database': str(path), 'confirmed': True})
    with pytest.raises(Exception):
        app.live._sessions[auth['token']]['conn'].execute('DELETE FROM assets')
    with pytest.raises(PermissionError):
        app.live.preview(auth['token'], 'main', 'assets; DROP TABLE assets;', 10)


def test_unknown_live_table_rejected(identity):
    app, auth, root = identity
    path = create_demo_db(root / 'demo.sqlite')
    app.process({**auth, 'type': 'db_connect', 'backend': 'sqlite', 'database': str(path), 'confirmed': True})
    with pytest.raises(PermissionError):
        app.live.preview(auth['token'], 'main', 'not_a_real_table', 5)


def test_manual_sync_replaces_deleted_document(identity):
    app, auth, root = identity
    (root / 'knowledge' / 'catalog.txt').unlink()
    status = app.process({**auth, 'type': 'sync_knowledge'})
    assert status['files'] == 0
    assert app.knowledge.catalog() == []


def test_viewer_cannot_sync_or_connect(sandbox):
    app, _, root = sandbox
    app.auth.create_user('viewer1', 'CorrectHorseBattery42', 'viewer')
    login = app.process({'type': 'login', 'client_id': 'v1', 'username': 'viewer1', 'password': 'CorrectHorseBattery42'})
    packet = {'client_id': 'v1', 'token': login['token']}
    with pytest.raises(PermissionError):
        app.process({**packet, 'type': 'sync_knowledge'})
    with pytest.raises(PermissionError):
        app.process({**packet, 'type': 'db_connect', 'backend': 'sqlite', 'database': str(root / 'foo.sqlite'), 'confirmed': True})


def test_unauthenticated_rejected(sandbox):
    app, _, _ = sandbox
    with pytest.raises(PermissionError):
        app.process({'type': 'knowledge_status', 'client_id': 'client'})


def test_user_history_isolation(sandbox):
    app, _, _ = sandbox
    app.auth.create_user('another', 'CorrectHorseBattery42', 'analyst')
    one = app.process({'type': 'login', 'client_id': 'c1', 'username': 'tester', 'password': 'CorrectHorseBattery42'})
    two = app.process({'type': 'login', 'client_id': 'c2', 'username': 'another', 'password': 'CorrectHorseBattery42'})
    tid = app.process({'type': 'new_chat', 'client_id': 'c1', 'token': one['token']})['thread_id']
    with pytest.raises(PermissionError):
        app.process({'type': 'open_chat', 'client_id': 'c2', 'token': two['token'], 'thread_id': tid})


def test_yaml_hot_reload(sandbox):
    app, cfg, root = sandbox
    current = yaml.safe_load(cfg.path.read_text(encoding='utf-8'))
    current['model']['max_tokens'] = 77
    cfg.path.write_text(yaml.safe_dump(current), encoding='utf-8')
    assert cfg.get()['model']['max_tokens'] == 77


def test_ui_config_writes_yaml(sandbox):
    app, cfg, root = sandbox
    cfg.update_ui(theme='Ocean')
    assert yaml.safe_load(cfg.path.read_text())['ui']['theme'] == 'Ocean'
    with pytest.raises(ValueError):
        cfg.update_ui(password='abc')


def test_password_redaction():
    assert 'SuperSecret' not in redact('password=SuperSecret')
    assert 'SuperSecret' not in redact('PWD={SuperSecret}')
    assert 'SecretToken' not in redact('"token": "SecretToken"')


def test_db_password_not_logged(identity):
    app, auth, root = identity
    path = create_demo_db(root / 'demo.sqlite')
    app.process({**auth, 'type': 'db_connect', 'backend': 'sqlite', 'database': str(path), 'password': 'DONT_LOG_THIS', 'confirmed': True})
    app.live.close(auth['token'])
    text = '\n'.join(p.read_text() for p in (root / 'logs').iterdir())
    assert 'DONT_LOG_THIS' not in text


def test_disabled_feature_rejected(identity):
    app, auth, _ = identity
    with pytest.raises(ValueError):
        app.process({**auth, 'type': 'transfer_offer'})
    with pytest.raises(ValueError):
        app.process({**auth, 'type': 'browser_open'})


def test_model_disabled_by_default(identity):
    app, auth, _ = identity
    status = app.process({**auth, 'type': 'model_status'})
    assert status['enabled'] is False


def test_socket_protocol(identity):
    app, auth, _ = identity
    with TCPServer(('127.0.0.1', 0), Handler) as server:
        server.app = app
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.create_connection(server.server_address, timeout=2) as sock:
                sock.settimeout(5)
                stream = sock.makefile('rb')
                sock.sendall((json.dumps({**auth, 'type': 'new_chat'}) + '\n').encode())
                reply = json.loads(stream.readline())
                assert reply['type'] == 'chat_opened'
                sock.sendall((json.dumps({**auth, 'type': 'chat', 'thread_id': reply['thread_id'], 'message': 'finance reporting'}) + '\n').encode())
                answer = json.loads(stream.readline())
                assert answer['type'] == 'answer'
                assert 'catalog.txt' in answer['answer']
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_plugin_registry(sandbox):
    from orion_db.features import Registry
    registry = Registry()
    registry.load(['plugins.example_feature'])
    result = registry.handle('plugin_example', {'username': 'tester', 'role': 'viewer'}, {})
    assert result['message'] == 'Hello tester'


def test_identifier_rejects_sql_injection():
    assert ident('assets', 'sqlite') == '"assets"'
    with pytest.raises(ValueError):
        ident('assets;drop table assets', 'sqlite')


def test_viewer_connect_chat_does_not_prompt(sandbox):
    app, _, _ = sandbox
    app.auth.create_user('viewer2', 'CorrectHorseBattery42', 'viewer')
    login = app.process({'type': 'login', 'client_id': 'v2', 'username': 'viewer2', 'password': 'CorrectHorseBattery42'})
    packet = {'client_id': 'v2', 'token': login['token']}
    tid = app.process({**packet, 'type': 'new_chat'})['thread_id']
    result = app.process({**packet, 'type': 'chat', 'thread_id': tid, 'message': 'connect to database'})
    assert result['type'] == 'answer'
    assert 'not permitted' in result['answer']


def test_db_flag_blocks_all_live_operations(identity):
    app, auth, root = identity
    import yaml
    settings = yaml.safe_load(app.cfg.path.read_text(encoding='utf-8'))
    settings['features']['live_database'] = False
    app.cfg.path.write_text(yaml.safe_dump(settings), encoding='utf-8')
    for action in ('db_profiles', 'db_tables', 'db_preview'):
        with pytest.raises(PermissionError):
            app.process({**auth, 'type': action})


def test_model_missing_falls_back_to_docs(identity, monkeypatch):
    app, auth, _ = identity
    settings = yaml.safe_load(app.cfg.path.read_text(encoding='utf-8'))
    settings['features']['local_model'] = True
    settings['model']['enabled'] = True
    app.cfg.path.write_text(yaml.safe_dump(settings), encoding='utf-8')
    def fail(*args):
        raise RuntimeError('llama-cpp-python not installed')
    monkeypatch.setattr(app.model, 'summarize', fail)
    tid = app.process({**auth, 'type': 'new_chat'})['thread_id']
    result = app.process({**auth, 'type': 'chat', 'thread_id': tid, 'message': 'finance reporting'})
    assert 'Local model unavailable' not in result['answer']  # Extractive docs by default.
    assert 'catalog.txt' in result['answer']


def test_model_can_switch_gguf_paths_without_restart(identity, monkeypatch):
    import sys
    import types
    app, auth, root = identity
    first, second = root / 'first.gguf', root / 'second.gguf'
    first.write_bytes(b'gguf one')
    second.write_bytes(b'gguf two')
    loaded = []
    class FakeLlama:
        def __init__(self, model_path, **kwargs):
            loaded.append(model_path)
        def create_chat_completion(self, **kwargs):
            return {'choices': [{'message': {'content': 'Grounded answer [Source: catalog.txt]'}}]}
    monkeypatch.setitem(sys.modules, 'llama_cpp', types.SimpleNamespace(Llama=FakeLlama))
    settings = yaml.safe_load(app.cfg.path.read_text(encoding='utf-8'))
    settings['features']['local_model'] = True
    settings['model']['enabled'] = True
    settings['model']['path'] = str(first)
    app.cfg.path.write_text(yaml.safe_dump(settings), encoding='utf-8')
    hits = app.knowledge.search('finance reporting')
    assert 'Grounded answer' in app.model.summarize('finance reporting', hits)
    assert len(loaded) == 1
    app.model.summarize('finance reporting', hits)
    assert len(loaded) == 1
    settings['model']['path'] = str(second)
    app.cfg.path.write_text(yaml.safe_dump(settings), encoding='utf-8')
    app.model.summarize('finance reporting', hits)
    assert loaded == [str(first), str(second)]


def test_socket_login_does_not_log_password(sandbox):
    app, _, root = sandbox
    with TCPServer(('127.0.0.1', 0), Handler) as server:
        server.app = app
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.create_connection(server.server_address, timeout=2) as sock:
                sock.settimeout(5)
                sock.sendall((json.dumps({'type': 'login', 'client_id': 'safe-client', 'username': 'tester', 'password': 'CorrectHorseBattery42'}) + '\n').encode())
                response = json.loads(sock.makefile('rb').readline())
                assert response['type'] == 'login_ok'
        finally:
            server.shutdown()
            thread.join(timeout=5)
    events = (root / 'logs' / 'events.jsonl').read_text()
    assert 'CorrectHorseBattery42' not in events
    assert 'login' in events


def test_sqlserver_windows_auth_mocked(sandbox, monkeypatch):
    import sys
    import types
    app, cfg, root = sandbox
    strings = []
    class Cursor:
        def execute(self, sql):
            return self
        def fetchone(self):
            return (1,)
    class Conn:
        def cursor(self):
            return Cursor()
    def connect(s, **kwargs):
        strings.append(s)
        return Conn()
    monkeypatch.setitem(sys.modules, 'pyodbc', types.SimpleNamespace(connect=connect))
    connection, info = app.live._connect({'backend': 'sqlserver', 'host': 'db.example', 'database': 'finance_reporting',
                                         'port': 1433, 'auth_mode': 'windows', 'tls': True})
    assert info['backend'] == 'sqlserver'
    assert 'Trusted_Connection=yes' in strings[0]
    assert 'PWD=' not in strings[0]
