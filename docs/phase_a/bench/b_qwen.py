from bc import *
from llama_cpp import Llama
P=pool()
off=300
def L(ctx): return Llama(model_path=GGUF,n_ctx=ctx,n_gpu_layers=-1,verbose=False)
def q(l,sys_,u,**k):
    t=time.time(); r=l.create_chat_completion(messages=[{"role":"system","content":sys_},{"role":"user","content":u}],temperature=0,**k)
    w=time.time()-t; c=r["choices"][0]
    return c["message"]["content"].strip(),w,r["usage"],c.get("finish_reason")
for ctx in (2048,4096,8192):
    t=time.time(); l=L(ctx); emit(stage="Q1_load",n_ctx=ctx,load_s=round(time.time()-t,1))
    q(l,SINGLE,"warm up")
    for B in (1,10,25,50,100):
        n=20 if B==1 else 2
        for i in range(n):
            ch=P[off:off+B]; off=(off+B)%(len(P)-120)
            try:
                if B==1:
                    o,w,u,f=q(l,SINGLE,ch[0]); out=[o]; ok=True
                else:
                    o,w,u,f=q(l,BATCH,json.dumps(ch,ensure_ascii=False))
                    try: out=_coerce_json_array(o,len(ch)); ok=True
                    except Exception: out=ch; ok=False
                emit(stage="Q1",n_ctx=ctx,B=B,i=i,valid=ok,wall=round(w,2),prompt_tok=u["prompt_tokens"],out_tok=u["completion_tokens"],finish=f,out_tps=round(u["completion_tokens"]/w,1),m=metrics(ch,out))
            except Exception as e: emit(stage="Q1",n_ctx=ctx,B=B,i=i,error=str(e)[:200])
    if ctx==8192:
        # Q2 схема
        for B in (25,50):
            for i in range(2):
                ch=P[off:off+B]; off=(off+B)%(len(P)-120)
                sch={"type":"array","items":{"type":"string"},"minItems":B,"maxItems":B}
                try:
                    o,w,u,f=q(l,BATCH,json.dumps(ch,ensure_ascii=False),response_format={"type":"json_object","schema":sch})
                    try: out=_coerce_json_array(o,len(ch)); ok=True
                    except Exception: out=ch; ok=False
                    emit(stage="Q2_schema",n_ctx=ctx,B=B,i=i,valid=ok,wall=round(w,2),prompt_tok=u["prompt_tokens"],out_tok=u["completion_tokens"],finish=f,m=metrics(ch,out))
                except Exception as e: emit(stage="Q2_schema",B=B,i=i,error=str(e)[:200])
        # Q3 реальный эффект префикса системного промпта (проверка прошлого вывода про 44%)
        S=P[:30]; 
        t=time.time(); [q(l,SINGLE,x) for x in S]; a=time.time()-t
        t=time.time()
        for x in S:
            l.reset(); q(l,SINGLE,x)
        b=time.time()-t
        t=time.time(); [q(l,"",x) for x in S]; c=time.time()-t
        emit(stage="Q3_prefix",n=len(S),with_prefix_reuse_s=round(a,2),full_reset_each_s=round(b,2),empty_system_s=round(c,2))
    del l
