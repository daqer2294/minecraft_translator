# Q4: потокобезопасность in-process клиента (как 6 воркеров mirrorer на loose-файлах). Отдельный процесс: падение ожидаемо.
from bc import *
from bc import _coerce_json_array,_extract_tokens
import threading
from src.llm.local_llamacpp import LocalLlamaCppClient
import llama_cpp
n=[0]; O=llama_cpp.Llama
class W(O):
    def __init__(s,*a,**k):
        n[0]+=1; super().__init__(*a,**k)
import src.llm.local_llamacpp as M
llama_cpp.Llama=W
c=LocalLlamaCppClient(model_path=GGUF,model="q")
P=pool()[:24]; res=[];lock=threading.Lock()
def w(i):
    for x in P[i::3]:
        t=time.time()
        try:
            o=c.chat([{"role":"system","content":SINGLE},{"role":"user","content":x}],temperature=0)
            with lock: res.append(("ok",x,o,time.time()-t))
        except Exception as e:
            with lock: res.append(("err",x,repr(e)[:120],time.time()-t))
ts=[threading.Thread(target=w,args=(i,)) for i in range(3)]
t0=time.time(); [t.start() for t in ts]; [t.join() for t in ts]
print("RESULT",json.dumps(dict(wall=round(time.time()-t0,1),models_loaded=n[0],ok=sum(r[0]=="ok" for r in res),err=sum(r[0]=="err" for r in res),errs=[r[2] for r in res if r[0]=="err"][:3],garbage=[(r[1],r[2][:50]) for r in res if r[0]=="ok" and not CY.search(r[2])][:4]),ensure_ascii=False),flush=True)
# таймаут и отмена in-process
e=threading.Event(); e.set(); t=time.time()
r=c.chat([{"role":"user","content":"Translate to Russian: Diamond"}],timeout=0.001,cancel_event=e)
print("CANCEL",json.dumps({"returned":r[:40],"s":round(time.time()-t,2),"note":"вернулся ответ при cancel=set и timeout=1мс => игнорируются"},ensure_ascii=False),flush=True)
