from pathlib import Path
import pytest
import yaml
from orion_db.config import Config, DEFAULT_CONFIG
from orion_db.server import App


@pytest.fixture
def sandbox(tmp_path):
    data = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding='utf-8'))
    root = tmp_path
    (root / 'knowledge').mkdir()
    (root / 'knowledge' / 'catalog.txt').write_text('Database: finance_reporting\nEngine: SQL Server\nTable: expenses\nPurpose: monthly finance reporting.\n', encoding='utf-8')
    data['knowledge']['folder'] = str(root / 'knowledge')
    data['knowledge']['index_file'] = str(root / 'index.json')
    data['document_classification']['registry_file'] = str(root / 'classifications.json')
    data['document_access']['registry_file'] = str(root / 'document_access.json')
    data['knowledge']['schema_catalog_file'] = str(root / 'catalog.json')
    data['reports']['output_directory'] = str(root / 'reports')
    data['learning']['feedback_file'] = str(root / 'feedback.sqlite')
    data['security']['users_file'] = str(root / 'users.sqlite')
    data['security']['history_file'] = str(root / 'history.sqlite')
    data['logging']['directory'] = str(root / 'logs')
    data['database']['profiles'] = []
    data['model']['enabled'] = False
    data['features']['local_model'] = False
    path = root / 'config.yaml'
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding='utf-8')
    cfg = Config(path)
    app = App(cfg)
    app.auth.create_user('tester', 'CorrectHorseBattery42', 'admin')
    return app, cfg, root


@pytest.fixture
def identity(sandbox):
    app, cfg, root = sandbox
    client = 'test-client-1'
    response = app.process({'type': 'login', 'client_id': client, 'username': 'tester', 'password': 'CorrectHorseBattery42'})
    return app, {'client_id': client, 'token': response['token']}, root
