"""Server, desktop, account setup, knowledge sync, and demo DB utilities."""
import argparse
import getpass
import json
import sqlite3
from pathlib import Path

from .config import Config, resolve_path
from .auth import ROLES


def create_demo_db(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError('Demo DB already exists; refusing to overwrite: ' + str(path))
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE assets (asset_id INTEGER PRIMARY KEY, asset_name TEXT, department TEXT, purchase_year INTEGER)')
        db.executemany('INSERT INTO assets VALUES (?,?,?,?)', [
            (101, 'Laptop A', 'IT', 2024), (102, 'Monitor B', 'Finance', 2023), (103, 'Printer C', 'Operations', 2022)])
        db.execute('CREATE TABLE locations (location_id INTEGER PRIMARY KEY, city TEXT, asset_id INTEGER)')
        db.executemany('INSERT INTO locations VALUES (?,?,?)', [(1, 'Hyderabad', 101), (2, 'Pune', 102), (3, 'Bengaluru', 103)])
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description='ORION Database Enterprise')
    parser.add_argument('--config', help='Alternate YAML configuration file')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('server')
    sub.add_parser('desktop')
    create = sub.add_parser('create-user')
    create.add_argument('username')
    create.add_argument('--role', choices=sorted(ROLES), default='analyst')
    setrole = sub.add_parser('set-user-role', help='Trusted local role management; stop the server first')
    setrole.add_argument('username')
    setrole.add_argument('--role', choices=sorted(ROLES), required=True)
    sub.add_parser('sync')
    sub.add_parser('status')
    sub.add_parser('discover-now')
    sub.add_parser('catalog')
    save = sub.add_parser('save-db-password')
    save.add_argument('alias', help='Approved database profile alias in YAML')
    grant = sub.add_parser('grant-document', help='Trusted local ACL maintenance; stop the server first')
    grant.add_argument('source', help='Exact relative filename from knowledge index')
    grant.add_argument('--roles', nargs='+', choices=sorted(ROLES - {'admin'}), required=True)
    revoke = sub.add_parser('revoke-document', help='Trusted local ACL maintenance; stop the server first')
    revoke.add_argument('source')
    sub.add_parser('create-demo-db')
    args = parser.parse_args(argv)
    cfg = Config(args.config)
    if args.command == 'server':
        from .server import serve
        serve(cfg)
    elif args.command == 'desktop':
        from .desktop import main as desktop_main
        desktop_main(cfg)
    elif args.command == 'create-user':
        from .auth import Auth
        pw = getpass.getpass('Password (at least 10 characters): ')
        if pw != getpass.getpass('Confirm password: '):
            parser.error('Passwords do not match')
        Auth(cfg).create_user(args.username, pw, args.role)
        print('Employee account created:', args.username, 'role:', args.role)
    elif args.command == 'set-user-role':
        from .auth import Auth
        print(json.dumps(Auth(cfg).set_role(args.username, args.role), indent=2))
    elif args.command == 'sync':
        from .server import App
        app = App(cfg, startup_sync=False)
        result = app.discovery.refresh(documents=True, schemas=False)
        print(json.dumps(result, indent=2))
    elif args.command == 'status':
        from .logging_utils import Audit
        from .knowledge import Knowledge
        from .model import Model
        a = Audit(cfg)
        from .schema_catalog import SchemaCatalog
        print(json.dumps({'config': str(cfg.path), 'knowledge': Knowledge(cfg, a).status(),
                          'catalog': SchemaCatalog(cfg, a).status(), 'model': Model(cfg, a).status()}, indent=2))
    elif args.command == 'discover-now':
        from .server import App
        app = App(cfg, startup_sync=False)
        print(json.dumps(app.discovery.refresh(documents=True, schemas=True), indent=2))
    elif args.command == 'catalog':
        from .server import App
        app = App(cfg, startup_sync=False)
        app.discovery.refresh(documents=True, schemas=False)
        print(json.dumps(app.discovery.catalog.entries(), indent=2))
    elif args.command == 'save-db-password':
        profile = next((p for p in cfg.get()['database'].get('profiles', []) if p.get('alias') == args.alias), None)
        if not profile:
            parser.error('No approved profile with that alias exists in YAML')
        if profile.get('backend') == 'sqlite' or profile.get('auth_mode') == 'windows':
            parser.error('This connection does not need a stored SQL password')
        try:
            import keyring
        except ImportError:
            parser.error('Install keyring first: python -m pip install keyring')
        secret = getpass.getpass('Database password (hidden): ')
        if not secret:
            parser.error('Empty password is not allowed')
        keyring.set_password('ORION_DB', str(profile.get('credential_ref') or args.alias), secret)
        print('Saved password in the operating-system credential store for:', args.alias)
    elif args.command in ('grant-document', 'revoke-document'):
        from .access_control import DocumentAccess
        from .logging_utils import Audit
        from .knowledge import Knowledge
        access = DocumentAccess(cfg, Audit(cfg))
        if args.command == 'grant-document':
            knowledge = Knowledge(cfg, Audit(cfg))
            if knowledge.sync_once() == 'error':
                parser.error('Knowledge sync failed: ' + knowledge.last_error)
            print(json.dumps(access.grant_local(knowledge, args.source, args.roles), indent=2))
        else:
            access.revoke_local(args.source)
            print('Revoked document permissions for:', args.source)
    elif args.command == 'create-demo-db':
        print('Created:', create_demo_db(resolve_path('data/demo_assets.sqlite')))


if __name__ == '__main__':
    main()
