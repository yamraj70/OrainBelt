import json,math,re
from collections import Counter
from pathlib import Path
from ..ingest.documents import load_documents
RX=re.compile(r"[A-Za-z0-9][A-Za-z0-9_'’-]*")
def tokenize(t): return [m.group(0).lower() for m in RX.finditer(t)]
def chunk_text(t,size=220,overlap=40):
    w=t.split(); out=[]; step=max(1,size-overlap)
    for s in range(0,len(w),step):
        x=w[s:s+size]
        if len(x)<20 and out: break
        if x: out.append(" ".join(x))
        if s+size>=len(w): break
    return out
def build_index(folder,outfile,size=220,overlap=40):
    docs=load_documents(folder); chunks=[]; cid=0
    for d in docs:
        for n,t in enumerate(chunk_text(d["text"],size,overlap),1):
            chunks.append({"id":cid,"source":d["name"],"path":d["source"],"chunk":n,"text":t,"tokens":tokenize(t)}); cid+=1
    data={"version":1,"documents":len(docs),"chunks":chunks}
    p=Path(outfile); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
    return data
class LocalIndex:
    def __init__(self,path):
        d=json.loads(Path(path).read_text(encoding="utf-8")); self.chunks=d["chunks"]; self.document_count=d.get("documents",0)
        self.n=len(self.chunks); self.df=Counter(); self.tfs=[]; self.lens=[]
        for c in self.chunks:
            tf=Counter(c.get("tokens") or tokenize(c["text"])); self.tfs.append(tf); self.lens.append(sum(tf.values())); self.df.update(tf.keys())
        self.avg=sum(self.lens)/max(1,self.n)
    def search(self,q,k=4):
        terms=tokenize(q); scored=[]
        for i,c in enumerate(self.chunks):
            score=0.; tf=self.tfs[i]; dl=self.lens[i]
            for term in terms:
                if term in tf:
                    df=self.df[term]; idf=math.log(1+(self.n-df+.5)/(df+.5)); f=tf[term]
                    score+=idf*(f*2.5)/(f+1.5*(.25+.75*dl/max(1,self.avg)))
            if score>0: scored.append((score,c))
        scored.sort(key=lambda x:x[0],reverse=True)
        return [dict(c,score=s) for s,c in scored[:k]]
