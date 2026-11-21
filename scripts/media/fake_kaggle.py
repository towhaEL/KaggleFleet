#!/usr/bin/env python3
"""Stand-in for the `kaggle` CLI, used only to record the README demo."""
import json, os, sys, pathlib
a = sys.argv[1:]
cfg = pathlib.Path(os.environ.get("KAGGLE_CONFIG_DIR", "."))
user = "alice"
try: user = json.loads((cfg/"kaggle.json").read_text())["username"]
except Exception: pass
st = pathlib.Path(os.environ["DEMO_STATE"]) / "status_calls"
if a[:1] == ["--version"]: print("Kaggle CLI 1.7.4.5"); sys.exit()
if a[:2] == ["config", "view"]: print(f"Configuration values from {cfg}\n- username: {user}"); sys.exit()
if a[:2] == ["kernels", "list"]: print("ref  title"); sys.exit()
if a[:2] == ["kernels", "push"]:
    meta = json.loads((pathlib.Path(a[3])/"kernel-metadata.json").read_text())
    print(f"Kernel version 1 successfully pushed.  Please check progress at https://www.kaggle.com/code/{meta['id']}"); sys.exit()
if a[:2] == ["kernels", "status"]:
    n = int(st.read_text()) if st.exists() else 0; st.write_text(str(n+1))
    s = "KernelWorkerStatus.RUNNING" if n < 1 else "KernelWorkerStatus.COMPLETE"
    print(f'{a[2]} has status "{s}"'); sys.exit()
if a[:2] == ["kernels", "logs"]:
    print("[  3.1s] Using GPU: Tesla T4 (15 GB)\n[ 41.8s] epoch 1/3  loss 0.912  val_acc 0.712\n[ 80.4s] epoch 2/3  loss 0.534  val_acc 0.801\n[119.6s] epoch 3/3  loss 0.371  val_acc 0.846\n[121.0s] saved model.pt, metrics.json"); sys.exit()
if a[:2] == ["kernels", "output"]:
    d = pathlib.Path(a[a.index("-p")+1]); d.mkdir(parents=True, exist_ok=True)
    (d/"model.pt").write_bytes(b"0"*1024); (d/"metrics.json").write_text('{"val_acc": 0.846}')
    print(f"Output file downloaded to {d}/model.pt\nOutput file downloaded to {d}/metrics.json"); sys.exit()
print("unsupported", a, file=sys.stderr); sys.exit(1)
