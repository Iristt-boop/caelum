"""把 LimeZu 的家具导进房子，做成**能拖的独立精灵**。

糖糖 2026-09-03：「你可以不用放家具，我自己摆」→「家具做成能拖动的能自己放的」
→ 选了方案 A：我批量导入并预先摆好，她在建造模式里拖到满意。

## 它做三件事

1. 从 local-assets 里把选中的家具导出成贴图（放进仓库，作为产品的一部分）
2. 注册到 furniture-manifest.json 的 textures
3. 在 house 的 initial-state.json 里生成家具条目，按房间排好

## ⚠️ 三条容易错的

· **贴图放仓库、原始素材包不放**。许可允许前者（产品的一部分），
  禁止后者（分发素材本身）。署名写在 THIRD_PARTY_NOTICES。
· **blocking 一律 false**。设成 true 的话，家具会占住碰撞格 ——
  摆得不巧就能把人堵在房间里，而且**不会有任何报错**，只是走不过去。
  等她摆定了再逐件决定谁该挡路。
· **footprint 必须和贴图大小对得上**。它是拖拽时的命中区，
  写小了抓不住、写大了会挡住旁边那件。

## 自检

导完会验：每张贴图文件在不在、每件家具是不是落在它那间房的矩形内。
不验的话「摆进去了」只是一句声称 —— 家具跑到墙里看图才发现，
而那时已经摆了几十件。
"""
import json
import math
import os
import re
from pathlib import Path

from PIL import Image

ROOM = Path("D:/claude-code/caelum-room")
SINGLES = ROOM / "local-assets/limezu/singles"
GENERIC = ROOM / "local-assets/limezu/1_Generic_16x16.png"
OUT_DIR = ROOM / "web/room/assets/room-v2/game/furniture-limezu"
MANIFEST = ROOM / "web/room/assets/room-v2/config/furniture-manifest.json"
STATE = ROOM / "web/room/data/scenes/house/initial-state.json"
ROOMS_JSON = ROOM / "web/room/data/scenes/house/rooms.json"

CELL = 16
S = 2                       # 家具放大 2 倍，和房子的尺度配

#: 要导什么。(键, 来源, 定位, 中文名, 放哪间房)
#: 来源 "s" = singles 单图（定位是 "主题_编号"）
#: 来源 "g" = Generic 图集（定位是 "列,行,宽,高"，单位是格）
ITEMS = [
    # ---- 大客厅 ----
    ("sofa_bench",   "s", "Living_Room_Singles_3",   "长凳",   "living"),
    ("tv_console",   "g", "0,30,6,2",                "电视柜", "living"),
    ("rug_red",      "g", "2,22,2,2",                "红地毯", "living"),
    ("floor_lamp",   "s", "Living_Room_Singles_79",  "落地灯", "living"),
    ("plant_big",    "s", "Living_Room_Singles_18",  "大绿植", "living"),
    ("fireplace",    "s", "Living_Room_Singles_107", "壁炉",   "living"),
    ("console_deco", "s", "Living_Room_Singles_52",  "边柜",   "living"),

    # ---- 一楼卧室（主卧）----
    ("bed_double",   "s", "Bedroom_Singles_217",     "双人床", "bedroom1"),
    ("dresser",      "s", "Bedroom_Singles_397",     "梳妆台", "bedroom1"),
    ("wardrobe",     "s", "Bedroom_Singles_517",     "衣柜",   "bedroom1"),
    ("bed_lamp",     "s", "Living_Room_Singles_73",  "台灯",   "bedroom1"),

    # ---- 厨房 ----
    ("fridge",       "s", "Kitchen_Singles_289",     "冰箱",   "kitchen"),
    ("coffee",       "s", "Kitchen_Singles_262",     "咖啡机", "kitchen"),
    ("kitchen_cab",  "s", "Kitchen_Singles_343",     "橱柜",   "kitchen"),

    # ---- 二楼卧室 ----
    ("bed_single",   "s", "Bedroom_Singles_25",      "单人床", "bedroom2"),
    ("night_stand",  "s", "Living_Room_Singles_45",  "床头柜", "bedroom2"),
    ("mirror_stand", "s", "Living_Room_Singles_91",  "穿衣镜", "bedroom2"),

    # ---- 书房 ----
    ("bookcase",     "s", "Living_Room_Singles_37",  "书柜",   "study"),
    ("study_lamp",   "s", "Living_Room_Singles_81",  "落地灯", "study"),

    # ---- 音乐房 / 电竞房 / 茶室 / 花房 ----
    ("piano_ish",    "s", "Living_Room_Singles_1",   "立柜",   "music"),
    ("music_plant",  "s", "Living_Room_Singles_16",  "绿植",   "music"),
    ("gaming_desk",  "g", "0,22,4,2",                "长柜",   "gaming"),
    ("tea_table",    "s", "Living_Room_Singles_57",  "矮几",   "tearoom"),
    ("tea_plant",    "s", "Living_Room_Singles_15",  "小盆栽", "tearoom"),
    ("green_tree",   "g", "12,28,2,2",               "小树",   "greenhouse"),
    ("green_palm",   "g", "14,26,2,2",               "棕榈",   "greenhouse"),
]


def load_sprite(kind, locator):
    if kind == "s":
        im = Image.open(SINGLES / f"{locator}.png").convert("RGBA")
    else:
        cx, cy, w, h = (int(v) for v in locator.split(","))
        im = Image.open(GENERIC).convert("RGBA").crop(
            (cx * CELL, cy * CELL, (cx + w) * CELL, (cy + h) * CELL))
    bb = im.getchannel("A").getbbox()
    if bb is None:
        raise SystemExit(f"{locator} 是空图")
    im = im.crop(bb)
    #: ⚠️ 必须 NEAREST。像素图用别的插值会糊，而糊了和「贴图错了」看着很像
    return im.resize((im.width * S, im.height * S), Image.NEAREST)


spec = json.loads(ROOMS_JSON.read_text(encoding="utf-8"))
rect_of = {r["id"]: r["rect"] for r in spec["rooms"]}

OUT_DIR.mkdir(parents=True, exist_ok=True)
manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
state = json.loads(STATE.read_text(encoding="utf-8"))

#: 只保留原来那件地毯之外的，重跑时不叠加
state["furniture"] = [f for f in state["furniture"] if not f["id"].startswith("lz_")]

cursor = {}          # 每间房排到哪儿了
placed, textures = [], {}

for key, kind, locator, cn, room in ITEMS:
    if room not in rect_of:
        raise SystemExit(f"{key} 指到了不存在的房间 {room}")
    im = load_sprite(kind, locator)
    im.save(OUT_DIR / f"{key}.png")
    tex = f"lz-{key.replace('_', '-')}"
    textures[tex] = f"room/assets/room-v2/game/furniture-limezu/{key}.png"

    w_cells = max(1, math.ceil(im.width / CELL))
    h_cells = max(1, math.ceil(im.height / CELL))
    x0, y0, x1, y1 = rect_of[room]

    #: 沿房间上沿从左往右排，排满一行换下一行。她之后自己拖，这只是初始位置
    cx, cy, rowh = cursor.get(room, (x0 + 2, y0 + 2, 0))
    if cx + w_cells > x1 - 1:
        cx, cy, rowh = x0 + 2, cy + rowh + 2, 0
    pos = (cx, cy)
    cursor[room] = (cx + w_cells + 2, cy, max(rowh, h_cells))

    placed.append({
        "id": f"lz_{key}", "type": "furniture", "asset": key, "interactiveLayer": "back",
        "layers": [{"id": "back", "texture": tex, "role": "floor"}],
        "position": {"x": pos[0] * CELL, "y": pos[1] * CELL},
        "footprint": {"x": pos[0], "y": pos[1], "width": w_cells, "height": h_cells},
        #: 🔴 一律 false。true 会占住碰撞格，摆得不巧能把人堵死，而且不报错
        "blocking": False, "layer": "floor", "pointerPassthrough": False,
        "interactions": [], "_名字": cn, "_房间": room,
    })

manifest["textures"].update(textures)
MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")
state["furniture"].extend(placed)
STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8", newline="\n")

print(f"导入 {len(placed)} 件家具，注册 {len(textures)} 张贴图")

# ---- 自检 ----
bad = []
for f in placed:
    p = ROOM / "web" / textures[f["layers"][0]["texture"]]
    if not p.exists():
        bad.append(f"{f['id']} 贴图不存在")
    x0, y0, x1, y1 = rect_of[f["_房间"]]
    fp = f["footprint"]
    if not (x0 <= fp["x"] and fp["x"] + fp["width"] - 1 <= x1
            and y0 <= fp["y"] and fp["y"] + fp["height"] - 1 <= y1):
        bad.append(f"{f['id']} 超出了 {f['_房间']} 的范围 {fp}")
print("自检:", "全部落在各自房间内、贴图齐全" if not bad else bad)
