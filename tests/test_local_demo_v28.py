"""v2.8 demo mode: local only, passwordless, restricted, with real document ACLs."""
import pytest
import yaml
from orion_db.server import App
from orion_db.auth import is_loopback


def demo_setup(sandbox, role='employee'):
    app, cfg, root = sandbox
    settings = yaml.safe_load(cfg.path.read_text(encoding='utf-8'))
    # Set defaults so older YAML configurations can also run this test.
    authentication = settings.setdefault('security', {}).setdefault('authentication', {})
    authentication['mode'] = 'local_demo'
    demo = authentication.setdefault('local_demo', {})
    demo.setdefault('username', 'demo')
    demo.setdefault('loopback_only', True)
    demo.setdefault('allow_admin_actions', False)
    demo.setdefault('allow_live_database', False)
    demo.setdefault('allow_document_exports', False)
    demo['role'] = role
    cfg.path.write_text(yaml.safe_dump(settings, sort_keys=False), encoding='utf-8')
    return app, cfg, root


def session(app, ip='127.0.0.1'):
    login = app.process({'type': 'demo_login', 'client_id': 'demo-desktop',
                         'role': 'admin', 'username': 'admin'}, ip=ip)
    return {'client_id': 'demo-desktop', 'token': login['token']}, login


def chat(app, auth, message):
    thread = app.process({**auth, 'type': 'new_chat'})['thread_id']
    return app.process({**auth, 'type': 'chat', 'thread_id': thread, 'message': message})


def test_required_mode_does_not_accept_demo_login(sandbox):
    app, cfg, root = sandbox
    with pytest.raises(PermissionError):
        session(app)
    assert app.process({'type': 'login', 'client_id': 'normal', 'username': 'tester',
                        'password': 'CorrectHorseBattery42'})['role'] == 'admin'


def test_local_demo_without_users_and_role_cannot_be_spoofed(sandbox):
    app, cfg, root = demo_setup(sandbox)
    auth, login = session(app)
    assert login['type'] == 'login_ok' and login['role'] == 'employee'
    assert login['username'] == 'demo' and login['demo'] is True
    assert app.process({**auth, 'type': 'hello', 'role': 'admin'})['role'] == 'employee'
    assert app.auth.identity(auth['token'], auth['client_id'])['username'] == '__orion_local_demo__'
    with pytest.raises(PermissionError):
        app.process({'type': 'login', 'client_id': 'normal', 'username': 'tester',
                     'password': 'CorrectHorseBattery42'})
    with pytest.raises(PermissionError):
        app.process({'type': 'hello', 'client_id': 'another', 'token': auth['token']})


def test_remote_ip_and_nonloopback_server_denied(sandbox):
    app, cfg, root = demo_setup(sandbox)
    for ip in ('10.0.0.5', '192.168.1.2', 'example.com', '::ffff:192.168.1.2'):
        with pytest.raises(PermissionError):
            session(app, ip)
    auth, _ = session(app)
    with pytest.raises(PermissionError):
        app.process({**auth, 'type': 'hello'}, ip='10.0.0.5')
    settings = yaml.safe_load(cfg.path.read_text(encoding='utf-8'))
    settings['server']['host'] = '0.0.0.0'
    cfg.path.write_text(yaml.safe_dump(settings), encoding='utf-8')
    with pytest.raises(PermissionError):
        session(app)
    with pytest.raises(PermissionError):
        App(cfg, startup_sync=False)


def test_invalid_demo_settings_fail_closed(sandbox):
    app, cfg, root = demo_setup(sandbox)
    for key, value in [('role', 'admin'), ('allow_live_database', True),
                       ('allow_admin_actions', True), ('allow_document_exports', True),
                       ('loopback_only', False)]:
        settings = yaml.safe_load(cfg.path.read_text(encoding='utf-8'))
        settings['security']['authentication']['local_demo'][key] = value
        cfg.path.write_text(yaml.safe_dump(settings), encoding='utf-8')
        with pytest.raises(PermissionError):
            session(app)
        settings['security']['authentication']['local_demo'][key] = (
            'employee' if key == 'role' else True if key == 'loopback_only' else False)
        cfg.path.write_text(yaml.safe_dump(settings), encoding='utf-8')


def test_demo_restricts_privileged_actions_and_database(sandbox):
    app, cfg, root = demo_setup(sandbox)
    auth, _ = session(app)
    for action in ('document_review_queue', 'document_review_decide', 'sync_knowledge',
                   'db_connect', 'db_connect_saved', 'db_profiles', 'db_tables', 'db_status',
                   'db_preview', 'report_generate', 'report_generate_document',
                   'refresh_discovery', 'schema_catalog', 'discovery_status',
                   'plugin_anything', 'learning_feedback_review', 'model_status'):
        with pytest.raises(PermissionError):
            app.process({**auth, 'type': action})
    assert app.process({**auth, 'type': 'new_chat'})['type'] == 'chat_opened'


def test_demo_denies_planned_db_tools_and_legacy_db_fallback(sandbox, monkeypatch):
    app, cfg, root = demo_setup(sandbox)
    auth, _ = session(app)
    settings = yaml.safe_load(cfg.path.read_text(encoding='utf-8'))
    settings['features']['local_model'] = True
    settings['model']['enabled'] = True
    cfg.path.write_text(yaml.safe_dump(settings), encoding='utf-8')
    monkeypatch.setattr(app.model, 'plan_action', lambda *args: {
        'tool': 'generate_report', 'arguments': {'table': 'expenses'}})
    with pytest.raises(PermissionError):
        chat(app, auth, 'Export Excel report for expenses')
    with pytest.raises(PermissionError):
        app.agent.execute('list_databases', {}, 'show databases', auth['token'], 'employee')
    settings['features']['local_model'] = False
    settings['model']['enabled'] = False
    cfg.path.write_text(yaml.safe_dump(settings), encoding='utf-8')
    result = chat(app, auth, 'Create an Excel report from the live database')
    assert result['type'] == 'answer'
    assert 'report_ready' not in str(result)
    assert not app.live.status(auth['token'])['connected']


def test_demo_rag_honors_approved_employee_documents(sandbox):
    app, cfg, root = demo_setup(sandbox)
    (root / 'knowledge' / 'salary_secret.txt').write_text(
        'Employee salary is 9999999', encoding='utf-8')
    (root / 'knowledge' / 'leave_policy.txt').write_text(
        'Annual leave allowance is 12 days.', encoding='utf-8')
    app.knowledge.sync_once()
    app.document_access.grant_local(app.knowledge, 'leave_policy.txt', ['employee'])
    auth, _ = session(app)
    result = app.agent.execute('search_knowledge', {'query': 'annual leave allowance'},
                               'annual leave allowance', auth['token'], 'employee')
    assert result['sources'] == ['leave_policy.txt']
    result = app.agent.execute('search_knowledge', {'query': 'Employee salary 9999999'},
                               'Employee salary', auth['token'], 'employee')
    assert 'salary_secret.txt' not in str(result)
    assert '9999999' not in str(result)
    result = chat(app, auth, 'How many resumes?')
    assert result['document_count'] == 0


def test_mode_switch_revokes_old_sessions(sandbox):
    app, cfg, root = demo_setup(sandbox)
    auth, _ = session(app)
    settings = yaml.safe_load(cfg.path.read_text(encoding='utf-8'))
    settings['security']['authentication']['mode'] = 'required'
    cfg.path.write_text(yaml.safe_dump(settings), encoding='utf-8')
    with pytest.raises(PermissionError):
        app.process({**auth, 'type': 'hello'})
    login = app.process({'type': 'login', 'client_id': 'normal',
                         'username': 'tester', 'password': 'CorrectHorseBattery42'})
    assert login['role'] == 'admin'


def test_loopback_addresses():
    assert is_loopback('127.0.0.1') and is_loopback('::1')
    assert not is_loopback('127.0.0.2x')
    assert not is_loopback('localhost')  # peer must be an actual IP address


def test_demo_background_discovery_does_not_connect_databases(sandbox, monkeypatch):
    app, cfg, root = demo_setup(sandbox)
    settings = yaml.safe_load(cfg.path.read_text(encoding='utf-8'))
    settings['database']['profiles'] = [{'alias': 'example', 'backend': 'sqlite',
                                         'database': str(root / 'data.sqlite')}]
    cfg.path.write_text(yaml.safe_dump(settings), encoding='utf-8')
    def forbidden(*args, **kwargs):
        raise AssertionError('Demo mode attempted to connect to a live database')
    monkeypatch.setattr(app.live, '_connect', forbidden)
    assert app.discovery._scan_live() == ({}, [])
    assert app.discovery.refresh(documents=False, schemas=True)['status'] == 'completed'


def test_demo_tcp_handshake_on_loopback(sandbox):
    """Exercise real JSON-lines transport rather than only App.process calls."""
    import json
    import socket
    import threading
    from orion_db.server import TCPServer, Handler
    app, cfg, root = demo_setup(sandbox)
    with TCPServer(('127.0.0.1', 0), Handler) as server:
        server.app = app
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with socket.create_connection(server.server_address, timeout=3) as sock:
                sock.settimeout(5)
                reader = sock.makefile('rb')
                def send(packet):
                    sock.sendall((json.dumps(packet) + '\n').encode())
                    return json.loads(reader.readline())
                login = send({'type': 'demo_login', 'client_id': 'tcp-demo',
                              'role': 'admin'})
                assert login['type'] == 'login_ok'
                assert login['role'] == 'employee'
                auth = {'client_id': 'tcp-demo', 'token': login['token']}
                assert send({**auth, 'type': 'hello'})['role'] == 'employee'
                assert send({**auth, 'type': 'db_connect'})['type'] == 'error'
                assert send({**auth, 'type': 'new_chat'})['type'] == 'chat_opened'
        finally:
            server.shutdown()
            worker.join(timeout=5)
