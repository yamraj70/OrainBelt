from pathlib import Path
import re
SUPPORTED={".txt",".pdf",".docx"}
def clean_text(t):
    t=t.replace("\x00"," ")
    t=re.sub(r"[ \t]+"," ",t)
    return re.sub(r"\n{3,}","\n\n",t).strip()
def read_document(path):
    p=Path(path); e=p.suffix.lower()
    if e==".txt": return clean_text(p.read_text(encoding="utf-8",errors="ignore"))
    if e==".pdf":
        from pypdf import PdfReader
        return clean_text("\n".join(x.extract_text() or "" for x in PdfReader(str(p)).pages))
    if e==".docx":
        from docx import Document
        return clean_text("\n".join(x.text for x in Document(str(p)).paragraphs))
    raise ValueError(f"Unsupported: {e}")
def load_documents(folder):
    root=Path(folder)
    if not root.exists(): raise FileNotFoundError(root)
    out=[]
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in SUPPORTED:
            t=read_document(p)
            if t: out.append({"source":str(p),"name":p.name,"text":t})
    if not out: raise ValueError("No TXT, PDF, or DOCX documents found.")
    return out
