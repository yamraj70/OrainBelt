# ORION Belt Enterprise — Database Edition v2.0

A **database-focused**, local-first enterprise assistant rebuilt using the three user-supplied Orianbelt v26 desktop/TCP server and v18 knowledge-sync files as **design references**. This is a clean standalone application, not an import-compatible patch for the legacy `enterprise` and `orianbelt` packages.

## What is included

- **Familiar PySide6 desktop:** Midnight/Ocean/Forest/Light themes, left sidebar, recent conversations, employee login, TCP connection indicator, chat, settings.
- **TCP server:** JSON-line messages (`login`, `hello`, `new_chat`, `open_chat`, `chat`, `delete_chat`, `db_*`, `sync_knowledge`) with per-employee chat history and authentication.
- **Documented database Q&A:** Syncs only `.txt` files from the YAML-approved folder; startup and explicit manual synchronization. No background scanning.
- **Live database:** Explicit connection dialog and real `SELECT 1` verification for SQLite, PostgreSQL and Microsoft SQL Server. Credentials are transient and not saved. Table discovery and bounded read-only previews; no arbitrary SQL execution.
- **YAML:** One file controls UI, TCP, model, knowledge, database profiles, roles, logging, and plugin modules.
- **Logs:** `logs/events.jsonl` for requests/responses, sync, and DB metadata events; `logs/errors.log` for redacted diagnostic errors.
- **Optional Qwen3 GGUF:** Disabled by default to avoid missing-`llama_cpp` and slow model timeout problems. Without it, grounded TXT extraction works immediately.
- **Extensibility:** Explicit `plugins.modules` in YAML plus registry and `plugins/example_feature.py`.

**Not included:** voice recognition, browser automation, LAN employee file transfer, background file monitoring, invoice/HR agent tools, MySQL live connector, arbitrary SQL execution, or a bundled GGUF model. The application will not invent live database results from documents.

## Windows installation

Requires **Python 3.11 64-bit**. Extract into a new folder; do not overwrite the old project.



Choose a password of at least 10 characters. No default user or password is included. Then run in separate terminals:

```powershell
.\scripts\START_SERVER.bat
.\scripts\START_DESKTOP.bat
```

Sign in with the account you created. The demo SQLite file is created by the install script (unless it already exists).

## Demonstration

1. Ask **“Which databases do we have?”** → names documented in TXT files, with filenames, *not* claims of live connections.
2. Ask **“What tables are documented for finance_reporting?”** → relevant synchronized TXT excerpts with source filenames.
3. Ask **“Connect to database”** → explicit connection dialog; choose the `demo_sqlite` profile, verify the path and approve.
4. Choose **Browse Tables** → select `main.assets` and preview actual demo rows.
5. Add or edit `.txt` files under `knowledge/`, then click **Sync Knowledge** (admin only) or run `python -m orion_db.cli sync`. No background polling occurs.

The sample documents and demo database contain **fictional data only**.

## Configure everything in YAML

Edit `config/orion.yaml`. Settings are re-read on subsequent requests. Listener host/port and TLS changes require restarting the server; desktop connection changes require restarting the desktop. Model options reload at the next model invocation; the GGUF is loaded lazily. UI appearance changes are written back to YAML.

Important settings:

```yaml
knowledge:
  folder: knowledge  # Or D:/OrianbeltKnowledge/Databases
  auto_sync_on_start: true
  sync_interval_seconds: 0  # Disabled by design
features:
  knowledge: true
  live_database: true
  local_model: false
model:
  enabled: false
  path: models/Qwen3-4B-Q4_K_M.gguf
logging:
  log_chat_text: true
  log_answer_text: true
  log_db_rows: false
```

For a different config path, set environment variable `ORION_CONFIG` or pass `--config path\to\file.yaml` before the CLI subcommand.

### Database drivers

SQLite works with standard Python; the demo SQLite database is created locally. PostgreSQL requires `pip install -r requirements-postgresql.txt`; SQL Server requires `pip install -r requirements-sqlserver.txt` **and Microsoft ODBC Driver 18**. The live SQL Server dialog supports SQL login or Windows integrated authentication, as allowed by YAML. Driver availability and access to your actual server must be verified on Windows. Use **read-only database accounts**, network TLS, and least-privilege grants. The app restricts its generated queries to metadata and SELECT previews, but the database account remains the final authorization boundary.

Connection profiles in YAML are **templates**, not active connections. Never put database passwords in YAML. Live database credentials are entered in the desktop and held only for the active TCP session. They are dropped when the client disconnects.

### Optional local model

The project runs without a model. If you want Qwen3 answers summarized from retrieved documents, install a compatible prebuilt `llama-cpp-python` wheel into the **server** virtual environment, put the GGUF at the configured path, then set **both** `features.local_model: true` and `model.enabled: true`. Model answers are only generated when TXT retrieval finds relevant sources. If model loading fails and `fallback_to_documents_on_error` is true, ORION returns document excerpts instead. The desktop uses a background socket reader and has **no inference response timeout**; slow inference may still take time.

The model uses the GGUF's embedded chat template rather than forcing ChatML. The default `max_tokens` is configurable.

### Authentication and TCP security

Accounts are stored as salted scrypt hashes in `data/users.sqlite`. Roles: `viewer` (document Q&A), `analyst` (Q&A and live database), `admin` (also knowledge synchronization). Chat history is separated by employee account in `data/chat_history.sqlite`. Auth tokens expire and are invalidated when the TCP connection closes. TCP defaults to `127.0.0.1:5050` (local machine only). To bind to a LAN interface, configure **TLS cert/key on server and trusted CA on client**. Never expose the raw TCP port to the internet. The original legacy security modules are intentionally not imported.

### Logging and privacy

`events.jsonl` records timestamps, request IDs, request/response action types, durations, document chat text and answer text (enabled in the demo YAML), knowledge sync results, model errors and DB metadata actions. `errors.log` records redacted exception descriptions. Passwords/tokens and actual live DB rows are excluded. **Disable `log_chat_text` and `log_answer_text` for sensitive deployments**, set filesystem permissions, and apply retention rules. This is a demonstrator, not a complete enterprise compliance system.

### Extending ORION

Create `plugins/my_feature.py` with a `register(registry)` function and register an action name beginning with `plugin_`. Add `plugins.my_feature` to `plugins.modules` in YAML, then restart the server. Plugins are trusted Python code with server permissions: only administrators should install/enable them. Add UI buttons and feature flags for new client-side features separately. Core DB/knowledge features are isolated in `orion_db/database.py` and `orion_db/knowledge.py`.

## Testing

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q orion_db
```

Automated tests cover backend behavior, YAML reload, TXT sync, auth, DB SQLite read-only access, TCP protocol, plugin registration, and log redaction. **PySide6 desktop appearance, actual remote PostgreSQL/SQL Server connectivity, GGUF inference, and Windows installer compilation require separate Windows tests.**

## Installer

`installer/ORION_Database_Enterprise.iss` is an **Inno Setup 6 build script**, not a compiled installer. Run `installer/BUILD_INSTALLER.bat` on Windows with Inno Setup installed. This source installer still requires Python 3.11 and internet/offline dependency packages on the client; it does not bundle Python or the model. For production, build and sign a self-contained executable and test on a clean Windows VM.
