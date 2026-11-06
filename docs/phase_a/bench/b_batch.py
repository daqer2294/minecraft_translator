# R1: годность батчей (перезамер: в первом прогоне _coerce_json_array не импортировался)
from bc import *
from llama_cpp import Llama
P=pool(); MODEL="llama3.1:8b-instruct-q4_K_M"; raw={}
keyed_sys=BATCH+"\nReturn a JSON object whose keys are the 1-based item numbers and whose values are the translations."
def run(backend,ctx,B,i,ch,variant,call):
    payload=json.dumps(ch,ensure_ascii=False) if variant!="keyed" else json.dumps({str(j+1):x for j,x in enumerate(ch)},ensure_ascii=False)
    sysm=keyed_sys if variant=="keyed" else BATCH
    sch=None
    if variant=="schema": sch={"type":"array","items":{"type":"string"},"minItems":B,"maxItems":B}
    if variant=="keyed": sch={"type":"object","properties":{str(j+1):{"type":"string"} for j in range(B)},"required":[str(j+1) for j in range(B)]}
    t=time.time(); o,pt,ot,fin=call(sysm,payload,ctx,sch,cap_batch(ch)); w=time.time()-t
    ok=True;why=None
    try:
        if variant=="keyed":
            j=json.loads(o); out=[j[str(k+1)] for k in range(B)]
        else: out=_coerce_json_array(o,len(ch))
    except Exception:
        ok=False; out=[""]*len(ch); why=fail_reason(o,len(ch))
    raw[f"{backend}|{ctx}|{B}|{i}|{variant}"]={"src":ch,"raw":o}
    json.dump(raw,open(os.path.join(SP,"r1_raw.json"),"w"),ensure_ascii=False)
    emit(stage="R1",backend=backend,variant=variant,ctx=ctx,B=B,i=i,valid=ok,why=why,finish=fin,wall=round(w,2),s_per_str=round(w/B,3),prompt_tok=pt,out_tok=ot,tok_per_str=round(ot/B,1),**({"m":metrics(ch,out)} if ok else {}))
def plan(off):
    # (ctx,B,repeats,variant)
    return [(8192,10,3,"plain"),(8192,25,3,"plain"),(8192,50,3,"plain"),(8192,100,3,"plain"),(2048,100,2,"plain"),(4096,100,2,"plain"),(8192,25,2,"schema"),(8192,50,2,"schema"),(8192,100,1,"schema"),(8192,25,2,"keyed"),(8192,50,2,"keyed")]
s=Side()
try:
    ochat(MODEL,SINGLE,"warm",8192)
    def oc(a,b,ctx,sch,cap):
        d=ochat(MODEL,a,b,ctx,fmt=sch,num_predict=cap,timeout=500); return d["message"]["content"],d.get("prompt_eval_count",0),d.get("eval_count",0),d.get("done_reason")
    off=0
    for ctx,B,n,v in plan(0):
        for i in range(n):
            ch=P[off:off+B]; off=(off+B)%(len(P)-120)
            try: run("llama3.1",ctx,B,i,ch,v,oc)
            except Exception as e: emit(stage="R1",backend="llama3.1",variant=v,ctx=ctx,B=B,i=i,error=str(e)[:160])
finally: s.stop()
cur=None;lm=None
def qc(a,b,ctx,sch,cap):
    global cur,lm
    if cur!=ctx:
        lm=None; lm=Llama(model_path=GGUF,n_ctx=ctx,n_gpu_layers=-1,verbose=False); cur=ctx; lm.create_chat_completion(messages=[{"role":"user","content":"hi"}],max_tokens=4)
    k=dict(response_format={"type":"json_object","schema":sch}) if sch else {}
    r=lm.create_chat_completion(messages=[{"role":"system","content":a},{"role":"user","content":b}],temperature=0,max_tokens=cap,**k)
    c=r["choices"][0]; return c["message"]["content"],r["usage"]["prompt_tokens"],r["usage"]["completion_tokens"],c.get("finish_reason")
off=0
for ctx,B,n,v in plan(0):
    for i in range(n):
        ch=P[off:off+B]; off=(off+B)%(len(P)-120)
        try: run("qwen3b",ctx,B,i,ch,v,qc)
        except Exception as e: emit(stage="R1",backend="qwen3b",variant=v,ctx=ctx,B=B,i=i,error=str(e)[:160])
