from bc import *
from concurrent.futures import ThreadPoolExecutor
MODEL="llama3.1:8b-instruct-q4_K_M"
P=pool(); print("pool",len(P),flush=True)
s=Side()
try:
    # O0: продакшн-путь /v1 без num_ctx: какой контекст реально выставляется
    b=P[:25]; t=time.time()
    r=urllib.request.urlopen(urllib.request.Request(URL+"/v1/chat/completions",data=json.dumps({"model":MODEL,"temperature":0,"messages":[{"role":"system","content":BATCH},{"role":"user","content":json.dumps(b,ensure_ascii=False)}]}).encode(),headers={"Content-Type":"application/json"}),timeout=400)
    d=json.loads(r.read()); emit(stage="O0_v1_default",wall=round(time.time()-t,1),usage=d.get("usage"),ps=[{k:m.get(k) for k in("name","size","size_vram","context_length")} for m in ops()])
    # O1: размер батча x num_ctx
    off=0
    for ctx in (2048,4096,8192):
        for B in (1,10,25,50,100):
            n=20 if B==1 else 2
            for i in range(n):
                ch=P[off:off+B]; off=(off+B)%(len(P)-120)
                try:
                    if B==1:
                        d=ochat(MODEL,SINGLE,ch[0],ctx); out=[d["message"]["content"].strip()]; ok=True
                    else:
                        d=ochat(MODEL,BATCH,json.dumps(ch,ensure_ascii=False),ctx); 
                        try: out=_coerce_json_array(d["message"]["content"],len(ch)); ok=True
                        except Exception: out=ch; ok=False
                    emit(stage="O1",model=MODEL,num_ctx=ctx,B=B,i=i,valid=ok,**ostat(d),m=metrics(ch,out))
                except Exception as e: emit(stage="O1",model=MODEL,num_ctx=ctx,B=B,i=i,error=str(e)[:150])
    # O2: JSON schema
    for B in (25,50,100):
        for i in range(2):
            ch=P[off:off+B]; off=(off+B)%(len(P)-120)
            sch={"type":"array","items":{"type":"string"},"minItems":B,"maxItems":B}
            try:
                d=ochat(MODEL,BATCH,json.dumps(ch,ensure_ascii=False),8192,fmt=sch)
                try: out=_coerce_json_array(d["message"]["content"],len(ch)); ok=True
                except Exception: out=ch; ok=False
                emit(stage="O2_schema",model=MODEL,num_ctx=8192,B=B,i=i,valid=ok,**ostat(d),m=metrics(ch,out))
            except Exception as e: emit(stage="O2_schema",model=MODEL,B=B,i=i,error=str(e)[:200])
finally: s.stop()
# O3: OLLAMA_NUM_PARALLEL
for par in (1,2,4):
    s=Side(par)
    try:
        ochat(MODEL,SINGLE,"warm",4096)
        for mode,B,N in (("batch25",25,12),("single",1,48)):
            chunks=[P[i*B:(i+1)*B] for i in range(N)]
            def one(ch):
                t=time.time()
                if B==1:
                    d=ochat(MODEL,SINGLE,ch[0],4096); return time.time()-t,d.get("eval_count",0),True
                d=ochat(MODEL,BATCH,json.dumps(ch,ensure_ascii=False),8192)
                try: _coerce_json_array(d["message"]["content"],len(ch)); ok=True
                except Exception: ok=False
                return time.time()-t,d.get("eval_count",0),ok
            t0=time.time()
            with ThreadPoolExecutor(par) as ex: rs=list(ex.map(one,chunks))
            w=time.time()-t0
            emit(stage="O3_parallel",model=MODEL,par=par,mode=mode,wall=round(w,1),strings=B*N,str_per_s=round(B*N/w,2),out_tok_per_s=round(sum(r[1] for r in rs)/w,1),lat_mean=round(sum(r[0] for r in rs)/len(rs),2),valid=sum(r[2] for r in rs),n=N,ps=[{k:m.get(k) for k in("size","size_vram","context_length")} for m in ops()])
    finally: s.stop()
