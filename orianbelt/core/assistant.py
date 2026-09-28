import json
from pathlib import Path
from ..providers.gguf import GGUFProvider
from ..rag.index import LocalIndex
SYSTEM="""You are Orianbelt, a local document-aware AI assistant.
Reply in clear, grammatical English. Use supplied document context as the primary source for document questions.
Do not invent document facts. If context is insufficient, say so clearly. Use conversation history for follow-up questions."""
class Assistant:
    def __init__(self,name,models_dir="models"):
        self.name=name; self.dir=Path(models_dir)/name; f=self.dir/"assistant.json"
        if not f.exists(): raise FileNotFoundError(f"Assistant '{name}' does not exist. Run create first.")
        self.config=json.loads(f.read_text(encoding="utf-8"))
        self.provider=GGUFProvider(self.config["gguf"],int(self.config.get("n_ctx",4096)),self.config.get("n_threads"))
        ix=self.dir/"knowledge.json"; self.index=LocalIndex(ix) if ix.exists() else None
        self.history=[]; self.last_sources=[]
    def clear(self): self.history=[]; self.last_sources=[]
    def info(self):
        return {"name":self.name,"gguf":self.config["gguf"],"documents":self.index.document_count if self.index else 0,
                "chunks":len(self.index.chunks) if self.index else 0,"history_messages":len(self.history)}
    def ask(self,q,max_tokens=350,temperature=.3):
        hits=self.index.search(q,4) if self.index else []; self.last_sources=hits
        context="\n\n".join(f"[Source {i}: {x['source']}, chunk {x['chunk']}]\n{x['text']}" for i,x in enumerate(hits,1))
        content=(f"DOCUMENT CONTEXT:\n{context}\n\nUSER QUESTION:\n{q}\n\nAnswer in clear English using relevant context." if hits else q)
        msgs=[{"role":"system","content":SYSTEM}]+self.history[-8:]+[{"role":"user","content":content}]
        ans=self.provider.chat(msgs,max_tokens,temperature)
        self.history += [{"role":"user","content":q},{"role":"assistant","content":ans}]
        return ans
