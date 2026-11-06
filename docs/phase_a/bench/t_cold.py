import faulthandler;faulthandler.enable()
from bc import *
from bc import _extract_tokens
import threading,sys
from src.llm.local_llamacpp import LocalLlamaCppClient
mode=sys.argv[1]; nt=int(sys.argv[2])
c=LocalLlamaCppClient(model_path=GGUF,model="q")
if mode=="warm":
    c.chat([{"role":"user","content":"hi"}]); print("warmed",flush=True)
P=pool()[:nt*6]; res=[];lock=threading.Lock()
def w(i):
    for x in P[i::nt]:
        try:
            o=c.chat([{"role":"system","content":SINGLE},{"role":"user","content":x}],temperature=0)
            with lock: res.append(("ok",x,o))
        except BaseException as e:
            with lock: res.append(("err",x,repr(e)[:160]))
ts=[threading.Thread(target=w,args=(i,)) for i in range(nt)]
t0=time.time();[t.start() for t in ts];[t.join() for t in ts]
print("RESULT",json.dumps(dict(mode=mode,nt=nt,wall=round(time.time()-t0,1),ok=sum(r[0]=="ok" for r in res),err=sum(r[0]=="err" for r in res),errs=[r[2] for r in res if r[0]=="err"][:3],nocyr=[(r[1],r[2][:60]) for r in res if r[0]=="ok" and not CY.search(r[2])][:5]),ensure_ascii=False),flush=True)
