"""从 LimeZu 的角色表切出我们要的 18 帧。

糖糖 2026-09-03：「他的人物我的不是不合适吗，素材包里有我俩的人物。」
—— 对：手绘风的小人放进像素房子里会打架，所以两个人都换成 LimeZu 的像素角色。

## 表是怎么排的（看图认出来的，不是猜的）

角色 16×32，一行一种动作：

    第0行  静止四方向  4 帧    0=右 1=上(背面) 2=左 3=下(正面)
    第1行  起步/收步    24 帧   ⚠️ 不是走路循环！六帧里四张几乎等于站立
    第2行  走路        24 帧   每 6 帧一个方向，顺序同上 —— **用这行**
    第3行  睡觉        13 帧
    第4行  坐          12 帧

⚠️ 左右两张互为镜像，几何上分不出哪张朝哪边 —— 第一版我推成了
「0=左 2=右」，糖糖 2026-09-03 实测走起来是反的，已对调成「0=右 2=左」。
以后换别的角色表**先走两步验方向**，别照抄这里的顺序。

## ⚠️ read / work / rest 目前是占位

LimeZu 没有现成的「看书 / 打字 / 靠着」三个姿势（坐和睡是有的）。
先用坐姿和静止顶着，先看整体效果；以后从别的行里找更合适的，或用生成器补。
"""
from pathlib import Path

from PIL import Image

CW, CH = 16, 32
SRC = Path("D:/claude-code/caelum-room/local-assets/limezu/chars")
OUT = Path("D:/claude-code/scratch/limezu-frames")

#: 方向 → 静止帧的列号 / 走路帧的起始列号
IDLE_COL = {"right": 0, "up": 1, "left": 2, "down": 3}
WALK_COL = {"right": 0, "up": 6, "left": 12, "down": 18}


def cut(im, row, col):
    return im.crop((col * CW, row * CH, col * CW + CW, row * CH + CH))


def build(src_png, name):
    im = Image.open(src_png).convert("RGBA")
    base = OUT / name
    for sub in ("idle", "walk", "actions"):
        (base / sub).mkdir(parents=True, exist_ok=True)

    for d, c in IDLE_COL.items():
        cut(im, 0, c).save(base / "idle" / f"{d}.png")
    for d, c0 in WALK_COL.items():
        #: 🔴 走路取**第 2 行**的第 0、4 帧，不是第 1 行。
        #
        #  糖糖 2026-09-03 说「走路像平移」，量出来是两回事叠在一起：
        #    · 第一版取第 1 行的 1、4 帧 —— 和静止只差 0 和 4 个像素
        #    · 第二版改成第 1 行的 2、3 帧 —— 彼此只差 19 像素（同一只脚）
        #  真相是**第 1 行根本不是走路循环**：六帧和静止的差是
        #  0,0,138,155,4,2（站站迈迈站站）。第 2 行才是：
        #  199,51,194,197,50,195，一左一右在交替。
        #  取第 2 行彼此差别最大的 0 和 4 帧（187 像素）。
        #  ⚠️ 换角色表要重新量这两个数，别照抄。
        for i, off in enumerate((0, 4)):
            cut(im, 2, c0 + off).save(base / "walk" / f"{d}-{i}.png")

    sit = cut(im, 4, 4)          # 正面坐
    sleep = cut(im, 3, 0)        # 躺
    idle_down = cut(im, 0, IDLE_COL["down"])
    sit.save(base / "actions" / "sit.png")
    sit.save(base / "actions" / "read.png")      # 占位
    sit.save(base / "actions" / "work-0.png")    # 占位
    cut(im, 4, 5).save(base / "actions" / "work-1.png")  # 占位：坐姿的另一帧
    sleep.save(base / "actions" / "sleep.png")
    idle_down.save(base / "actions" / "rest.png")  # 占位

    n = sum(1 for _ in base.rglob("*.png"))
    print(f"{name}: 切了 {n} 张 —— {'齐了' if n == 18 else '不对，应该 18 张'}")


#: 糖糖 2026-09-03 用 Character Generator 2.0 捏的我俩。
#: ⚠️ 生成器导出的表是 896x640（20 行），预设是 896x656 —— 行数少半行，
#: 但前 20 行的排布一致，所以同一套列号能用。换别的表**先验方向和行号**。
build(SRC / "iris-gen.png", "iris")
build(SRC / "nox-gen.png", "nox")
