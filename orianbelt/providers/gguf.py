from pathlib import Path
class GGUFProvider:
    def __init__(self,path,n_ctx=4096,n_threads=None):
        p=Path(path)
        if not p.exists(): raise FileNotFoundError(f"GGUF model not found: {p}")
        try: from llama_cpp import Llama
        except ImportError as e: raise RuntimeError("Install llama-cpp-python first.") from e
        kw={"model_path":str(p),"n_ctx":n_ctx,"verbose":False}
        if n_threads: kw["n_threads"]=n_threads
        self.llm=Llama(**kw)
    def chat(self,messages,max_tokens=350,temperature=.3):
        r=self.llm.create_chat_completion(messages=messages,max_tokens=max_tokens,temperature=temperature,top_p=.9)
        return r["choices"][0]["message"]["content"].strip()
