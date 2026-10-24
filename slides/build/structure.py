"""Step 1: clone template slides into the talk order (31 slides), drop unused, write build/stage1.pptx."""
import os, re, shutil, subprocess, sys, zipfile
SK = sys.argv[1]  # pptx skill dir
HERE = os.path.dirname(os.path.abspath(__file__)); W = os.path.join(HERE, "work")
shutil.rmtree(W, ignore_errors=True)
zipfile.ZipFile(os.path.join(HERE, "..", "template", "scd-base.pptx")).extractall(W)
# talk order -> template source slide (1 cover dark, 3 speaker dark, 5 two-col dark, 6 two-col light, 7 img+text dark, 8 img+text light, 9 thanks dark)
ORDER = [1, 5, 3, 5, 8, 8, 6, 8, 8, 6, 6, 8, 8, 8, 6, 6, 6, 6, 8, 8, 6, 6, 5, 5, 5, 6, 6, 7, 6, 6, 5, 9]
used, new = {}, []
for src in ORDER:
    name = f"slide{src}.xml"
    if name not in used:
        used[name] = True; new.append(name); continue
    out = subprocess.run([sys.executable, os.path.join(SK, "scripts", "add_slide.py"), W, name], capture_output=True, text=True, check=True).stdout
    new.append(re.search(r"(slide\d+\.xml)", out.split("Created")[-1]).group(1))
# rebuild sldIdLst in talk order: map slide file -> rId via presentation.xml.rels
rels = open(os.path.join(W, "ppt/_rels/presentation.xml.rels")).read()
rid = {m.group(2): m.group(1) for m in re.finditer(r'Id="(rId\d+)"[^>]*Target="slides/(slide\d+\.xml)"', rels)}
pres = open(os.path.join(W, "ppt/presentation.xml")).read()
ids = "".join(f'<p:sldId id="{256 + i}" r:id="{rid[s]}"/>' for i, s in enumerate(new))
pres = re.sub(r"<p:sldIdLst>.*?</p:sldIdLst>", f"<p:sldIdLst>{ids}</p:sldIdLst>", pres, flags=re.S)
open(os.path.join(W, "ppt/presentation.xml"), "w").write(pres)
subprocess.run([sys.executable, os.path.join(SK, "scripts", "clean.py"), W], check=True, capture_output=True)
out = os.path.join(HERE, "stage1.pptx")
if os.path.exists(out): os.remove(out)
subprocess.run(f'cd "{W}" && zip -qXr "{out}" .', shell=True, check=True)
print("order:", new)
