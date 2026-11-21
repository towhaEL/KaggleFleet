"""Record the README demo: run the real kfleet CLI against fake_kaggle.py.

Usage: python scripts/media/demo_capture.py WORK_DIR   (writes WORK_DIR/scenes.json)
No network, no real Kaggle account. Accounts alice/bob/chen are placeholders.
"""
import json, os, shutil, subprocess, sys, pathlib
HERE = pathlib.Path(__file__).resolve().parent; REPO = HERE.parents[1]
S = pathlib.Path(sys.argv[1]).resolve(); S.mkdir(parents=True, exist_ok=True); proj = S/"vision-paper"
shutil.rmtree(S/"state", ignore_errors=True); shutil.rmtree(proj, ignore_errors=True)
(S/"state").mkdir(); (proj/"notebooks").mkdir(parents=True)
def nb(name, title, code):
    cells=[{"cell_type":"markdown","metadata":{},"source":[f"# {title}"]},
           {"cell_type":"code","metadata":{},"execution_count":None,"outputs":[],"source":[code]}]
    (proj/"notebooks"/name).write_text(json.dumps({"cells":cells,"metadata":{},"nbformat":4,"nbformat_minor":5}))
nb("01_data_prep.ipynb","Data preparation","import pandas as pd")
nb("02_train.ipynb","Train ViT baseline","import torch")
nb("03_eval.ipynb","Evaluate and plot","import matplotlib")
env = dict(os.environ, KAGGLE_FLEET_DIR=str(S/"state/fleet"), KAGGLE_FLEET_KAGGLE_BIN=str(HERE/"fake_kaggle.py"),
           DEMO_STATE=str(S/"state"), PYTHONPATH=str(REPO/"src"), COLUMNS="110")
def kf(*a):
    p = subprocess.run([sys.executable,"-m","kaggle_fleet",*a],cwd=proj,env=env,capture_output=True,text=True)
    return (p.stdout+p.stderr).replace(str(proj),"~/vision-paper").strip("\n")
kf("account","add","--username","alice","--key","0"*32,"--label","Alice")
kf("account","add","--username","bob","--key","1"*32,"--label","Bob")
kf("account","add","--username","chen","--key","2"*32,"--label","Chen")
scenes = []
def scene(cmd, *a, comment=None, clear=False):
    scenes.append({"cmd": cmd, "out": kf(*a), "comment": comment, "clear": clear})
scene("kfleet account list", "account","list")
scene("kfleet notebook list", "notebook","list")
scene("kfleet run push 02_train.ipynb --account bob", "run","push","02_train.ipynb","--account","bob", clear=True)
scene("kfleet run status", "run","status")
scene("kfleet run logs", "run","logs", comment="# ...a few minutes later")
scene("kfleet run status", "run","status")
scene("kfleet run download", "run","download", clear=True)
scenes.append({"cmd": "ls outputs/nb-02-train", "comment": None, "clear": False,
               "out": "  ".join(sorted(p.name for p in (proj/"outputs/nb-02-train").iterdir()))})
json.dump(scenes, open(S/"scenes.json","w"), indent=1)
for s in scenes: print("$", s["cmd"]); print(s["out"]); print()
