# R2: прототип целевого пайплайна на тех же 9 реальных jar + одинаковые 200 строк: одиночки vs батч с откатом
from bc import *
import threading
from llama_cpp import Llama
R=os.path.expanduser("~/Documents/curseforge/minecraft/Instances/Create Stranded at Sea/mods")
def lang(z):
    r=[];n=set(z.namelist())
    for x in n:
        if re.match(r"assets/[^/]+/lang/en_us\.json$",x):
            try:
                e=json.loads(z.read(x).decode("utf-8-sig")); y=x.replace("en_us","ru_ru"); ru=json.loads(z.read(y).decode("utf-8-sig")) if y in n else {}
                r.append((x,e,ru))
            except Exception: pass
    return r
sel=[];tot=0
for j in sorted(glob.glob(os.path.join(R,"*.jar"))):
    c=sum(len(e) for _,e,_ in lang(zipfile.ZipFile(j)))
    if 8<=c<=90 and tot<320: sel.append(j); tot+=c
todo=[];all_=skip=untr=0
for j in sel:
    for x,e,ru in lang(zipfile.ZipFile(j)):
        for k,v in e.items():
            if not isinstance(v,str): continue
            all_+=1
            if k in ru and ru[k]!=v: skip+=1; continue
            if not re.search("[A-Za-z]",PH.sub("",CODES.sub("",v))): untr+=1; continue
            todo.append(v)
U9=list(dict.fromkeys(todo))
U200=pool()[500:700]
def ok1(s,o): return bool(o) and _extract_tokens(s)==_extract_tokens(o)
def work(label,items,B,single,batch,tag):
    t=time.time();reqs=chunk_fail=retried=roll=0;res={}
    if B==1:
        for x in items:
            reqs+=1
            try: o=single(x)
            except Exception: o=""
            if not ok1(x,o): roll+=1;o=x
            res[x]=o
    else:
        for i in range(0,len(items),B):
            ch=items[i:i+B]; reqs+=1
            try: out=_coerce_json_array(batch(ch),len(ch))
            except Exception:
                chunk_fail+=1; out=[""]*len(ch)
            for x,o in zip(ch,out):
                if not ok1(x,o) or o==x or not CY.search(o):
                    if o: retried+=1
                    reqs+=1
                    try: o=single(x)
                    except Exception: o=""
                    if not ok1(x,o): roll+=1;o=x
                res[x]=o
    w=time.time()-t
    emit(stage="R2",label=label,set=tag,B=B,n=len(items),wall=round(w,1),s_per_str=round(w/len(items),3),requests=reqs,chunk_fail=chunk_fail,retried_items=retried,rollback=roll,m=metrics(list(res.keys()),list(res.values())),all=all_,skip_ru=skip,untr=untr,todo=len(todo),uniq=len(U9))
    json.dump(res,open(os.path.join(SP,f"r2_{label}_{tag}_{B}.json"),"w"),ensure_ascii=False)
l=Llama(model_path=GGUF,n_ctx=4096,n_gpu_layers=-1,verbose=False)
def qs(x):
    return l.create_chat_completion(messages=[{"role":"system","content":SINGLE},{"role":"user","content":x}],temperature=0,max_tokens=cap_single(x))["choices"][0]["message"]["content"].strip()
def qb(ch):
    return l.create_chat_completion(messages=[{"role":"system","content":BATCH},{"role":"user","content":json.dumps(ch,ensure_ascii=False)}],temperature=0,max_tokens=cap_batch(ch))["choices"][0]["message"]["content"]
qs("warm")
for B in (1,10,25):
    work("qwen3b",U9,B,qs,qb,"jars9")
    work("qwen3b",U200,B,qs,qb,"u200")
del l
s=Side()
try:
    M="llama3.1:8b-instruct-q4_K_M"
    os_=lambda x:ochat(M,SINGLE,x,4096,num_predict=cap_single(x),timeout=120)["message"]["content"].strip()
    ob=lambda ch:ochat(M,BATCH,json.dumps(ch,ensure_ascii=False),4096,num_predict=cap_batch(ch),timeout=300)["message"]["content"]
    os_("warm")
    for B in (1,10,25):
        work("llama3.1",U9,B,os_,ob,"jars9")
        work("llama3.1",U200,B,os_,ob,"u200")
finally: s.stop()
