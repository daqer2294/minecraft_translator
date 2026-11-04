# P: реальный путь mirrorer на подмножестве реальных jar + прототип целевого пайплайна на тех же данных
from bc import *
import tempfile,shutil,io,contextlib,threading
from src import mirrorer
from src.translators import Translator
from src.utils.cache import TranslationCache
from src.llm.local_llamacpp import LocalLlamaCppClient
from src.llm.openai_compatible import OpenAICompatibleClient
R=os.path.expanduser("~/Documents/curseforge/minecraft/Instances/Create Stranded at Sea/mods")
def lang(z):
    r=[]
    n=set(z.namelist())
    for x in n:
        if re.match(r"assets/[^/]+/lang/en_us\.json$",x):
            try:
                e=json.loads(z.read(x).decode("utf-8-sig")); y=x.replace("en_us","ru_ru"); ru=json.loads(z.read(y).decode("utf-8-sig")) if y in n else {}
                r.append((x,e,ru))
            except Exception: pass
    return r
# выбираем реальные jar без ru_ru и с ru_ru до ~320 строк суммарно
sel=[];tot=0
for j in sorted(glob.glob(os.path.join(R,"*.jar"))):
    z=zipfile.ZipFile(j); c=sum(len(e) for _,e,_ in lang(z))
    if 8<=c<=90 and tot<320: sel.append(j); tot+=c
print("jars",len(sel),"strings",tot,flush=True)
class Inst:
    def __init__(s,c): s.c=c; s.n=0; s.t=0.0; s.err=collections.Counter(); s.name=c.name
    def chat(s,m,**k):
        s.n+=1; t=time.time()
        try: return s.c.chat(m,**k)
        except Exception as e:
            s.err[type(e).__name__]+=1; raise
        finally: s.t+=time.time()-t
def run_current(label,client):
    b=tempfile.mkdtemp(); os.makedirs(os.path.join(b,"mods")); o=tempfile.mkdtemp()
    for j in sel: os.symlink(j,os.path.join(b,"mods",os.path.basename(j)))
    I=Inst(client); tr=Translator(I,TranslationCache(os.path.join(tempfile.mkdtemp(),"c.json")),strict=True,log=lambda s:None,target_lang="ru_ru")
    t=time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        mirrorer.mirror_translate_dir(b,o,tr,log=lambda s:None,write=True,target_lang="ru_ru")
    w=time.time()-t; st=collections.Counter()
    for r,_,fs in os.walk(o):
        for f in fs:
            if f=="ru_ru.json":
                for v in json.load(open(os.path.join(r,f),encoding="utf-8")).values():
                    if isinstance(v,str):
                        st["out"]+=1; st["cyr"]+=int(bool(CY.search(v)))
    emit(stage="P_current",label=label,wall=round(w,1),requests=I.n,llm_s=round(I.t,1),overhead_s=round(w-I.t,1),errors=dict(I.err),out=st["out"],cyr=st["cyr"],no_cyr_share=round(1-st["cyr"]/max(1,st["out"]),3),str_per_s=round(st["out"]/w,2))
    shutil.rmtree(b);shutil.rmtree(o)
def run_target(label,client,B=25):
    # прототип: skip готового ru, отсев непереводимого, глобальный дедуп, батчи по B, откат на одиночные
    todo=[];all_=0;skip=0;untr=0
    for j in sel:
        for x,e,ru in lang(zipfile.ZipFile(j)):
            for k,v in e.items():
                if not isinstance(v,str): continue
                all_+=1
                if k in ru and ru[k]!=v: skip+=1; continue
                if not re.search("[A-Za-z]",PH.sub("",CODES.sub("",v))): untr+=1; continue
                todo.append(v)
    uniq=list(dict.fromkeys(todo)); t=time.time(); reqs=0;fail=0;res={}
    for i in range(0,len(uniq),B):
        ch=uniq[i:i+B]; reqs+=1
        try:
            out=_coerce_json_array(client.chat([{"role":"system","content":BATCH},{"role":"user","content":json.dumps(ch,ensure_ascii=False)}],temperature=0,timeout=400),len(ch))
        except Exception:
            fail+=1; out=[]
            for x in ch:
                reqs+=1; out.append(client.chat([{"role":"system","content":SINGLE},{"role":"user","content":x}],temperature=0,timeout=200))
        res.update(zip(ch,out))
    w=time.time()-t; cy=sum(bool(CY.search(v)) for v in res.values())
    emit(stage="P_target",label=label,B=B,all=all_,skip_ru=skip,untr=untr,todo=len(todo),uniq=len(uniq),wall=round(w,1),requests=reqs,batch_fail=fail,cyr=cy,no_cyr_share=round(1-cy/max(1,len(res)),3),m=metrics(list(res.keys()),list(res.values())),strings_per_s_all=round(all_/w,2))
q=LocalLlamaCppClient(model_path=GGUF,model="q",n_ctx=4096)
q.chat([{"role":"user","content":"hi"}])
run_current("qwen3b_inproc",q); run_target("qwen3b_inproc",q)
del q
s=Side()
try:
    c=OpenAICompatibleClient(base_url=URL,api_key="",model="llama3.1:8b-instruct-q4_K_M")
    c.chat([{"role":"user","content":"hi"}],timeout=200)
    run_current("llama3.1_8b_ollama",c); run_target("llama3.1_8b_ollama",c)
finally: s.stop()
