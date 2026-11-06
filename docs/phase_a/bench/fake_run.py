# A2: НАСТОЯЩИЙ код mirrorer+Translator на всей сборке, LLM заменена счётчиком (ноль задержки).
import os,sys,json,time,tempfile,shutil,threading,collections,io,contextlib,re
sys.path.insert(0,os.getcwd())
from src import mirrorer,config
from src.translators import Translator
from src.utils.cache import TranslationCache
R=os.path.expanduser("~/Documents/curseforge/minecraft/Instances")
name,folder=sys.argv[1],sys.argv[2]
base=os.path.join(R,folder)
class C:
    name="counter"
    def __init__(s): s.l=threading.Lock(); s.single=0; s.batch=0; s.batch_items=0; s.chars=0; s.th=set(); s.conc=0; s.peak=0
    def chat(s,m,**k):
        with s.l:
            s.conc+=1; s.peak=max(s.peak,s.conc); s.th.add(threading.get_ident()); s.chars+=sum(len(x["content"]) for x in m)
        u=m[-1]["content"]; a=None
        if "JSON array" in m[0]["content"]:
            a=json.loads(u)
        with s.l:
            s.conc-=1
            if a is not None: s.batch+=1; s.batch_items+=len(a)
            else: s.single+=1
        if a is not None: return json.dumps(["ПЕР "+x for x in a],ensure_ascii=False)
        return "ПЕР "+u
out=tempfile.mkdtemp(prefix="mct_fake_"); cdir=tempfile.mkdtemp()
cl=C(); logs=[]; 
cache=TranslationCache(os.path.join(cdir,"c.json"))
tr=Translator(cl,cache,strict=True,log=logs.append,target_lang="ru_ru")
ticks=[];tot=[]
t0=time.time()
with contextlib.redirect_stdout(io.StringIO()):
    mirrorer.mirror_translate_dir(base,out,tr,log=logs.append,write=True,on_total=tot.append,on_tick=lambda *a:ticks.append((time.time(),)+a),target_lang="ru_ru")
wall=time.time()-t0
kinds=collections.Counter(re.match(r"\[[^\]]+\]\[[^\]]+\]",l).group(0) for l in logs if re.match(r"\[[^\]]+\]\[[^\]]+\]",l))
errs=[l for l in logs if "[ERR]" in l or "[WARN]" in l or "[LWRN]" in l]
outfiles=collections.Counter()
for r,_,fs in os.walk(out):
    for f in fs:
        rel=os.path.relpath(os.path.join(r,f),out).replace("\\","/")
        outfiles[rel.split("/")[0]+"/*."+f.rsplit(".",1)[-1]]+=1
# проверка: en_us.snbt остался en_us?
snbt=[os.path.relpath(os.path.join(r,f),out) for r,_,fs in os.walk(os.path.join(out,"config","ftbquests","quests","lang")) for f in fs] if os.path.isdir(os.path.join(out,"config","ftbquests")) else []
loc=collections.Counter(x.split(os.sep)[4] if len(x.split(os.sep))>4 else "?" for x in snbt)
print(json.dumps(dict(pack=name,total_files=tot[0] if tot else None,wall_s=round(wall,1),single_requests=cl.single,batch_requests=cl.batch,batch_items=cl.batch_items,avg_batch=round(cl.batch_items/max(1,cl.batch),1),prompt_chars=cl.chars,peak_conc=cl.peak,threads=len(cl.th),ticks=len(ticks),cache_entries=len(cache),log_kinds=kinds.most_common(14),errors=len(errs),err_samples=[e[:170] for e in errs[:8]],outfiles=outfiles.most_common(10),ftb_lang_out_sample=sorted(snbt)[:3],ftb_lang_out_locales=loc.most_common(5),out_mb=round(sum(os.path.getsize(os.path.join(r,f)) for r,_,fs in os.walk(out) for f in fs)/1e6,1)),ensure_ascii=False,indent=0))
shutil.rmtree(out,ignore_errors=True); shutil.rmtree(cdir,ignore_errors=True)
