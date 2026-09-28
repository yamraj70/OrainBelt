import argparse,json
from pathlib import Path
def interactive(name,models_dir):
    from ..core.assistant import Assistant
    print(f"Loading {name}..."); bot=Assistant(name,models_dir); info=bot.info()
    print(f"\nOrianbelt v1.2\nAssistant: {name}\nKnowledge: {info['documents']} document(s), {info['chunks']} chunk(s)\nType /help for commands.\n")
    while True:
        try: q=input(f"{name} >>> ").strip()
        except (EOFError,KeyboardInterrupt): print("\nGoodbye."); break
        if not q: continue
        c=q.lower()
        if c in ("/exit","/quit"): print("Goodbye."); break
        if c=="/help": print("/help /sources /clear /info /exit"); continue
        if c=="/clear": bot.clear(); print("Conversation history cleared."); continue
        if c=="/info":
            for k,v in bot.info().items(): print(f"{k}: {v}")
            continue
        if c=="/sources":
            if not bot.last_sources: print("No sources for previous answer.")
            else:
                for i,x in enumerate(bot.last_sources,1): print(f"{i}. {x['source']} (chunk {x['chunk']}, score {x['score']:.3f})")
            continue
        try: print(f"\nOrianbelt >>> {bot.ask(q)}\n")
        except Exception as e: print(f"\nError: {e}\n")
def main():
    p=argparse.ArgumentParser(prog="orianbelt"); p.add_argument("--models-dir",default="models"); s=p.add_subparsers(dest="cmd",required=True)
    x=s.add_parser("create"); x.add_argument("name"); x.add_argument("--gguf",required=True); x.add_argument("--n-ctx",type=int,default=4096); x.add_argument("--n-threads",type=int)
    x=s.add_parser("learn"); x.add_argument("name"); x.add_argument("data"); x.add_argument("--chunk-words",type=int,default=220); x.add_argument("--overlap-words",type=int,default=40)
    x=s.add_parser("run"); x.add_argument("name")
    x=s.add_parser("ask"); x.add_argument("name"); x.add_argument("question")
    x=s.add_parser("serve"); x.add_argument("name"); x.add_argument("--host",default="127.0.0.1"); x.add_argument("--port",type=int,default=11435)
    s.add_parser("models"); a=p.parse_args(); root=Path(a.models_dir)
    if a.cmd=="create":
        d=root/a.name; d.mkdir(parents=True,exist_ok=True)
        cfg={"name":a.name,"gguf":str(Path(a.gguf).resolve()),"n_ctx":a.n_ctx,"n_threads":a.n_threads}
        (d/"assistant.json").write_text(json.dumps(cfg,indent=2),encoding="utf-8"); print(f"Created assistant: {a.name}"); return
    if a.cmd=="learn":
        from ..rag.index import build_index
        d=root/a.name
        if not (d/"assistant.json").exists(): raise SystemExit("Assistant does not exist. Run create first.")
        z=build_index(a.data,d/"knowledge.json",a.chunk_words,a.overlap_words)
        print(f"Indexed {z['documents']} document(s) into {len(z['chunks'])} chunk(s)."); return
    if a.cmd=="run": interactive(a.name,a.models_dir); return
    if a.cmd=="ask":
        from ..core.assistant import Assistant
        print(Assistant(a.name,a.models_dir).ask(a.question)); return
    if a.cmd=="serve":
        from ..api.server import serve
        serve(a.name,a.models_dir,a.host,a.port); return
    if a.cmd=="models":
        found=[d.name for d in root.iterdir() if d.is_dir() and (d/"assistant.json").exists()] if root.exists() else []
        print("\n".join(sorted(found)) if found else "No assistants found.")
if __name__=="__main__": main()
