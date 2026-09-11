"""把**一整套户型**画成一张图 + 配套的碰撞网格。素材用 LimeZu Modern Interiors。

糖糖 2026-09-03：
  「不是一个页面一个 room，是整个户型在一起，把灯光控制风景放在这样的 house 就行。」
  「房间可以大一些，不需要对照现实 1:1，只要灯光位置清楚就可以。」
  「你可以不用放家具，我自己摆。你只需要把布局改好看就行。」

所以这个脚本**只画壳**：地板 + 墙 + 门。家具她自己在建造模式里摆。

## 素材与许可

LimeZu Modern Interiors 完整版（她 2026-09-03 买的）。
⚠️ 许可原文三条：可用于任何商业/非商业项目、可以改；
**不许转卖或分发素材本身**；**必须署名 limezu.itch.io**。
所以原包不进仓库，解出来的几张放在被 git 忽略的 local-assets/limezu/。

## 🔴 布局的唯一真源是 rooms.json

这里一行布局都不写。界面上的灯光光圈以后也读同一份 ——
抄成两份迟早漂移，那时的表现是「图上房间挪了，灯还亮在老地方」，
而画面看着挺正常，很难发现。

## ⚠️ 一格 = 16px 素材放大 2 倍

LimeZu 是 16×16 的图，我们的逻辑格也是 16px。1:1 会让整栋房子只有现在一半大、
人物也得跟着缩；放大 2 倍既保住尺度，颗粒感也更接近她给的参考图。
"""
from collections import deque
from pathlib import Path
import json

from PIL import Image, ImageDraw

ROOM = Path("D:/claude-code/caelum-room")
ART = ROOM / "local-assets/limezu"
BG_OUT = ROOM / "web/room/assets/room-v2/game/background/house.png"
MAP_OUT = ROOM / "web/room/data/scenes/house/room-map.json"

SPEC = json.loads((ROOM / "web/room/data/scenes/house/rooms.json").read_text(encoding="utf-8"))
GRID = SPEC["grid"]["width"]
CELL = SPEC["grid"]["cell"]
#: 🔴 一格贴一张，**不放大**。
#  第一版放大 2 倍却仍然每格都贴 —— 每张图盖住相邻格，纹理被拉大又互相覆盖，
#  连墙都被压掉了。要放大就得每 2 格贴一张，但房间坐标是奇数，对不齐；
#  1:1 最简单也最不会错。颗粒感不够的话改成整张图最后统一放大。
S = 1

#: 每间房的地板，坐标是 Room_Builder_Floors_16x16.png 里的 (列, 行)。
#: 从带坐标的挑选图上选的，不是数出来的。
FLOOR_BY_ROOM = {
    "living": (1, 16), "bedroom2": (1, 16), "study": (1, 16),
    "bedroom1": (1, 19),
    "music": (1, 30),
    "kitchen": (9, 12),
    "bath1": (9, 17),
    "tearoom": (5, 23),
    "gaming": (13, 20),
    "greenhouse": (9, 24),
}
#: 墙面：Room_Builder_Walls_16x16.png 的 (列, 行)。
#: 每种花色 2 行 × 10 列（col0/1/2 = 左中右）。col1 是中段，四面都用它。
WALL_TILE = (1, 5)

floors_img = Image.open(ART / "Room_Builder_Floors_16x16.png").convert("RGBA")
walls_img = Image.open(ART / "Room_Builder_Walls_16x16.png").convert("RGBA")


def src_tile(img, cx, cy):
    """取一格素材并放大。⚠️ 必须 NEAREST —— 像素图用别的插值会糊成一团。"""
    t = img.crop((cx * 16, cy * 16, cx * 16 + 16, cy * 16 + 16))
    return t.resize((16 * S, 16 * S), Image.NEAREST)


ROOMS = [(r["id"], r["name"], *r["rect"]) for r in SPEC["rooms"]]
DOORS = [tuple(d["rect"]) for d in SPEC["doors"]]

walls = [[1] * GRID for _ in range(GRID)]
house = Image.new("RGBA", (GRID * CELL, GRID * CELL), (0, 0, 0, 0))
draw = ImageDraw.Draw(house)

#: 🔴 墙用**圆角实心块**，不用 LimeZu 的墙贴图。
#
#  试过用它的墙面贴图（Room_Builder_Walls 的 col1）：那是**正视角的墙面**，
#  俯视图里贴出来只是一条浅灰细线，几乎看不见 —— 而糖糖恰恰要求
#  「房间之间要有明显隔断」。所以墙自己画：厚、深色、圆角。
#  地板则用 LimeZu 的，两者搭起来正好（她说好看的那版就是圆角厚墙）。
WALL_RGB = (58, 48, 44, 255)
WALL_PX = 14
RADIUS = 26


def rounded_mask(w, h, radius):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    return m


for rid, _name, x0, y0, x1, y1 in ROOMS:
    px0, py0 = x0 * CELL, y0 * CELL
    px1, py1 = (x1 + 1) * CELL - 1, (y1 + 1) * CELL - 1
    draw.rounded_rectangle([px0 - WALL_PX, py0 - WALL_PX, px1 + WALL_PX, py1 + WALL_PX],
                           radius=RADIUS + WALL_PX, fill=WALL_RGB)

    floor = src_tile(floors_img, *FLOOR_BY_ROOM.get(rid, (1, 16)))
    patch = Image.new("RGBA", (px1 - px0 + 1, py1 - py0 + 1), (0, 0, 0, 0))
    for gy in range(0, patch.height, floor.height):
        for gx in range(0, patch.width, floor.width):
            patch.paste(floor, (gx, gy), floor)
    patch.putalpha(rounded_mask(patch.width, patch.height, RADIUS))
    house.alpha_composite(patch, (px0, py0))

    for gy in range(y0, y1 + 1):
        for gx in range(x0, x1 + 1):
            walls[gy][gx] = 0

door_floor = src_tile(floors_img, 1, 16)
for dx0, dy0, dx1, dy1 in DOORS:
    for gy in range(dy0, dy1 + 1):
        for gx in range(dx0, dx1 + 1):
            walls[gy][gx] = 0
            house.paste(door_floor, (gx * CELL, gy * CELL), door_floor)

BG_OUT.parent.mkdir(parents=True, exist_ok=True)
house.save(BG_OUT)
print(f"户型拼好了 {BG_OUT.name} {house.size}｜{len(ROOMS)} 间｜可走格 {GRID*GRID - sum(map(sum, walls))}")

# ---- 连通性自检 ----
start = (ROOMS[0][2] + 2, ROOMS[0][3] + 2)
seen, q = {start}, deque([start])
while q:
    cx, cy = q.popleft()
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = cx + dx, cy + dy
        if 0 <= nx < GRID and 0 <= ny < GRID and not walls[ny][nx] and (nx, ny) not in seen:
            seen.add((nx, ny))
            q.append((nx, ny))
for _rid, name, x0, y0, _x1, _y1 in ROOMS:
    print(f"  {name:6s} {'可以走到' if (x0 + 1, y0 + 1) in seen else '走不到  <-- 门没开在公共墙上'}")

# 🔴 反向自检：屋外不能是可走的（第一版栽过 —— 只在房间四周画墙，
# 房间之外没有障碍，人可以走到虚空里，而连通性自检当时还说「全部可达」）
outside = any(not walls[0][gx] for gx in range(GRID)) or any(not walls[gy][0] for gy in range(GRID))
print("  屋外是墙:", "是" if not outside else "否  <-- 人能走出房子")


def layer(data, i, n):
    return {"data": data, "height": GRID, "id": i, "name": n, "opacity": 1,
            "type": "tilelayer", "visible": True, "width": GRID, "x": 0, "y": 0}


flat = [walls[i // GRID][i % GRID] for i in range(GRID * GRID)]
m = {"compressionlevel": -1, "height": GRID, "infinite": False,
     "layers": [layer([2] * (GRID * GRID), 1, "floor"), layer(flat, 2, "walls")],
     "nextlayerid": 3, "nextobjectid": 1, "orientation": "orthogonal", "renderorder": "right-down",
     "tiledversion": "1.11.0", "tileheight": CELL,
     "tilesets": [{"columns": 3, "firstgid": 1, "image": "room-placeholder-tiles.png",
                   "imageheight": CELL, "imagewidth": CELL * 3, "margin": 0,
                   "name": "room-placeholder-tiles", "spacing": 0, "tilecount": 3,
                   "tileheight": CELL, "tilewidth": CELL}],
     "tilewidth": CELL, "type": "map", "version": "1.10", "width": GRID}
MAP_OUT.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
print(f"碰撞图写了 {MAP_OUT.name}")
