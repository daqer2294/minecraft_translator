# Тот же сценарий, что t_mirror.py (падает), но клиент обёрнут блокировкой снаружи. Код проекта не меняется.
import faulthandler;faulthandler.enable()
from bc import *
import tempfile,shutil,threading
from src import mirrorer
from src.translators import Translator
from src.utils.cache import TranslationCache
from src.llm.local_llamacpp import LocalLlamaCppClient
S=os.path.expanduser("~/Documents/curseforge/minecraft/Instances/Create Stranded at Sea/config/ftbquests/quests/chapters")
b=tempfile.mkdtemp(); o=tempfile.mkdtemp(); d=os.path.join(b,"config/ftbquests/quests/chapters"); os.makedirs(d)
fs=sorted(glob.glob(S+"/*.snbt"),key=os.path.getsize)[:12]
for f in fs: shutil.copy(f,d)
c=LocalLlamaCppClient(model_path=GGUF,model="q",n_ctx=4096); L=threading.Lock(); n=[0]
class W:
    name=c.name
    def chat(s,m,**k):
        with L:
            n[0]+=1; return c.chat(m,**k)
log=[]
tr=Translator(W(),TranslationCache(os.path.join(tempfile.mkdtemp(),"c.json")),strict=True,log=log.append,target_lang="ru_ru")
t=time.time(); mirrorer.mirror_translate_dir(b,o,tr,log=log.append,write=True,target_lang="ru_ru")
e=threading.Event(); e.set(); t2=time.time(); r=c.chat([{"role":"user","content":"Translate to Russian: Diamond"}],timeout=0.001,cancel_event=e)
emit(stage="R3_lock",files=len(fs),wall=round(time.time()-t,1),requests=n[0],err_lines=[x for x in log if "[ERR]" in x][:3],cancel_ignored=dict(returned=r[:30],s=round(time.time()-t2,2)))
