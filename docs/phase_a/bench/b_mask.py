# A3d: маскирование разметки перед моделью и восстановление после. Та же выборка A3c, одиночные запросы.
from bc import *
from llama_cpp import Llama
C=json.load(open("a3c_sample.json",encoding="utf-8"))
MASK=re.compile(r"%(?:\d+\$)?-?\d*(?:\.\d+)?[sdifx]|%%|\{[\w\.\-:@]*\}|§[0-9a-fk-orA-FK-OR]|&[0-9a-fk-orA-FK-OR]|\n|<[^<>\n]{1,40}>")
EXTRA=SINGLE+"\nTokens like [[0]], [[1]] are markup: copy each of them exactly once, unchanged, in a natural position."
def mask(s):
    t=[]
    def f(m):
        t.append(m.group(0)); return f"[[{len(t)-1}]]"
    return MASK.sub(f,s),t
def unmask(o,t):
    found=re.findall(r"\[\[(\d+)\]\]",o)
    if sorted(map(int,found))!=list(range(len(t))): return None
    return re.sub(r"\[\[(\d+)\]\]",lambda m:t[int(m.group(1))],o)
def run(label,call):
    t0=time.time();out=[];lost=0
    for s in C:
        m,t=mask(s)
        if not re.search("[A-Za-z]",m): out.append(s); continue
        try: o=call(EXTRA,m,cap_single(m)+len(t)*6)
        except Exception: o=""
        r=unmask(o,t)
        if r is None: lost+=1; out.append(s)
        else: out.append(r)
    w=time.time()-t0; me=metrics(C,out); nl=[(s,o) for s,o in zip(C,out) if "\n" in s]
    emit(stage="A3d",model=label,n=len(C),wall=round(w,1),rollback_mask_lost=lost,ph=f'{me.get("ph_ok",0)}/{me.get("ph_n",0)}',codes=f'{me.get("code_ok",0)}/{me.get("code_n",0)}',nl=f'{sum(o.count(chr(10))==s.count(chr(10)) for s,o in nl)}/{len(nl)}',strict_rollback=me.get("strict_rollback",0),no_cyr=len(C)-me.get("cyr",0))
    json.dump(out,open(f"a3d_{label.replace('/','_').replace(':','_')}.json","w"),ensure_ascii=False)
l=Llama(model_path=GGUF,n_ctx=4096,n_gpu_layers=-1,verbose=False)
def q(a,b,cap):
    return l.create_chat_completion(messages=[{"role":"system","content":a},{"role":"user","content":b}],temperature=0,max_tokens=cap)["choices"][0]["message"]["content"].strip()
q(SINGLE,"warm",8); run("Qwen2.5-3B",q); del l
s=Side()
try:
    for M in ("llama3.1:8b-instruct-q4_K_M","owl/t-lite:latest"):
        ochat(M,SINGLE,"warm",4096,num_predict=8)
        run(M,lambda a,b,cap:ochat(M,a,b,4096,num_predict=cap,timeout=120)["message"]["content"].strip())
finally: s.stop()
