import os,sys,json,time,threading,tempfile,zipfile,io,contextlib
sys.path.insert(0,os.getcwd())
from src import mirrorer,config
from src.translators import Translator
from src.utils.cache import TranslationCache
from src.llm.base import LLMCancelledError,LLMTimeoutError

class Fake:
    name="fake"
    def __init__(s,delay=0.01,fail=None,ev=None): s.delay=delay;s.fail=fail;s.ev=ev;s.n=0
    def chat(s,m,**k):
        s.n+=1; time.sleep(s.delay)
        if s.ev is not None and s.ev.is_set(): raise LLMCancelledError("cancel")
        if s.fail: raise s.fail
        u=m[-1]["content"]
        try:
            a=json.loads(u)
            if isinstance(a,list): return json.dumps(["ПЕР "+x for x in a],ensure_ascii=False)
        except Exception: pass
        return "ПЕР "+u
def tmpjar(base,name,n):
    os.makedirs(base,exist_ok=True)
    p=os.path.join(base,name)
    with zipfile.ZipFile(p,"w") as z: z.writestr("assets/m/lang/en_us.json",json.dumps({f"k{i}":f"Hello number {i}" for i in range(n)}))
    return p
quiet=lambda: contextlib.redirect_stdout(io.StringIO())

# H1: отравление кэша при отмене/таймауте
c=TranslationCache(os.path.join(tempfile.mkdtemp(),"c.json")); ev=threading.Event(); ev.set
f=Fake(ev=threading.Event()); f.ev.set()
class Cl(Fake): pass
tr=Translator(Fake(fail=LLMTimeoutError("t")),c,strict=True,log=lambda s:None,target_lang="ru_ru")
with quiet(): r=tr.translate("Diamond Sword")
print("H1a timeout single: cache['Diamond Sword'] =",repr(c.get("Diamond Sword","ru_ru")),"(совпадает с оригиналом = отравлено)")
c=TranslationCache(os.path.join(tempfile.mkdtemp(),"c.json"))
tr=Translator(Fake(fail=LLMTimeoutError("t")),c,strict=True,log=lambda s:None,target_lang="ru_ru")
with quiet(): r=tr.translate_many(["Iron Ingot","Gold Ingot"])
print("H1b timeout batch : cache =",dict(c.data.get("ru_ru",{})))
c=TranslationCache(os.path.join(tempfile.mkdtemp(),"c.json")); e=threading.Event()
class C2:
    name="x"
    def chat(s,m,**k):
        e.set(); raise LLMCancelledError("in flight")
tr=Translator(C2(),c,strict=True,log=lambda s:None,target_lang="ru_ru",cancel_event=e)
with quiet(): r=tr.translate("Copper Sword")
print("H1c отмена в полёте: cache =",dict(c.data.get("ru_ru",{})))
c=TranslationCache(os.path.join(tempfile.mkdtemp(),"c.json"))
tr=Translator(Fake(fail=RuntimeError("HTTP 500")),c,strict=True,log=lambda s:None,target_lang="ru_ru")
with quiet(): r=tr.translate("Silver Sword")
print("H1d HTTP 500       : cache =",dict(c.data.get("ru_ru",{})))
# второй прогон после фикса сервера: перевод возвращается?
tr2=Translator(Fake(),c,strict=True,log=lambda s:None,target_lang="ru_ru")
print("    повторный прогон со здоровой моделью:",tr2.translate("Silver Sword"),"| запросов к модели:",tr2.client.n)

# H2: стоп в jar-цикле: что попадает в out_root и откуда пустой [ERR][jar]
base=tempfile.mkdtemp(); out=tempfile.mkdtemp(); mods=os.path.join(base,"mods")
tmpjar(mods,"a.jar",200); tmpjar(mods,"b.jar",200); tmpjar(mods,"c.jar",200)
from src.gui.api import _Control,_StopRequested
ctl=_Control(); ev=threading.Event(); logs=[]
c=TranslationCache(os.path.join(tempfile.mkdtemp(),"c.json"))
class C3:
    name="x"
    def chat(s,m,**k):
        time.sleep(0.003); return "ПЕР "+m[-1]["content"]
tr=Translator(C3(),c,strict=True,log=logs.append,target_lang="ru_ru",cancel_event=ev)
def tick(*a): ctl.checkpoint()
threading.Timer(0.3,lambda:(ctl.stop(),ev.set())).start()
stopped=False
try:
    with quiet(): mirrorer.mirror_translate_dir(base,out,tr,log=logs.append,write=True,on_tick=tick,on_total=lambda t:None,cancel_event=ev,target_lang="ru_ru")
except _StopRequested: stopped=True
fs=[os.path.join(r,f) for r,_,ff in os.walk(out) for f in ff]
for p in fs:
    d=json.load(open(p,encoding="utf-8")); tot=len(d); en=sum(1 for v in d.values() if not v.startswith("ПЕР"))
    print(f"H2 jar при Стоп: {os.path.relpath(p,out)} строк {tot}, НЕпереведённых {en}")
print("H2 лог ERR:",[l for l in logs if "[ERR]" in l][:4],"| _StopRequested дошёл:",stopped)

# H3: один файл считается и ok и skip
b=tempfile.mkdtemp(); o=tempfile.mkdtemp(); d=os.path.join(b,"assets","m","lang"); os.makedirs(d); json.dump({"a":"Hi there"},open(os.path.join(d,"en_us.json"),"w"))
os.makedirs(os.path.join(o,"assets","m","lang")); json.dump({},open(os.path.join(o,"assets","m","lang","ru_ru.json"),"w"))
ticks=[]; 
with quiet(): mirrorer.mirror_translate_dir(b,o,Translator(Fake(),TranslationCache(os.path.join(tempfile.mkdtemp(),"c.json")),log=lambda s:None),log=lambda s:None,write=True,on_tick=lambda *a:ticks.append(a),target_lang="ru_ru")
print("H3 tick (done,ok,err,skip) для уже готового файла:",ticks)

# H4: jar с существующим ru_ru — перезаписывается ли официальный перевод
b=tempfile.mkdtemp(); o=tempfile.mkdtemp(); m=os.path.join(b,"mods"); os.makedirs(m)
with zipfile.ZipFile(os.path.join(m,"x.jar"),"w") as z:
    z.writestr("assets/x/lang/en_us.json",json.dumps({"a":"Iron Ingot"})); z.writestr("assets/x/lang/ru_ru.json",json.dumps({"a":"Железный слиток (офиц.)"},ensure_ascii=False))
fk=Fake()
with quiet(): mirrorer.mirror_translate_dir(b,o,Translator(fk,TranslationCache(os.path.join(tempfile.mkdtemp(),"c.json")),log=lambda s:None),log=lambda s:None,write=True,target_lang="ru_ru")
print("H4 существующий ru_ru в jar: запросов к LLM",fk.n,"| результат:",json.load(open(os.path.join(o,"jar_lang","x","assets","x","lang","ru_ru.json"),encoding="utf-8")))
print("   pack.mcmeta в выводе:",os.path.exists(os.path.join(o,"pack.mcmeta")),"| дерево:",[os.path.relpath(os.path.join(r,f),o) for r,_,ff in os.walk(o) for f in ff])

# H5: cwd=/ как в .app из Finder
os.chdir("/")
print("H5 cwd:",os.getcwd(),"| Api output default:",os.path.abspath("./out_mirror"),"| cache:",os.path.abspath(config.DEFAULT_CACHE_PATH))
c=TranslationCache(config.DEFAULT_CACHE_PATH)
tr=Translator(Fake(),c,strict=True,log=lambda s:None,target_lang="ru_ru")
try:
    print("   translate():",tr.translate("Hello there friend"))
except Exception as e: print("   translate() ИСКЛЮЧЕНИЕ:",type(e).__name__,e)
c._dirty=0
try: os.makedirs("out_mirror")
except Exception as e: print("   makedirs out_mirror:",type(e).__name__,e)
