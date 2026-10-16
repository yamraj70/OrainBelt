# ORION Database Enterprise v2.0 — release notes

Built from the user's three reference Python files (Orianbelt v26 desktop, v26 TCP server, and v18 knowledge sync). This is a **standalone database-only redesign**, not a copy of their legacy code or a v0.9.x patch.

## Verified in the build environment

- 27 automated backend tests passed (Python 3.13 Linux test environment).
- All application Python modules compiled without syntax errors.
- Real local SQLite demo connection, metadata listing, bounded previews, and write prevention tested.
- TCP JSON-lines request/response, employee login, per-user chat history, TXT sync, role checks, password redaction, and YAML model path changes tested.
- SQL Server Windows integrated authentication connection string construction tested with a mock; actual ODBC connection not tested.

## Not verified

- PySide6 desktop on Windows (PySide6 was unavailable in this build environment).
- Real SQL Server or PostgreSQL connection against a customer database.
- Actual GGUF inference and GPU acceleration.
- Inno Setup compilation, Windows packaging, code signing, and clean-PC installation.

## Design decisions

- Model is **off by default**. Document-grounded responses work without `llama_cpp`, avoiding the previous missing-dependency issue.
- Desktop reads TCP responses in a background Qt thread and does not impose a short AI-response read timeout.
- Knowledge sync is startup + manual only; there is no recurring background monitor.
- All normal application configuration is in `config/orion.yaml`; passwords are never put in YAML.
- The original v26 visual structure is preserved, while removed feature buttons and routes are omitted.
