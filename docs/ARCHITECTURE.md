# ORION Database Edition — architecture and message flow

Desktop (PySide6, v26-style sidebar + themes) ↔ JSON-lines TCP ↔ Authenticated Server ↔ Router

- **TXT question** → Knowledge index (startup/manual sync only) → grounded source excerpts → optional local GGUF summarizer → answer + source filenames.
- **Connect DB** → client dialog and approval → authenticated `db_connect` → live connector → `SELECT 1` verification → verified status. Never silently connect from a TXT catalog.
- **Browse DB** → `db_tables` metadata → select schema/table → `db_preview` with validated identifier and configured limit → live rows (never written to event logs).
- **Chat history** → local SQLite server database keyed by authenticated username.
- **Logs** → JSONL request/response metadata and separate errors, with credential redaction.
- **New features** → trusted plugin registry (`plugin_*` actions), YAML allowlist, explicit roles.

TCP protocol compatible in spirit with v26 (`login`, `hello`, `new_chat`, `open_chat`, `chat`, `answer`, `chats`) but not wire-compatible with removed legacy transfer/invoice/SQL v25 actions. The v26 desktop cannot be dropped in unchanged: this release provides a new desktop preserving its visual structure.

Source reference files from the user were reviewed; the old modules import unavailable `enterprise`/`orianbelt` packages, so this is a standalone implementation rather than a blind copy. No original customer secrets, credentials or data were bundled.
