# Translation memory: сколько "todo" закрывается точным совпадением с официальными ru_ru других модов / ванилью
import os,re,json,zipfile,io,glob,collections,sys
R=os.path.expanduser("~/Documents/curseforge/minecraft/Instances")
SP=os.path.dirname(os.path.abspath(__file__))
INST=os.path.expanduser("~/Documents/curseforge/minecraft/Install")
print("Install dir:",os.path.isdir(INST), os.listdir(INST)[:8] if os.path.isdir(INST) else "")
van_en={};van_ru={}
vj=glob.glob(os.path.join(INST,"versions","1.21.1","1.21.1.jar"))
print("client jar:",vj)
if vj:
    z=zipfile.ZipFile(vj[0]); van_en=json.loads(z.read("assets/minecraft/lang/en_us.json")); print("vanilla en",len(van_en))
idx=glob.glob(os.path.join(INST,"assets","indexes","*.json")); print("indexes",[os.path.basename(x) for x in idx][-4:])
for i in sorted(idx,key=os.path.getmtime,reverse=True):
    d=json.load(open(i)); o=d.get("objects",{}).get("minecraft/lang/ru_ru.json")
    if o:
        h=o["hash"]; p=os.path.join(INST,"assets","objects",h[:2],h)
        if os.path.exists(p): van_ru=json.load(open(p,encoding="utf-8")); print("vanilla ru from",os.path.basename(i),len(van_ru)); break
vanilla_pairs={van_en[k]:van_ru[k] for k in van_en if k in van_ru and isinstance(van_en[k],str)}
print("vanilla pairs",len(vanilla_pairs))
def load(b):
    try:
        d=json.loads(b.decode("utf-8-sig")); return d if isinstance(d,dict) else None
    except Exception: return None
P={"Cobblemon":"Cobblemon SMP - The #1 Legendary Pokemon Adventure","ATM10":"All the Mods 10 - ATM10","Stranded":"Create Stranded at Sea"}
global_tm=collections.defaultdict(collections.Counter)
for label,f in P.items():
    b=os.path.join(R,f); tm=collections.defaultdict(collections.Counter)
    for j in glob.glob(os.path.join(b,"mods","*.jar")):
        try: z=zipfile.ZipFile(j)
        except Exception: continue
        names=set(z.namelist())
        for n in names:
            if re.match(r"assets/[^/]+/lang/ru_ru\.json$",n):
                e=n.replace("ru_ru","en_us")
                if e in names:
                    ed=load(z.read(e)); rd=load(z.read(n))
                    if ed and rd:
                        for k,v in ed.items():
                            r=rd.get(k)
                            if isinstance(v,str) and isinstance(r,str) and r!=v and re.search("[А-Яа-яЁё]",r): tm[v][r]+=1; global_tm[v][r]+=1
    todo=json.load(open(os.path.join(SP,f"todo_{label}.json"),encoding="utf-8"))
    hit=sum(1 for t in todo if t in tm); v=sum(1 for t in todo if t in vanilla_pairs); both=sum(1 for t in todo if t in tm or t in vanilla_pairs)
    amb=sum(1 for t,c in tm.items() if len(c)>1); 
    # насколько едины термины в официальных ru: для коротких строк
    print(label,"todo uniq",len(todo),"| TM hit",hit,"| vanilla",v,"| TM|vanilla",both,f"({100*both/max(1,len(todo)):.1f}%)","| en строк в TM",len(tm),"| неоднозначных en→>1 ru",amb)
    # вокабуляр: сколько todo полностью собираются из коротких TM-терминов (2-3 слова), прикидка шаблонов
    words=collections.Counter(w for t in todo for w in re.findall(r"[A-Za-z']+",t)); print("   уникальных слов в todo:",len(words),"топ:",words.most_common(14))
    tok=sum(len(t) for t in todo)
    print("   символов todo:",tok)
json.dump({k:c.most_common(1)[0][0] for k,c in global_tm.items() if len(k)<80},open(os.path.join(SP,"tm_all.json"),"w"),ensure_ascii=False)
for term in ("Ingot","Iron Ingot","Dust","Nugget","Plate","Gear","Rod","Block of Steel","Copper Ingot","Sword"):
    c=global_tm.get(term); print(term,c.most_common(4) if c else None)
# разнобой: Steel Ingot
amb=[(k,c.most_common(3)) for k,c in global_tm.items() if len(c)>=3 and len(k)<30]
print("примеры неоднозначности:",amb[:8])
