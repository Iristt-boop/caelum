"""用像素素材拼一间**俯视角**的房间（room2 的背景）。

糖糖 2026-09-03：「我想要俯视图，第二个房间用星露谷那个像素让我看看效果。」

素材：sierrassets 的 8×8 地板墙面 + 3/4 视角家具（俯视地板 + 看得见正面的家具，
正是星露谷那种画法）。许可允许用在产品里，⚠️ 但不许把素材本身再分发 ——
所以它进的是私有仓库，且只作为合成后的背景图。

## 🔴 比例是这件事的成败

房间世界是 1600×1600，人物约 248px 高。8px 的砖原样贴上去只有一个指甲盖，
屋子会显得空得离谱。放大 8 倍：一块砖 64px、一张床约 240px ≈ 人的一个身位，
这才对得上。**倍数错了整间屋就废了**，先算再画。

## ⚠️ 墙的位置必须和碰撞图对齐

画完要按同样的墙厚重新生成 room-map.json。上游文档那句
「不要只换背景而继续使用不匹配的碰撞图」——代价是人走进墙里，
或者卡在看得见的空地上。
"""
from pathlib import Path

from PIL import Image

PACK = Path("D:/WorkBuddy/2026-09-02-17-20-41/pixel-assets/sierrassets_pack")
TILES = PACK / "floors and walls" / "individual sprites"
FURN = PACK / "furniture" / "individual sprites"
OUT = Path("D:/claude-code/caelum-room/web/room/assets/room-v2/game/background/room2-pixel.png")

WORLD = 1600
S = 8          # 放大倍数：8px 砖 → 64px 一格
CELL = 16      # 逻辑网格一格 16px（room-map 的约定）

#: 墙占多少逻辑格。⚠️ 改这里就要改 generate_room_map 的 --top-wall
TOP_WALL_CELLS = 12
SIDE_WALL_CELLS = 2
BOTTOM_WALL_CELLS = 2


def tile(n: int) -> Image.Image:
    im = Image.open(TILES / f"Slice {n}.png").convert("RGBA")
    return im.resize((im.width * S, im.height * S), Image.NEAREST)


def furn(n: int) -> Image.Image:
    im = Image.open(FURN / f"part-Slice {n}.png").convert("RGBA")
    return im.resize((im.width * S, im.height * S), Image.NEAREST)


room = Image.new("RGBA", (WORLD, WORLD), (0, 0, 0, 0))

top = TOP_WALL_CELLS * CELL
left = SIDE_WALL_CELLS * CELL
right = WORLD - SIDE_WALL_CELLS * CELL
bottom = WORLD - BOTTOM_WALL_CELLS * CELL

# ---- 地板：木色平铺（#9 是程序挑出来的「四边无描边」的纯砖）----
floor = tile(9)
for y in range(top, bottom, floor.height):
    for x in range(left, right, floor.width):
        room.paste(floor, (x, y), floor)

# ---- 上墙：灰白两道的墙面（152~154），铺满顶部 ----
wall = tile(153)
for y in range(0, top, wall.height):
    for x in range(0, WORLD, wall.width):
        room.paste(wall, (x, y), wall)

# ---- 左右和下沿：用深色描边收边，和素材的黑边同色 ----
edge = Image.new("RGBA", (left, WORLD), (43, 43, 43, 255))
room.paste(edge, (0, 0))
room.paste(edge, (right, 0))
room.paste(Image.new("RGBA", (WORLD, WORLD - bottom), (43, 43, 43, 255)), (0, bottom))

# ---- 家具。坐标按逻辑格给，乘 CELL 变像素 ----
#: (编号, 格子x, 格子y, 说明)。y 是**顶边**，家具自己往下长
PIECES = [
    # ⚠️ 编号从「编号画在图**上方**」的索引图读的。第一版索引画在下方，
    # 视觉上更贴近下一行，我整体读错一行 —— 拼出来床变成了电视。
    #
    # 坐标按**量出来的占格**排，不靠眼睛估：第一版全挤在左上角互相压着，
    # 下面 2/3 空着。房间 100x100 格，墙占上 12 / 左右 2 / 下 2。
    #   (编号, x格, y格, 占格 + 说明)
    (156, 6, 14, "16x15 双人床"),
    (7, 24, 20, "8x8  床头柜"),
    #: 猫窝改成**独立家具精灵**了（见 room2 的 initial-state），
    #  背景里不能再画一遍，否则同一个窝会有重影
    (24, 36, 13, "7x16 高柜"),
    (18, 46, 13, "8x16 穿衣镜"),
    (16, 60, 14, "16x7 书桌"),
    (3, 66, 22, "5x7  椅子"),
    (48, 82, 14, "8x8  电视"),
    (10, 82, 26, "8x8  斗柜"),
    (49, 30, 56, "16x8 沙发"),
    (13, 32, 68, "16x7 茶几"),
    (39, 54, 58, "8x13 红扶手椅"),
    (14, 16, 60, "4x6  盆栽"),
    (15, 84, 62, "4x7  盆栽"),
]

for n, gx, gy, _label in PIECES:
    im = furn(n)
    room.alpha_composite(im, (gx * CELL, gy * CELL))

OUT.parent.mkdir(parents=True, exist_ok=True)
room.save(OUT)
print(f"拼好了 {OUT.name} {room.size}")
print(f"墙：上 {TOP_WALL_CELLS} 格 / 左右 {SIDE_WALL_CELLS} / 下 {BOTTOM_WALL_CELLS}")
print("[注意] 记得用同样的墙厚重新生成 room-map.json")
