# A2: профиль строк lang (без LLM). Только чтение.
import os,re,json,zipfile,io,sys,collections,glob,time,random
R=os.path.expanduser("~/Documents/curseforge/minecraft/Instances")
PACKS={"Cobblemon":"Cobblemon SMP - The #1 Legendary Pokemon Adventure","ATM10":"All the Mods 10 - ATM10","Stranded":"Create Stranded at Sea"}
EN=re.compile(r"assets/([^/]+)/lang/en_us\.json$"); 
CY=re.compile("[А-Яа-яЁё]"); LAT=re.compile("[A-Za-z]")
PH=re.compile(r"%(?:\d+\$)?-?\d*(?:\.\d+)?[sdifx%]|\{[\w\.\-:@]*\}|§.|&[0-9a-fk-orA-FK-OR]|\$\([^)]*\)|<[^>]+>|\\n|\n")
def load(b):
    try: d=json.loads(b.decode("utf-8-sig"))
    except Exception:
        try:
            d=json.loads(b.decode("utf-8-sig",errors="replace"),strict=False)
        except Exception: return None
    return d if isinstance(d,dict) else None
def classify(v,k=""):
    s=v.strip()
    if not s: return "empty"
    if CY.search(s) and not LAT.search(s): return "already_cyrillic"
    if not LAT.search(s): return "no_letters"
    bare=PH.sub("",s)
    if not LAT.search(bare): return "bare_markup"
    if re.fullmatch(r"(https?://|www\.)\S+",s): return "url"
    if re.fullmatch(r"[a-z0-9_.\-]+:[a-z0-9_/.\-]+",s): return "namespaced_id"
    if re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)+",s): return "translation_key"
    if re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)+",s): return "snake_id"
    if re.fullmatch(r"[\d.,:/\- ]*[a-zA-Z]{0,2}[\d.,:/\- ]*",s) and re.search(r"\d",s): return "numeric_unit"
    if re.fullmatch(r"\s*[A-Za-z]\s*",s): return "single_letter"
    return "text"
res={}
for label,folder in PACKS.items():
    base=os.path.join(R,folder); t0=time.time()
    files=[]  # (source_kind,jar,ns,en_map,ru_map)
    def scan(z,jar,kind):
        names=set(z.namelist())
        for n in names:
            m=EN.match(n)
            if m:
                en=load(z.read(n))
                if en is None: files.append((kind+"_bad",jar,m.group(1),{},{})); continue
                rp=None
                for c in (n.replace("en_us.json","ru_ru.json"),):
                    if c in names: rp=c
                ru=load(z.read(rp)) if rp else {}
                files.append((kind,jar,m.group(1),en,ru or {}))
        for n in names:
            if n.endswith(".jar") and n.startswith("META-INF/jar"):
                try:
                    with zipfile.ZipFile(io.BytesIO(z.read(n))) as zz: scan(zz,jar+"!"+os.path.basename(n),"nested")
                except Exception: pass
    jars=sorted(glob.glob(os.path.join(base,"mods","*.jar")))
    for j in jars:
        try:
            with zipfile.ZipFile(j) as z: scan(z,os.path.basename(j),"jar")
        except Exception as e: print("bad",j,e)
    # ru-переводы в loose kubejs/assets и openloader тоже учитываем как покрытие
    extra_ru=collections.defaultdict(dict)
    for p in glob.glob(os.path.join(base,"kubejs","assets","*","lang","ru_ru.json")):
        d=load(open(p,"rb").read()); ns=p.split(os.sep)[-3]
        if d: extra_ru[ns].update(d)
    loose=[]
    for p in glob.glob(os.path.join(base,"kubejs","assets","*","lang","en_us.json")):
        d=load(open(p,"rb").read()); ns=p.split(os.sep)[-3]
        if d: files.append(("kubejs",p,ns,d,{}))
    # глобальный индекс ru по (ns,key)
    ru_idx=collections.defaultdict(dict)
    for k,j,ns,en,ru in files:
        ru_idx[ns].update({kk:vv for kk,vv in ru.items() if isinstance(vv,str)})
    for ns,d in extra_ru.items(): ru_idx[ns].update(d)
    S=collections.Counter(); kinds=collections.Counter(); uniq_all=set(); uniq_todo=set(); todo_keys=0
    perjar=collections.Counter(); markup=collections.Counter(); lens=[]; todo_samples=[]
    per_kind=collections.Counter(); sameru=0; ru_copy=0; keys_seen=set()
    for k,j,ns,en,ru in files:
        for key,v in en.items():
            if not isinstance(v,str): continue
            S["total"]+=1; per_kind[k]+=1; uniq_all.add(v)
            dup=(ns,key) in keys_seen; keys_seen.add((ns,key))
            if dup: S["dup_ns_key"]+=1
            r=ru_idx[ns].get(key)
            c=classify(v,key)
            if c!="text":
                S["untranslatable:"+c]+=1; S["untranslatable"]+=1; continue
            if r is not None and r.strip() and r!=v:
                S["already_ru"]+=1; continue
            if r is not None and r==v: S["ru_identical_to_en"]+=1
            S["todo"]+=1; uniq_todo.add(v); perjar[j.split("!")[0]]+=1; lens.append(len(v)); 
            for name,rx in (("%s/%d",r"%(?:\d+\$)?[sdf]"),("{x}",r"\{[\w\.\-:@]*\}"),("§",r"§."),("&code",r"&[0-9a-fk-orA-FK-OR]"),("\\n",r"\n|\\n"),("$(macro)",r"\$\("),("<tag>",r"<[a-zA-Z/][^>]*>"),("json",r'^\s*[\[{]\s*"')):
                if re.search(rx,v): markup[name]+=1
    lens.sort()
    uniqtxt=sum(len(x) for x in uniq_todo)
    res[label]=dict(jars=len(jars),lang_files=len(files),total=S["total"],uniq_all=len(uniq_all),
        already_ru=S["already_ru"],untranslatable=S["untranslatable"],todo=S["todo"],uniq_todo=len(uniq_todo),chars_uniq_todo=uniqtxt,
        dup_ns_key=S["dup_ns_key"],ru_identical=S["ru_identical_to_en"],per_kind=dict(per_kind),
        untr={k.split(":")[1]:v for k,v in S.items() if k.startswith("untranslatable:")},markup=dict(markup),
        len_pct={q:lens[int(len(lens)*q)] for q in(.5,.9,.99)} if lens else {},top_jars=perjar.most_common(8),seconds=round(time.time()-t0,1))
    print(label,json.dumps(res[label],ensure_ascii=False))
    json.dump(sorted(uniq_todo),open(os.path.join(os.path.dirname(__file__),f"todo_{label}.json"),"w"),ensure_ascii=False)
json.dump(res,open(os.path.join(os.path.dirname(__file__),"profile_strings.json"),"w"),ensure_ascii=False,indent=1)
