"""Optional local semantic retrieval with incremental, persistent vector caching.

Install requirements-rag.txt and provide a local SentenceTransformer model path.
Documents are embedded locally. Never uploads text to a hosted service.
"""
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from .config import resolve_path


class SemanticIndex:
    def __init__(self, cfg):
        self.cfg = cfg
        self._model = None
        self._model_name = None
        self._lock = threading.RLock()

    def _settings(self):
        return self.cfg.get().get('rag', {})

    def _get_model(self):
        opts = self._settings()
        model_name = str(opts.get('embedding_model', '')).strip()
        if not model_name:
            raise RuntimeError('Set rag.embedding_model to a local embedding model directory')
        model_path = resolve_path(model_name)
        if not model_path.is_dir():
            raise FileNotFoundError('Local embedding model missing: ' + str(model_path))
        if self._model is None or self._model_name != str(model_path):
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError('Install optional dependencies: pip install -r requirements-rag.txt') from exc
            self._model = SentenceTransformer(str(model_path), local_files_only=True)
            self._model_name = str(model_path)
        return self._model

    def _connect(self):
        path = resolve_path(self._settings().get('cache_file', 'data/semantic_vectors.sqlite'))
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=20)
        conn.execute('CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, embedding TEXT NOT NULL)')
        return conn

    def search(self, question, chunks, allowed_sources, limit):
        """Returns [(score, chunk)] from cached/locally embedded text."""
        with self._lock:
            model = self._get_model()
            import numpy as np
            # Stable hash makes unchanged chunks reusable after hourly sync.
            allowed = [c for c in chunks if c['source'] in allowed_sources]
            max_chunks = max(1, min(20000, int(self._settings().get('max_indexed_chunks', 5000))))
            allowed = allowed[:max_chunks]
            if not allowed:
                return []
            model_id = self._model_name
            keys = [hashlib.sha256((model_id + '\x00' + c['source'] + '\x00' + c['text']).encode('utf-8')).hexdigest()
                    for c in allowed]
            with self._connect() as conn:
                cached = {}
                for key in keys:
                    row = conn.execute('SELECT embedding FROM vectors WHERE key=?', (key,)).fetchone()
                    if row:
                        cached[key] = json.loads(row[0])
                missing = [(key, c) for key, c in zip(keys, allowed) if key not in cached]
                batch = max(1, min(64, int(self._settings().get('batch_size', 16))))
                for start in range(0, len(missing), batch):
                    part = missing[start:start+batch]
                    vecs = model.encode([c['text'] for _, c in part], normalize_embeddings=True)
                    for (key, _), vec in zip(part, vecs):
                        arr = [float(v) for v in vec]
                        cached[key] = arr
                        conn.execute('INSERT OR REPLACE INTO vectors(key,embedding) VALUES (?,?)',
                                     (key, json.dumps(arr, separators=(',', ':'))))
            query = np.asarray(model.encode([question], normalize_embeddings=True)[0], dtype=np.float32)
            vectors = np.asarray([cached[key] for key in keys], dtype=np.float32)
            sims = vectors @ query
            threshold = float(self._settings().get('minimum_semantic_similarity', 0.3))
            matches = sorted(((float(s), c) for s, c in zip(sims, allowed) if s >= threshold),
                             key=lambda item: item[0], reverse=True)
            return matches[:max(1, limit)]
