from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from ..core.assistant import Assistant
app=FastAPI(title="Orianbelt API",version="1.2.0"); bot=None
class Request(BaseModel):
    prompt:str; max_new_tokens:int=350; temperature:float=.3
@app.get("/health")
def health(): return {"status":"ok","model":bot.name if bot else None}
@app.get("/v1/models")
def models(): return {"data":[{"id":bot.name}]} if bot else {"data":[]}
@app.post("/v1/generate")
def generate(r:Request):
    text=bot.ask(r.prompt,r.max_new_tokens,r.temperature)
    return {"model":bot.name,"text":text,"sources":[{"source":x["source"],"chunk":x["chunk"]} for x in bot.last_sources]}
def serve(name,models_dir="models",host="127.0.0.1",port=11435):
    global bot; bot=Assistant(name,models_dir); uvicorn.run(app,host=host,port=port)
