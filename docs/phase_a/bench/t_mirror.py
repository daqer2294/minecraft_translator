# настоящий mirror_translate_dir + in-process Qwen на loose-файлах (6 воркеров), как в GUI
import faulthandler;faulthandler.enable()
from bc import *
import tempfile,shutil,glob
from src import mirrorer
from src.translators import Translator
from src.utils.cache import TranslationCache
from src.llm.local_llamacpp import LocalLlamaCppClient
S=os.path.expanduser("~/Documents/curseforge/minecraft/Instances/Create Stranded at Sea/config/ftbquests/quests/chapters")
b=tempfile.mkdtemp(); o=tempfile.mkdtemp(); d=os.path.join(b,"config/ftbquests/quests/chapters"); os.makedirs(d)
fs=sorted(glob.glob(S+"/*.snbt"),key=os.path.getsize)[:12]
for f in fs: shutil.copy(f,d)
print("files",len(fs),"bytes",sum(os.path.getsize(f) for f in fs),flush=True)
c=LocalLlamaCppClient(model_path=GGUF,model="q",n_ctx=4096)
if len(sys.argv)>1 and sys.argv[1]=="warm": c.chat([{"role":"user","content":"hi"}]); print("warmed",flush=True)
log=[]
tr=Translator(c,TranslationCache(os.path.join(tempfile.mkdtemp(),"c.json")),strict=True,log=log.append,target_lang="ru_ru")
t=time.time()
mirrorer.mirror_translate_dir(b,o,tr,log=log.append,write=True,target_lang="ru_ru")
print("DONE",round(time.time()-t,1),"log",log[-5:],flush=True)
