# A3: качество. Эталон 1: кэш ChatGPT. Эталон 2: официальные ru_ru (человеческие) с ключами.
from bc import *
from llama_cpp import Llama
d=json.load(open(os.path.join(PROJECT,"src/translations_cache.json"),encoding="utf-8"))
pairs=[(k,v) for k,v in d.items() if isinstance(v,str) and CY.search(v) and k!=v and re.search("[A-Za-z]",k)]
random.seed(7); random.shuffle(pairs)
S=[p for p in pairs if len(p[0])<30][:120]; M=[p for p in pairs if 30<=len(p[0])<120][:60]; Lg=[p for p in pairs if len(p[0])>=120][:20]
A=S+M+Lg; random.shuffle(A); json.dump(A,open(os.path.join(SP,"a3_sample.json"),"w"),ensure_ascii=False,indent=0)
srcs=[a[0] for a in A]; refs=[a[1] for a in A]
print("sample",len(A),"ph",sum(bool(PH.search(s)) for s in srcs),"codes",sum(bool(CODES.search(s)) for s in srcs),flush=True)
MODELS=["llama3.1:8b-instruct-q4_K_M","llama3:latest","owl/t-lite:latest","QWEN"]
results={}
def strict(s,o):
    if not o or _extract_tokens(s)!=_extract_tokens(o): return s,True
    return o,False
def score(label,mode,outs,wall,tok):
    final=[];rb=0
    for s,o in zip(srcs,outs):
        f,r=strict(s,o); final.append(f); rb+=r
    m=metrics(srcs,outs)
    emit(stage="A3",model=label,mode=mode,wall=round(wall,1),str_per_s=round(len(srcs)/wall,2),out_tok=tok,out_tps=round(tok/wall,1),rollback=rb,rollback_share=round(rb/len(srcs),3),identical=m.get("identical",0),no_cyr=len(srcs)-m.get("cyr",0),ph=f'{m.get("ph_ok",0)}/{m.get("ph_n",0)}',codes=f'{m.get("code_ok",0)}/{m.get("code_n",0)}',chrF_raw=chrf(zip(outs,refs)),chrF_final=chrf(zip(final,refs)),chrF_short=chrf([(o,r) for o,r,s in zip(outs,refs,srcs) if len(s)<30]),chrF_mid=chrf([(o,r) for o,r,s in zip(outs,refs,srcs) if 30<=len(s)<120]),chrF_long=chrf([(o,r) for o,r,s in zip(outs,refs,srcs) if len(s)>=120]))
    results.setdefault(label,{})[mode]=outs
    json.dump(results,open(os.path.join(SP,"a3_outputs.json"),"w"),ensure_ascii=False)
def batch_run(call,items=None):
    items=items or srcs
    out=[];tok=0;fails=0;t=time.time()
    for i in range(0,len(items),25):
        ch=items[i:i+25]
        try:
            c,tk=call(BATCH,json.dumps(ch,ensure_ascii=False),cap_batch(ch)); tok+=tk
            out+=_coerce_json_array(c,len(ch))
        except Exception: out+=[""]*len(ch); fails+=1
    return out,time.time()-t,tok,fails
def single_run(call,items=None):
    items=items or srcs
    out=[];tok=0;errs=0;t=time.time()
    for x in items:
        try:
            o,k=call(SINGLE,x,cap_single(x)); out.append(o); tok+=k
        except Exception: out.append(""); errs+=1
    return out,time.time()-t,tok,errs
# A3c: плейсхолдеры и коды (в выборке ChatGPT их почти нет). Эталона нет, только сохранность разметки.
_T=[]
for lab in ("ATM10","Stranded","Cobblemon"): _T+=json.load(open(os.path.join(SP,f"todo_{lab}.json"),encoding="utf-8"))
_T=sorted({x for x in _T if len(x)<=200}); random.seed(13); random.shuffle(_T)
_pc=[x for x in _T if re.search(r"%(\d+\$)?[sd]",x) and "%%" not in x][:40]
_br=[x for x in _T if re.search(r"\{\w*\}",x)][:15]
_sc=[x for x in _T if "§" in x][:30]
_am=[x for x in _T if CODES.search(x) and "&" in x and "§" not in x][:15]
_nl=[x for x in _T if "\n" in x][:15]
C=list(dict.fromkeys(_pc+_br+_sc+_am+_nl))
json.dump(C,open(os.path.join(SP,"a3c_sample.json"),"w"),ensure_ascii=False,indent=0)
print("A3c",len(C),len(_pc),len(_br),len(_sc),len(_am),len(_nl),flush=True)
def score_c(label,mode,outs,wall,extra=0):
    m=metrics(C,outs); nl=[(s,o) for s,o in zip(C,outs) if "\n" in s]
    emit(stage="A3c",model=label,mode=mode,n=len(C),wall=round(wall,1),ph=f'{m.get("ph_ok",0)}/{m.get("ph_n",0)}',codes=f'{m.get("code_ok",0)}/{m.get("code_n",0)}',nl=f'{sum(o.count(chr(10))==s.count(chr(10)) for s,o in nl)}/{len(nl)}',strict_rollback=m.get("strict_rollback",0),no_cyr=len(C)-m.get("cyr",0),fails=extra)
    results.setdefault("A3c:"+label,{})[mode]=outs; json.dump(results,open(os.path.join(SP,"a3_outputs.json"),"w"),ensure_ascii=False)
s=Side()
try:
    for mdl in MODELS[:3]:
        try:
            t=time.time(); d0=ochat(mdl,SINGLE,"warm",4096); emit(stage="A3_load",model=mdl,load_s=round(time.time()-t,1))
            def sc(a,b,cap):
                d=ochat(mdl,a,b,4096,num_predict=cap,timeout=120); return d["message"]["content"].strip(),d.get("eval_count",0)
            def call(a,b,cap):
                d=ochat(mdl,a,b,8192,num_predict=cap,timeout=300); return d["message"]["content"],d.get("eval_count",0)
            out,w,tok,er=single_run(sc); emit(stage="A3_singlefail",model=mdl,errors=er); score(mdl,"single",out,w,tok)
            o,w,tk,f=batch_run(call); emit(stage="A3_batchfail",model=mdl,failed_chunks=f,chunks=8); score(mdl,"batch25",o,w,tk)
            o,w,tk,er=single_run(sc,C); score_c(mdl,"single",o,w,er)
            o,w,tk,f=batch_run(call,C); score_c(mdl,"batch25",o,w,f)
        except Exception as e: emit(stage="A3",model=mdl,error=str(e)[:200])
finally: s.stop()
l=Llama(model_path=GGUF,n_ctx=8192,n_gpu_layers=-1,verbose=False)
def qq(a,b,cap=512):
    r=l.create_chat_completion(messages=[{"role":"system","content":a},{"role":"user","content":b}],temperature=0,max_tokens=cap); return r["choices"][0]["message"]["content"].strip(),r["usage"]["completion_tokens"]
qq(SINGLE,"warm")
out,w,tok,er=single_run(qq); emit(stage="A3_singlefail",model="Qwen2.5-3B",errors=er); score("Qwen2.5-3B","single",out,w,tok)
o,w,tk,f=batch_run(qq); emit(stage="A3_batchfail",model="Qwen2.5-3B",failed_chunks=f,chunks=8); score("Qwen2.5-3B","batch25",o,w,tk)
o,w,tk,er=single_run(qq,C); score_c("Qwen2.5-3B","single",o,w,er)
o,w,tk,f=batch_run(qq,C); score_c("Qwen2.5-3B","batch25",o,w,f)
# лучшая по chrF_raw single
rows=[json.loads(x) for x in open(OUT,encoding="utf-8") if '"stage": "A3"' in x]
best=max([r for r in rows if r.get("mode")=="single" and "chrF_raw" in r],key=lambda r:r["chrF_raw"])["model"]; emit(stage="A3_best_single",model=best)
# A3b: официальные ru_ru + варианты промпта
tm=json.load(open(os.path.join(SP,"tm_all.json"),encoding="utf-8"))
H=[]
for j in sorted(glob.glob(os.path.expanduser("~/Documents/curseforge/minecraft/Instances/Create Stranded at Sea/mods/*.jar"))+glob.glob(os.path.expanduser("~/Documents/curseforge/minecraft/Instances/All the Mods 10 - ATM10/mods/*.jar"))):
    try: z=zipfile.ZipFile(j)
    except Exception: continue
    n=set(z.namelist())
    for x in n:
        if re.match(r"assets/[^/]+/lang/ru_ru\.json$",x) and x.replace("ru_ru","en_us") in n:
            try: e=json.loads(z.read(x.replace("ru_ru","en_us")).decode("utf-8-sig")); r=json.loads(z.read(x).decode("utf-8-sig"))
            except Exception: continue
            for k,v in e.items():
                rv=r.get(k)
                if isinstance(v,str) and isinstance(rv,str) and rv!=v and CY.search(rv) and re.search("[A-Za-z]{3}",v) and len(v)<=220 and "\n" not in v: H.append((k,v,rv))
random.seed(11); random.shuffle(H)
seen=set();HH=[]
for k,v,r in H:
    if v not in seen:
        seen.add(v);HH.append((k,v,r))
    if len(HH)>=150: break
json.dump(HH,open(os.path.join(SP,"a3b_sample.json"),"w"),ensure_ascii=False)
terms=sorted([(k,v) for k,v in tm.items() if 1<=len(k.split())<=3 and re.fullmatch(r"[A-Za-z' \-]+",k) and len(k)>=4 and len(v.split())<=len(k.split())+1 and not re.match(r"(?i)(в|на|из|с|от|для|по|к) ",v)],key=lambda x:-len(x[0]))
def gloss(t):
    r=[]
    low=t.lower()
    for a,b in terms:
        if a.lower()!=low and re.search(r"\b"+re.escape(a.lower())+r"\b",low):
            r.append((a,b))
            if len(r)>=6: break
    return r
def variants(k,v):
    g=gloss(v); gt="\n".join(f"- {a} = {b}" for a,b in g)
    return {"base":v,"key":f"Translation key (context only, do NOT translate): {k}\nText to translate:\n{v}","gloss":(f"Use these Russian terms:\n{gt}\nText to translate:\n{v}" if g else v),"key+gloss":(f"Translation key (context only, do NOT translate): {k}\n"+(f"Use these Russian terms:\n{gt}\n" if g else "")+f"Text to translate:\n{v}")},g
def run_vars(label,call):
    for name in ("base","key","gloss","key+gloss"):
        out=[];t=time.time();hit=[0,0]
        for k,v,r in HH:
            vv,g=variants(k,v)
            try: o=call(SINGLE,vv[name])
            except Exception: o=""
            out.append(o)
            for a,b in g:
                hit[1]+=1; hit[0]+=int(b[:max(4,len(b)-2)].lower() in o.lower())
        emit(stage="A3b",model=label,variant=name,wall=round(time.time()-t,1),chrF=chrf(zip(out,[h[2] for h in HH])),no_cyr=sum(not CY.search(o) for o in out),gloss_used=f"{hit[0]}/{hit[1]}",m=metrics([h[1] for h in HH],out))
        results.setdefault("A3b:"+label,{})[name]=out; json.dump(results,open(os.path.join(SP,"a3_outputs.json"),"w"),ensure_ascii=False)
run_vars("Qwen2.5-3B",lambda a,b:qq(a,b,300)[0])
del l
if best!="Qwen2.5-3B":
    s=Side()
    try:
        ochat(best,SINGLE,"warm",4096)
        run_vars(best,lambda a,b:ochat(best,a,b,4096,num_predict=300,timeout=120)["message"]["content"].strip())
    finally: s.stop()
