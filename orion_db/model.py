"""Local GGUF model for general questions and grounded document summarization."""
import threading
from .config import resolve_path
from .response import expanded_requested


class Model:
    def __init__(self, cfg, audit):
        self.cfg, self.audit = cfg, audit
        self._lock = threading.RLock()
        self._model = None
        self._signature = None

    def status(self):
        options = self.cfg.get()['model']
        path = resolve_path(options.get('path', 'models/model.gguf'))
        return {'enabled': bool(options.get('enabled', False) and self.cfg.get()['features'].get('local_model', False)),
                'path': str(path), 'exists': path.is_file(), 'loaded': self._model is not None,
                'backend': options.get('backend', 'llama_cpp')}

    def _load(self):
        options = self.cfg.get()['model']
        if options.get('backend') != 'llama_cpp':
            raise RuntimeError('Only llama_cpp is supported for local GGUF inference')
        path = resolve_path(options['path'])
        if not path.is_file():
            raise FileNotFoundError('GGUF model file missing: ' + str(path))
        signature = (str(path), path.stat().st_mtime_ns, int(options.get('context_size', 2048)), int(options.get('gpu_layers', 0)), int(options.get('threads', 4)))
        if self._model is not None and signature == self._signature:
            return self._model
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise RuntimeError('llama-cpp-python is not installed in the SERVER Python environment. See README optional model setup.') from exc
        self._model = None
        self._signature = None
        model = Llama(model_path=str(path), n_ctx=signature[2], n_gpu_layers=signature[3], n_threads=signature[4], verbose=False)
        self._model, self._signature = model, signature
        self.audit.event('model_loaded', path=str(path))
        return model

    def summarize(self, question, hits):
        opts = self.cfg.get()['model']
        with self._lock:
            model = self._load()
            context = '\n\n'.join(f'[Source: {h["source"]}]\n{h["text"]}' for h in hits)[:12000]
            response = model.create_chat_completion(messages=[
                {'role': 'system', 'content': opts.get('system_prompt', 'Answer only from the supplied text. Cite sources.')},
                {'role': 'user', 'content': 'DATABASE DOCUMENTATION:\n' + context + '\n\nQUESTION:\n' + question}
            ], max_tokens=int(opts.get('max_tokens', 256)), temperature=float(opts.get('temperature', 0.1)), top_p=float(opts.get('top_p', 0.9)))
            text = str(response['choices'][0]['message'].get('content') or '').strip()
            if not text:
                raise RuntimeError('Local model produced an empty answer')
            return text + '\n\nSource documents: ' + ', '.join(dict.fromkeys(h['source'] for h in hits)) + '\nNot a live database query.'

    def answer_general(self, question):
        """Use Qwen3 pretrained knowledge, without requiring document matches."""
        opts = self.cfg.get()['model']
        general_prompt = self.cfg.get().get('ai', {}).get('general_prompt',
            'Answer general-knowledge questions clearly and accurately. '
            'Do not claim to know internal company facts or live database records.')
        with self._lock:
            model = self._load()
            response = model.create_chat_completion(messages=[
                {'role': 'system', 'content': general_prompt + ' Respond in 1–2 sentences and at most 60 words unless asked for details. Never output reasoning or <think> tags.'},
                {'role': 'user', 'content': question.strip() + '\n/no_think'}
            ], max_tokens=(int(opts.get('max_tokens', 256)) if expanded_requested(question) else
                           min(int(opts.get('max_tokens', 256)), 256)),
               temperature=float(opts.get('temperature', 0.1)),
               top_p=float(opts.get('top_p', 0.9)))
            text = str(response['choices'][0]['message'].get('content') or '').strip()
            # Qwen3 may include reasoning markers depending on the GGUF template.
            import re
            text = re.sub(r'<think>.*?</think>', '', text, flags=re.S).strip()
            if not text or text.startswith('<think>'):
                raise RuntimeError('Qwen3 returned no final answer. Confirm /no_think support in the model chat template or increase model.max_tokens.')
            return text

    def plan_action(self, question, instructions, agent_settings=None):
        """Ask Qwen3 for one JSON plan; JSON is validated again by Agent."""
        import json
        import re
        opts = self.cfg.get()['model']
        agent_settings = agent_settings or {}
        with self._lock:
            model = self._load()
            params = dict(messages=[
                {'role': 'system', 'content': instructions},
                {'role': 'user', 'content': question.strip() + '\n/no_think'},
            ], max_tokens=max(100, min(512, int(agent_settings.get('max_plan_tokens', 220)))),
               temperature=0.0, top_p=0.9)
            try:
                response = model.create_chat_completion(**params, response_format={'type': 'json_object'})
            except (TypeError, ValueError):
                # Some llama-cpp-python builds do not accept response_format.
                response = model.create_chat_completion(**params)
            content = str(response['choices'][0]['message'].get('content') or '')
        content = re.sub(r'<think>.*?</think>', '', content, flags=re.I | re.S).strip()
        if content.startswith('```'):
            content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content).strip()
        # Never try to repair arbitrary non-JSON code or model prose.
        if len(content) > 4000:
            raise ValueError('Planner output too long')
        try:
            return json.loads(content)
        except (ValueError, TypeError) as exc:
            raise ValueError('Qwen3 did not return valid JSON') from exc
