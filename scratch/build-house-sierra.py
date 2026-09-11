"""把**一整套户型**画成一张图 + 配套的碰撞网格。

糖糖 2026-09-03：
  「不是一个页面一个 room，是整个户型在一起，把灯光控制风景放在这样的 house 就行。」
  「房间可以大一些，毕竟电子 room 不需要对照现实 1:1，只要灯光位置清楚就可以。」

所以不照搬她家十个房间，只留**真正挂着设备的四间**（主卧/客厅/电竞房/厨房）
加卫生间和阳台，六间，每间都大。灯亮在哪一眼看得清，比户型准确重要。

## 🔴 房子之外必须是墙

第一版只在每间房四周画墙，房间之间是空白 —— 而空白**没有碰撞**，
人可以走到屋外的虚空里。连通性自检当时说「全部可达」，
那是个**正确但没意义的答案**：它把虚空也算成了路。
现在反过来：整张图默认全是墙，再从里面挖出房间和门。

## ⚠️ 墙、碰撞、门都出自同一份 ROOMS

分开写两份迟早漂移，那时的表现是「看得见的门走不过去」。
门开在**相邻两间的公共墙**上，不朝空地开。

## 🔴 比例：家具 4 倍

一间房的时候 8 倍正好；整套户型 8 倍会让一张床占掉半间屋。
人物身高也要跟着降到 ~120px —— **两边一起改**，只改一边就是比例失衡。
"""
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw

PACK = Path("D:/WorkBuddy/2026-09-02-17-20-41/pixel-assets/sierrassets_pack")
TILES = PACK / "floors and walls" / "individual sprites"
FURN = PACK / "furniture" / "individual sprites"
ROOM = Path("D:/claude-code/caelum-room")
BG_OUT = ROOM / "web/room/assets/room-v2/game/background/house.png"
MAP_OUT = ROOM / "web/room/data/scenes/house/room-map.json"

# 🔴 布局的唯一真源是那份 json，这里不再写第二遍。
#
# 界面上的灯光光圈也读同一份 —— 抄成两份迟早漂移，
# 那时的表现是「图上房间挪了，灯还亮在老地方」，而画面看着挺正常。
import json

SPEC = json.loads((ROOM / "web/room/data/scenes/house/rooms.json").read_text(encoding="utf-8"))
GRID = SPEC["grid"]["width"]
CELL = SPEC["grid"]["cell"]
S = SPEC["scale"]
ROOMS = [(r["name"], *r["rect"], r["floor"]) for r in SPEC["rooms"]]
DOORS = [tuple(d["rect"]) for d in SPEC["doors"]]
FURNITURE = {r["name"]: [tuple(f) for f in r.get("furniture", [])] for r in SPEC["rooms"]}

WALL_RGB = (58, 48, 44, 255)


def tile(n):
    im = Image.open(TILES / f"Slice {n}.png").convert("RGBA")
    return im.resize((im.width * S, im.height * S), Image.NEAREST)


def furn(n):
    im = Image.open(FURN / f"part-Slice {n}.png").convert("RGBA")
    return im.resize((im.width * S, im.height * S), Image.NEAREST)


# 🔴 屋外是**透明**，不是墙色。
#
# 第一版把整张图铺满墙色，结果：房间之间没有间隙、楼层之间也看不出断开，
# 整栋楼糊成一块（糖糖 2026-09-03：「你的墙跟墙是挨着的，分不出来二三楼」）。
# 参考图那种画法是：每间房自己一圈厚墙，房子外面空着 ——
# 层与层之间那三行空档于是变成看得见的断口。
#
# ⚠️ 透明只是**画面**上的空。碰撞照旧：不是地板也不是门的格子一律挡路。
walls = [[1] * GRID for _ in range(GRID)]
house = Image.new("RGBA", (GRID * CELL, GRID * CELL), (0, 0, 0, 0))
draw = ImageDraw.Draw(house)

#: 墙厚（像素）和圆角半径。糖糖要的就是这个圆角 ——
#: 直角看着像方块拼的，圆角才像画出来的房子
WALL_PX = 14
RADIUS = 26


def rounded_mask(box, radius):
    """给地板用的圆角遮罩 —— 地板也要跟着圆，不然墙圆了地板还是方的，
    四个角会露出直角的地板尖。"""
    m = Image.new("L", (box[2] - box[0], box[3] - box[1]), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, m.width - 1, m.height - 1], radius=radius, fill=255)
    return m


for name, x0, y0, x1, y1, tnum in ROOMS:
    px0, py0 = x0 * CELL, y0 * CELL
    px1, py1 = (x1 + 1) * CELL - 1, (y1 + 1) * CELL - 1

    # 外圈：圆角的实心墙块
    draw.rounded_rectangle([px0 - WALL_PX, py0 - WALL_PX, px1 + WALL_PX, py1 + WALL_PX],
                           radius=RADIUS + WALL_PX, fill=WALL_RGB)

    # 内部：平铺地板，再用圆角遮罩裁一次
    floor = tile(tnum)
    patch = Image.new("RGBA", (px1 - px0 + 1, py1 - py0 + 1), (0, 0, 0, 0))
    for gy in range(0, patch.height, floor.height):
        for gx in range(0, patch.width, floor.width):
            patch.paste(floor, (gx, gy), floor)
    patch.putalpha(rounded_mask([px0, py0, px1 + 1, py1 + 1], RADIUS))
    house.alpha_composite(patch, (px0, py0))

    for gy in range(y0, y1 + 1):
        for gx in range(x0, x1 + 1):
            walls[gy][gx] = 0

# 门：把两间房之间那段墙铺成地板，人才走得过去
for dx0, dy0, dx1, dy1 in DOORS:
    door_tile = tile(9)
    for gy in range(dy0, dy1 + 1):
        for gx in range(dx0, dx1 + 1):
            walls[gy][gx] = 0
            house.paste(door_tile, (gx * CELL, gy * CELL), door_tile)

for name, x0, y0, _x1, _y1, _t in ROOMS:
    for n, ox, oy in FURNITURE.get(name, []):
        im = furn(n)
        house.alpha_composite(im, ((x0 + ox) * CELL, (y0 + oy) * CELL))

BG_OUT.parent.mkdir(parents=True, exist_ok=True)
house.save(BG_OUT)
print(f"户型拼好了 {BG_OUT.name} {house.size}｜{len(ROOMS)} 间｜可走格 {GRID*GRID - sum(map(sum, walls))}")

# ---- 连通性自检 ----
start = (ROOMS[0][1] + 2, ROOMS[0][2] + 2)
seen, q = {start}, deque([start])
while q:
    cx, cy = q.popleft()
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = cx + dx, cy + dy
        if 0 <= nx < GRID and 0 <= ny < GRID and not walls[ny][nx] and (nx, ny) not in seen:
            seen.add((nx, ny))
            q.append((nx, ny))
ok = True
for name, x0, y0, _x1, _y1, _t in ROOMS:
    hit = (x0 + 1, y0 + 1) in seen
    ok &= hit
    print(f"  {name:5s} {'可以走到' if hit else '走不到  <-- 门没开在公共墙上'}")

# 🔴 反向自检：屋外不能是可走的（第一版就栽在这儿）
outside_walkable = any(not walls[0][gx] for gx in range(GRID)) or any(not walls[gy][0] for gy in range(GRID))
print("  屋外是墙:", "是" if not outside_walkable else "否  <-- 人能走出房子")

# ---- 碰撞图（Tiled 格式，和引擎约定一致）----
import json

layer = lambda data: {"data": data, "height": GRID, "id": 1, "name": "x", "opacity": 1,
                      "type": "tilelayer", "visible": True, "width": GRID, "x": 0, "y": 0}
flat_floor = [2] * (GRID * GRID)
flat_walls = [walls[i // GRID][i % GRID] for i in range(GRID * GRID)]
m = {"compressionlevel": -1, "height": GRID, "infinite": False,
     "layers": [dict(layer(flat_floor), id=1, name="floor"), dict(layer(flat_walls), id=2, name="walls")],
     "nextlayerid": 3, "nextobjectid": 1, "orientation": "orthogonal", "renderorder": "right-down",
     "tiledversion": "1.11.0", "tileheight": CELL,
     "tilesets": [{"columns": 3, "firstgid": 1, "image": "room-placeholder-tiles.png",
                   "imageheight": CELL, "imagewidth": CELL * 3, "margin": 0,
                   "name": "room-placeholder-tiles", "spacing": 0, "tilecount": 3,
                   "tileheight": CELL, "tilewidth": CELL}],
     "tilewidth": CELL, "type": "map", "version": "1.10", "width": GRID}
MAP_OUT.parent.mkdir(parents=True, exist_ok=True)
MAP_OUT.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
print(f"碰撞图写了 {MAP_OUT}")
