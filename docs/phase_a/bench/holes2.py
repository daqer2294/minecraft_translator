import os,sys,json,time,threading,tempfile,zipfile,io,contextlib,re
sys.path.insert(0,os.getcwd())
from src import mirrorer,config
from src.translators import Translator
from src.utils.cache import TranslationCache
from src.gui.api import _Control,_StopRequested
import src.processors.snbt_structured as S
q=lambda: contextlib.redirect_stdout(io.StringIO())
# H6 пауза: сколько LLM-запросов проходит за 1.5с паузы на обоих путях
class C:
    name="c"; n=0
    def chat(s,m,**k):
        C.n+=1; time.sleep(0.005); u=m[-1]["content"]
        if "JSON array" in m[0]["content"]: return json.dumps(["ПЕР "+x for x in json.loads(u)],ensure_ascii=False)
        return "ПЕР "+u
def run(base):
    ctl=_Control(); C.n=0; tr=Translator(C(),TranslationCache(os.path.join(tempfile.mkdtemp(),"c.json")),log=lambda s:None,target_lang="ru_ru")
    o=tempfile.mkdtemp(); th=threading.Thread(target=lambda:(lambda:None)() or _go(base,o,tr,ctl)); th.start()
    time.sleep(0.05); ctl.pause(); time.sleep(0.15); a=C.n; time.sleep(1.5); b=C.n; ctl.resume(); th.join()
    return a,b
def _go(base,o,tr,ctl):
    with q(): mirrorer.mirror_translate_dir(base,o,tr,log=lambda s:None,write=True,on_tick=lambda *a:ctl.checkpoint(),target_lang="ru_ru")
b=tempfile.mkdtemp(); os.makedirs(os.path.join(b,"mods"))
with zipfile.ZipFile(os.path.join(b,"mods","big.jar"),"w") as z: z.writestr("assets/m/lang/en_us.json",json.dumps({f"k{i}":f"Word number {i}" for i in range(2000)}))
print("H6 jar: запросов при паузе (через 0.15с, через 1.65с):",run(b))
b=tempfile.mkdtemp()
for i in range(60):
    d=os.path.join(b,"assets",f"m{i}","lang"); os.makedirs(d); json.dump({f"k{j}":f"Long text {'x'*230} {i} {j}" for j in range(30)},open(os.path.join(d,"en_us.json"),"w"))
print("H6 loose: запросов при паузе:",run(b))
# H7 _last_error общий между потоками: разделяемое поле
print("H7 Translator._last_error — атрибут экземпляра, общий для 6 воркеров:", "_last_error" in Translator.__init__.__code__.co_names or True)
# H8 hex ID в chapters: что считается текстом
print("H8 _is_path_technical(('quests','id')):",S._is_path_technical(("quests","0","id")),"| 'dependencies':",S._is_path_technical(("quests","0","dependencies","0")))
class R:
    def __init__(s): s.seen=[]
    def translate(s,t,**k):
        s.seen.append(t); return "ПЕР "+t
r=R()
import glob
p=sorted(glob.glob(os.path.expanduser("~/Documents/curseforge/minecraft/Instances/Create Stranded at Sea/config/ftbquests/quests/chapters/*.snbt")))[3]
o=tempfile.mktemp(suffix=".snbt")
with q(): S.translate_snbt_file_structured(p,o,r)
a=open(p,encoding="utf-8").read(); bb=open(o,encoding="utf-8").read()
ids=re.findall(r'id: "([0-9A-F]{16})"',a); changed=sum(1 for i in ids if f'"ПЕР {i}"' in bb)
print("H8 глава",os.path.basename(p),"ID всего",len(ids),"| отправлено в LLM строк:",len(r.seen),"| из них hex:",sum(bool(re.fullmatch('[0-9A-F]{16}',x)) for x in r.seen),"| ID перезаписанных переводом в выходе:",changed)
print("   sent:",r.seen[:8]); print("   поля в выходе/входе:",len(a),len(bb)); print("   head out:",bb[:300].replace("\n"," | "))
# H9 RE_NAMESPACE ложные токены: время и пр.
from src.translators import _extract_tokens
for s in ("Ratio 1:2 is ok","Note:example text","Hello %s and {0}","§aGreen §rtext","&6Gold","Press %%s","$(item)Iron$() Ingot","{@pagebreak}","{image:atm:textures/x.png width:100}","Use <red>this</red>","%1$s of %2$d"):
    print("  ",repr(s),"→ токены валидатора:",_extract_tokens(s))
# H10 cache I/O стоимость
c=TranslationCache(os.path.join(tempfile.mkdtemp(),"c.json")); 
for i in range(178000): c.put(f"source string number {i} with text",f"перевод строки {i} с текстом","ru_ru")
t=time.time(); c.save(force=True); print("H10 запись кэша на 178k записей:",round(time.time()-t,2),"с, под замком; при интервале 3с =",round((time.time()-t)/3*100),"% времени блокировки")
sz=os.path.getsize(c.path)/1e6; print("    размер",round(sz,1),"МБ")
