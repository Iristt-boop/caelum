"""生成**家具库目录**：导出贴图 + 注册纹理 + 写目录文件。

糖糖 2026-09-04：「B 方案家居库做了吧，我发现你自己挑家具确实不太固定」
—— 对。我挑的那 26 件里有三件切歪、还漏了沙发电视。让她自己挑才是正解。

## 做法

把 LimeZu 的单图按主题整批导进来（不再是我手挑的几件），
每张登记成一条目录项：贴图键、显示名、占几格、属于哪类。
前端的家具库面板读这份目录，点一下就往房间里放一件。

## ⚠️ 数量和启动开销

纹理是在开局时**全量预加载**的，所以目录不能无限大。
每张 PNG 一两 KB，两三百张在本机可以接受；上千张就该改成按需加载了。
所以这里每个主题限量，并且**跳过太小的碎件**（1 格以下多是装饰点缀，
在这个尺度上看不清，反而把目录撑满）。

## ⚠️ 名字只能给到「主题 + 编号」

LimeZu 的单图文件名是 `Bedroom_Singles_217` 这种，没有语义。
硬猜「这是床那是柜」必然错（我已经错过一轮）。所以目录里显示的是
缩略图 + 主题 + 编号 —— **让眼睛认，不让我猜**。
"""
import json
import math
import re
from pathlib import Path

from PIL import Image

ROOM = Path("D:/claude-code/caelum-room")
SINGLES = ROOM / "local-assets/limezu/singles"
OUT_DIR = ROOM / "web/room/assets/room-v2/game/furniture-limezu"
MANIFEST = ROOM / "web/room/assets/room-v2/config/furniture-manifest.json"
CATALOG = ROOM / "web/room/data/furniture-catalog.json"

CELL, S = 16, 2

#: 主题 → 中文类别名 + 最多导多少件
THEMES = {
    "Living_Room_Singles": ("客厅", 122),
    "Bedroom_Singles": ("卧室", 90),
    "Kitchen_Singles": ("厨房", 90),
    "Bathroom_Singles": ("浴室", 80),
    "Classroom_and_Library_Singles": ("书房", 75),
    "Music_and_Sport_Singles": ("音乐", 80),
    "Japanese_Interiors_Singles": ("茶室", 80),
}

#: 太小的不收 —— 放大后不足这么多像素的多是碎装饰，看不清还占目录
MIN_PX = 20 * 20

OUT_DIR.mkdir(parents=True, exist_ok=True)
manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
catalog = []
skipped = 0

for theme, (label, limit) in THEMES.items():
    files = sorted(SINGLES.glob(f"{theme}_*.png"),
                   key=lambda p: int(re.findall(r"(\d+)\.png$", p.name)[0]))
    taken = 0
    for f in files:
        if taken >= limit:
            break
        im = Image.open(f).convert("RGBA")
        bb = im.getchannel("A").getbbox()
        if bb is None:
            continue
        im = im.crop(bb)
        if im.width * im.height * S * S < MIN_PX:
            skipped += 1
            continue
        #: ⚠️ 必须 NEAREST，像素图别的插值会糊
        im = im.resize((im.width * S, im.height * S), Image.NEAREST)
        num = re.findall(r"(\d+)\.png$", f.name)[0]
        key = f"{theme.lower().replace('_singles', '')}_{num}"
        im.save(OUT_DIR / f"{key}.png")
        tex = f"lz-{key.replace('_', '-')}"
        manifest["textures"][tex] = f"room/assets/room-v2/game/furniture-limezu/{key}.png"
        catalog.append({
            "key": key, "texture": tex, "category": label,
            "name": f"{label} {num}",
            "w": max(1, math.ceil(im.width / CELL)),
            "h": max(1, math.ceil(im.height / CELL)),
        })
        taken += 1
    print(f"  {label:4s} 导入 {taken} 件")

MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")
CATALOG.write_text(json.dumps({
    "_说明": "家具库目录。前端的面板读它；贴图键必须在 furniture-manifest 里注册过。"
             "由 scratch/build-catalog.py 生成，别手改。",
    "cell": CELL, "items": catalog,
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

print(f"\n目录 {len(catalog)} 件（跳过太小的 {skipped} 件），纹理共 {len(manifest['textures'])} 个")

# ---- 自检：目录里的每个纹理键都要能在清单里找到，且文件真的存在 ----
bad = [c["key"] for c in catalog
       if c["texture"] not in manifest["textures"]
       or not (ROOM / "web" / manifest["textures"][c["texture"]]).exists()]
print("自检:", "目录和贴图对得上" if not bad else f"对不上 {bad[:5]}")
