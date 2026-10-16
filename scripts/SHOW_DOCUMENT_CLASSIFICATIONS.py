"""Run locally as an administrator to inspect v2.7 step-1 suggestions."""
import json
from pathlib import Path

from orion_db.config import Config, resolve_path


def main():
    cfg = Config()
    options = cfg.get().get('document_classification', {})
    path = resolve_path(options.get('registry_file', 'data/document_classifications.json'))
    if not path.is_file():
        print('No classification registry yet. Start the server or run: python -m orion_db.cli sync')
        return
    documents = json.loads(path.read_text(encoding='utf-8')).get('documents', {})
    for name, item in sorted(documents.items()):
        print(f"{name} | {item.get('department')} | {item.get('document_type')} | "
              f"{item.get('sensitivity')} | {item.get('classification_status')} | "
              f"approval={item.get('approval_status')}")
    print(f'Total: {len(documents)}. Suggestions only; NOT access permissions.')


if __name__ == '__main__':
    main()
