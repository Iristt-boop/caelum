"""把房间里**当前的家具位置**固化回场景文件。

糖糖 2026-09-03 在建造模式里摆家具，摆完的位置只活在 `house.db` 里 ——
换台机器、或者哪天要重建库，她的活儿就没了。这个脚本把服务端此刻的布局
写回 `initial-state.json`，那份是进 git 的。

    python scratch/freeze-layout.py            # 固化 house（8878）
    python scratch/freeze-layout.py 8877 home  # 别的场景

## 🔴 为什么不直接读数据库

读 HTTP 接口拿到的是**服务端此刻认的状态**，和她在屏幕上看到的一定一致。
读库要自己解 state_json、还要处理「库里是按 id 索引的字典而接口返回数组」
这种差异（2026-09-03 我按数组写当场 TypeError）。少一层猜就少一处错。

## ⚠️ 只固化位置，不动别的

家具的贴图、footprint、blocking 这些是设计决定，来自导入脚本；
这里只把 `position` 和 `footprint` 的落点更新过去。
全量覆盖的话，她哪天在库里留下的临时字段会被一起写进 git。

## ⚠️ 写之前先备份

场景文件是唯一真源。写坏了要能退回去 —— 备份带时间戳放在同目录。
"""
import json
import shutil
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOM = Path("D:/claude-code/caelum-room")
port = sys.argv[1] if len(sys.argv) > 1 else "8878"
scene = sys.argv[2] if len(sys.argv) > 2 else "house"
STATE = ROOM / f"web/room/data/scenes/{scene}/initial-state.json"

with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/room/state", timeout=8) as r:
    live = json.load(r)

live_pos = {f["id"]: f for f in live.get("furniture", [])}
doc = json.loads(STATE.read_text(encoding="utf-8"))

moved, missing = [], []
for item in doc.get("furniture", []):
    cur = live_pos.get(item["id"])
    if cur is None:
        missing.append(item["id"])
        continue
    old = item.get("position", {})
    new = cur.get("position", {})
    if old.get("x") != new.get("x") or old.get("y") != new.get("y"):
        moved.append((item["id"], (old.get("x"), old.get("y")), (new.get("x"), new.get("y"))))
        item["position"] = {"x": new["x"], "y": new["y"]}
        #: footprint 是命中区，要跟着位置走，否则拖过之后抓不住它
        cell = 16
        fp = item.get("footprint")
        if fp:
            fp["x"], fp["y"] = new["x"] // cell, new["y"] // cell

#: 库里有、文件里没有的（比如以后从家具库现加的）——补进文件，否则固化会漏掉它们
known = {f["id"] for f in doc.get("furniture", [])}
added = []
for fid, f in live_pos.items():
    if fid in known:
        continue
    doc.setdefault("furniture", []).append(f)
    added.append(fid)

if not moved and not added and not missing:
    print("没有变化，什么都没写")
    raise SystemExit(0)

backup = STATE.with_name(f"initial-state.{datetime.now():%m%d-%H%M%S}.bak.json")
shutil.copy2(STATE, backup)
STATE.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

print(f"固化到 {STATE.name}（备份 {backup.name}）")
print(f"  挪过位置的 {len(moved)} 件：")
for fid, o, n in moved[:12]:
    print(f"    {fid:22s} {o} → {n}")
if added:
    print(f"  库里新增、补进文件的 {len(added)} 件: {added}")
if missing:
    #: ⚠️ 输出里**不许放 emoji**。这台机器的控制台是 GBK，编码不了 ⚠️，
    #  print 直接抛 UnicodeEncodeError —— 活干完了却以崩溃收场。
    #  今天已经是第三次（argparse 帮助、头宽自检、这里），emoji 只留在注释里。
    print(f"  [注意] 文件里有但库里没有的 {len(missing)} 件（可能被删过）: {missing}")
