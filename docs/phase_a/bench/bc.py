# общие помощники бенчмарка. Код проекта не меняется, всё пишется в scratchpad.
import os,sys,re,json,time,random,subprocess,urllib.request,collections,math,glob,zipfile,socket
SP=os.path.dirname(os.path.abspath(__file__))
PROJECT="/Users/daqer/PycharmProjects/minecraft_translator"
sys.path.insert(0,PROJECT)
os.chdir(SP)  # относительный кэш проекта не должен попасть в репозиторий
from src.translators import _coerce_json_array,_render_prompt,SINGLE_SYSTEM_PROMPT_TEMPLATE,BATCH_SYSTEM_PROMPT_TEMPLATE,_extract_tokens
SINGLE=_render_prompt(SINGLE_SYSTEM_PROMPT_TEMPLATE,"ru_ru"); BATCH=_render_prompt(BATCH_SYSTEM_PROMPT_TEMPLATE,"ru_ru")
OUT=os.environ.get("BENCH_OUT") or os.path.join(SP,"bench2.jsonl")
PORT=11435; URL=f"http://127.0.0.1:{PORT}"
GGUF=os.path.expanduser("~/.minecraft_translator/models/qwen2.5-3b-instruct-q4_k_m/Qwen2.5-3B-Instruct-Q4_K_M.gguf")
CY=re.compile("[А-Яа-яЁё]")
PH=re.compile(r"%(?:\d+\$)?-?\d*(?:\.\d+)?[sdifx]|%%|\{[\w\.\-:@]*\}")
CODES=re.compile(r"§[0-9a-fk-orA-FK-OR]|&[0-9a-fk-orA-FK-OR]")
MACRO=re.compile(r"\$\([^)]*\)")
def emit(**k):
    k["ts"]=time.strftime("%H:%M:%S")
    with open(OUT,"a",encoding="utf-8") as f: f.write(json.dumps(k,ensure_ascii=False)+"\n")
    print(json.dumps({a:b for a,b in k.items() if not isinstance(b,(list,dict)) or len(str(b))<200},ensure_ascii=False)[:420],flush=True)
def pool():
    random.seed(42); r=[]
    for lab,n in (("ATM10",400),("Stranded",240),("Cobblemon",160)):
        a=json.load(open(os.path.join(SP,f"todo_{lab}.json"),encoding="utf-8"))
        a=[x for x in a if len(x)<=220 and "\n" not in x and "§" not in x and '{"text"' not in x]
        random.shuffle(a); r+=a[:n]
    random.shuffle(r); return r
def metrics(srcs,outs):
    m=collections.Counter()
    for s,o in zip(srcs,outs):
        m["n"]+=1
        if o==s: m["identical"]+=1
        if CY.search(o or ""): m["cyr"]+=1
        ps=sorted(PH.findall(s)); po=sorted(PH.findall(o or ""))
        if ps:
            m["ph_n"]+=1; m["ph_ok"]+=int(ps==po)
        cs=sorted(CODES.findall(s)); co=sorted(CODES.findall(o or ""))
        if cs:
            m["code_n"]+=1; m["code_ok"]+=int(cs==co)
        if _extract_tokens(s)!=_extract_tokens(o or ""): m["strict_rollback"]+=1
    return dict(m)
# ---- Ollama side-instance ----
class Side:
    def __init__(s,par=None):
        env=dict(os.environ,OLLAMA_HOST=f"127.0.0.1:{PORT}",OLLAMA_NOPRUNE="1",OLLAMA_KEEP_ALIVE="10m",OLLAMA_MAX_LOADED_MODELS="1")
        if par: env["OLLAMA_NUM_PARALLEL"]=str(par)
        s.p=subprocess.Popen(["/usr/local/bin/ollama","serve"],env=env,stdout=open(os.path.join(SP,"ollama_side.log"),"a"),stderr=subprocess.STDOUT)
        for _ in range(60):
            try:
                urllib.request.urlopen(URL+"/api/version",timeout=1); break
            except Exception: time.sleep(0.5)
    def stop(s):
        s.p.terminate()
        try: s.p.wait(20)
        except Exception: s.p.kill()
        time.sleep(2)
def ochat(model,system,user,num_ctx=None,fmt=None,timeout=400,num_predict=None):
    o={"temperature":0}
    if num_predict: o["num_predict"]=num_predict
    if num_ctx: o["num_ctx"]=num_ctx
    b={"model":model,"stream":False,"options":o,"messages":[{"role":"system","content":system},{"role":"user","content":user}]}
    if fmt is not None: b["format"]=fmt
    t=time.time()
    r=urllib.request.urlopen(urllib.request.Request(URL+"/api/chat",data=json.dumps(b).encode(),headers={"Content-Type":"application/json"}),timeout=timeout)
    d=json.loads(r.read()); d["_wall"]=time.time()-t
    return d
def ops():
    try: return json.loads(urllib.request.urlopen(URL+"/api/ps",timeout=5).read()).get("models",[])
    except Exception: return []
def ostat(d):
    pe=d.get("prompt_eval_count",0); ec=d.get("eval_count",0); pd=d.get("prompt_eval_duration",0)/1e9; ed=d.get("eval_duration",0)/1e9
    return dict(wall=round(d["_wall"],2),load=round(d.get("load_duration",0)/1e9,2),prompt_tok=pe,prompt_s=round(pd,2),out_tok=ec,out_s=round(ed,2),out_tps=round(ec/ed,1) if ed else 0,prompt_tps=round(pe/pd,1) if pd else 0,done=d.get("done_reason"))
def chrf(pairs,beta=2,maxn=6):
    # chrF (Popović 2015), как в sacrebleu: без пробелов, агрегированные n-граммы
    P=[0]*maxn;R=[0]*maxn;M=[0]*maxn
    for h,r in pairs:
        h=re.sub(r"\s+","",h or ""); r=re.sub(r"\s+","",r or "")
        for n in range(1,maxn+1):
            a=collections.Counter(h[i:i+n] for i in range(len(h)-n+1)); b=collections.Counter(r[i:i+n] for i in range(len(r)-n+1))
            P[n-1]+=sum(a.values()); R[n-1]+=sum(b.values()); M[n-1]+=sum((a&b).values())
    ps=[M[i]/P[i] for i in range(maxn) if P[i]]; rs=[M[i]/R[i] for i in range(maxn) if R[i]]
    p=sum(ps)/maxn; r=sum(rs)/maxn
    return round(100*(1+beta**2)*p*r/((beta**2)*p+r),2) if p+r else 0.0

def cap_single(s): return max(96,len(s))
def cap_batch(ch): return sum(max(24,len(x)) for x in ch)+64
def fail_reason(raw,n):
    t=(raw or "").strip()
    try: j=json.loads(t)
    except Exception:
        i=t.find("["); k=t.rfind("]")
        if i<0: return "no_array"
        if k<=i: return "truncated"
        try: j=json.loads(t[i:k+1])
        except Exception: return "bad_json"
    if isinstance(j,list): return f"count {len(j)}!={n}" if len(j)!=n else "items_not_str"
    return "not_array"
__all__=[n for n in globals() if not n.startswith("__")]  # чтобы from bc import * отдавал и _coerce_json_array, _extract_tokens
