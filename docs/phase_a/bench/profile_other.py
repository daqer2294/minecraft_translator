import os,re,json,zipfile,glob,io,sys,collections
sys.path.insert(0,os.getcwd())
R=os.path.expanduser("~/Documents/curseforge/minecraft/Instances")
P={"ATM10":"All the Mods 10 - ATM10","Stranded":"Create Stranded at Sea","Cobblemon":"Cobblemon SMP - The #1 Legendary Pokemon Adventure"}
STR=re.compile(r'"((?:[^"\\]|\\.)*)"')
LINE=re.compile(r'^\s*([\w.\-]+):\s*(.*)$')
def ftb_map(path):
    # плоский SNBT lang-файл: key: "text" | key: [ "a" "b" ]
    t=open(path,encoding="utf-8",errors="replace").read(); out={}
    for m in re.finditer(r'^\s*([\w.\-]+):\s*(\[|")',t,re.M):
        k=m.group(1); i=m.end()-1
        if t[i]=='"':
            mm=STR.match(t,i); out[k]=[mm.group(1)] if mm else []
        else:
            j=t.find("\n\t]",i)
            seg=t[i:(j if j>0 else i+100000)]
            e=t.find("]",i) if "\n" not in t[i:t.find("]",i)] else j
            seg=t[i:e if e>0 else None]
            out[k]=[x.group(1) for x in STR.finditer(seg)]
    return out
for label,f in P.items():
    b=os.path.join(R,f); print("=====",label)
    q=os.path.join(b,"config/ftbquests/quests/lang")
    if os.path.isdir(q):
        def coll(l):
            d={}
            single=os.path.join(q,l+".snbt")
            if os.path.exists(single): d.update(ftb_map(single))
            for p in glob.glob(os.path.join(q,l,"**","*.snbt"),recursive=True): d.update(ftb_map(p))
            return d
        en=coll("en_us"); ru=coll("ru_ru")
        pieces=sum(len(v) for v in en.values()); cover=sum(1 for k in en if k in ru and ru[k]!=en[k])
        allstr=[s for v in en.values() for s in v]
        print(" FTB en keys",len(en),"strings",pieces,"chars",sum(map(len,allstr)),"uniq",len(set(allstr)),"| ru keys",len(ru),"covered",cover,"| &code",sum('&' in s for s in allstr),"pagebreak",sum('{@pagebreak}' in s for s in allstr),"\\n",sum('\\n' in s for s in allstr),"key-refs {..}",sum(bool(re.search(r'\{[a-z_]+\.[a-z_.]+\}',s)) for s in allstr),"json",sum(s.lstrip().startswith(('{','[')) for s in allstr))
        ks=collections.Counter(k.split(".")[0]+"."+k.split(".")[-1] for k in en); print("  key kinds",ks.most_common(8))
        print("  sample",[x for x in allstr if '{' in x or '\\n' in x][:3])
    # Patchouli + guideme + modonomicon across jars
    pa=collections.Counter(); gm=collections.Counter(); gmchars=0;pchars=0; mono=collections.Counter(); gmdirs=collections.Counter(); pa_sample=None; i18n=collections.Counter(); books=[]; mono_s=None
    for j in glob.glob(os.path.join(b,"mods","*.jar")):
        try: z=zipfile.ZipFile(j)
        except Exception: continue
        names=set(z.namelist())
        for n in names:
            m=re.match(r"data/[^/]+/patchouli_books/([^/]+)/book\.json$",n)
            if m:
                try:
                    d=json.loads(z.read(n)); i18n["i18n=true" if d.get("i18n") else "i18n=false"]+=1; i18n["use_resource_pack" if d.get("use_resource_pack") else "no_urp"]+=1; books.append(m.group(1))
                except Exception: pass
            m=re.match(r"(assets|data)/([^/]+)/patchouli_books/([^/]+)/en_us/(.+\.json)$",n)
            if m:
                pa["en_files"]+=1
                r=n.replace("/en_us/","/ru_ru/")
                if r in names: pa["ru_exists"]+=1
                else:
                    try:
                        s=z.read(n).decode("utf-8","replace"); pchars+=len(s); 
                        d=json.loads(s)
                        def w(o):
                            global pchars2
                            if isinstance(o,str): return [o]
                            if isinstance(o,list): return sum((w(x) for x in o),[])
                            if isinstance(o,dict):
                                r=[]
                                for k,v in o.items():
                                    if k in("text","title","name","description","subtitle","landing_text"): r+=w(v)
                                    elif isinstance(v,(list,dict)): r+=w(v)
                                return r
                            return []
                        st=w(d); pa["text_fields_todo"]+=len(st); pa["text_chars_todo"]+=sum(map(len,st)); pa["macro_$("]+=sum('$(' in x for x in st)
                        if pa_sample is None: pa_sample=[x for x in st if '$(' in x][:1]
                    except Exception: pa["bad"]+=1
            if n.endswith(".md") and re.match(r"assets/[^/]+/[^/]*guide[^/]*/",n):
                parts=n.split("/")
                if len(parts)>3 and parts[3].startswith("_"): gmdirs[parts[3]]+=1
                else:
                    gm["md_files"]+=1; gmchars+=z.getinfo(n).file_size
            if re.match(r"data/[^/]+/modonomicon/.+\.json$",n):
                mono["json"]+=1
                if mono_s is None and "/entries/" in n: mono_s=z.read(n).decode("utf-8","replace")[:240]
    print(" Patchouli",dict(pa),"books",len(books),dict(i18n),"sample",pa_sample)
    print(" GuideME",dict(gm),"KB",gmchars//1024,"lang dirs",gmdirs.most_common(8))
    print(" Modonomicon",dict(mono),repr(mono_s))
    # kubejs
    kj=os.path.join(b,"kubejs")
    if os.path.isdir(kj):
        en=glob.glob(os.path.join(kj,"assets","*","lang","en_us.json")); ru=glob.glob(os.path.join(kj,"assets","*","lang","ru_ru.json"))
        js=glob.glob(os.path.join(kj,"**","*.js"),recursive=True)
        lang_ev=sum('ClientEvents.lang' in open(p,errors="replace").read() for p in js)
        print(" KubeJS en_us",len(en),"ru_ru",len(ru),"js",len(js),"ClientEvents.lang scripts",lang_ev)
    # packs in zip / nested resourcepacks
    for sub in ("resourcepacks","config/openloader/resources","config/paxi/resourcepacks","global_packs","datapacks"):
        for z_ in glob.glob(os.path.join(b,sub,"*.zip")):
            try:
                z=zipfile.ZipFile(z_); l=[n for n in z.namelist() if re.search(r"lang/en_us\.json$",n)]; rr=[n for n in z.namelist() if re.search(r"lang/ru_ru\.json$",n)]
                print("  pack zip",os.path.basename(z_)[:40],"en_us",len(l),"ru_ru",len(rr))
            except Exception: pass
